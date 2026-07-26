// A player for a live stream, which is a different job from playing a file.
//
// ffplay treats what arrives on the socket as content with a timeline: it builds a queue and paces
// it against a clock, so the picture you see is whatever that clock says is due rather than what
// the host just drew. On a link-cable game that queue is pure input lag.
//
// This has no clock and no queue. Every time round the loop it takes everything the socket has,
// decodes all of it, and draws only the newest frame. Latency is therefore bounded by one frame
// however far behind it falls: a burst of arrivals costs discarded decodes, never a backlog to play
// out, and a slow start cannot turn into permanent lag.
//
// That last point is why this owns the socket instead of reading decoded frames from an ffmpeg
// pipe, which was the first attempt. With ffmpeg in front, the backlog collects in ffmpeg's own
// socket buffer where nothing downstream can skip it, and a one-second startup delay stayed one
// second behind for the rest of the session - measured at 59 frames in flight, flat.
//
// Audio and pad input are deliberately elsewhere: separate connections in stream-join.sh and
// pad-forward.py. A container with a shared clock is what this stream spent two sessions removing.

#include <SDL2/SDL.h>
#include <fcntl.h>
#include <libavcodec/avcodec.h>
#include <netdb.h>
#include <netinet/in.h>
#include <netinet/tcp.h>
#include <poll.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define READ_CHUNK 65536


// Finds the start of the next access unit at or after `from`, or -1. An access unit begins at a
// start code introducing a parameter set, an access-unit delimiter, or the first slice of a picture
// - and the first slice is the one whose first_mb_in_slice is zero, which as a ue(v) is the single
// bit 1, so it shows up as the top bit of the byte after the NAL header.
static long next_au_start(const uint8_t *buf, size_t len, size_t from) {
    for (size_t i = from; i + 6 <= len; i++) {
        if (buf[i] || buf[i + 1] || buf[i + 2] != 0 || buf[i + 3] != 1) {
            if (!(buf[i] == 0 && buf[i + 1] == 0 && buf[i + 2] == 0 && buf[i + 3] == 1)) continue;
        }
        unsigned nal = buf[i + 4] & 0x1F;
        if (nal == 7 || nal == 8 || nal == 9) return (long)i;
        if ((nal == 1 || nal == 5) && (buf[i + 5] & 0x80)) return (long)i;
    }
    return -1;
}

static int connect_to(const char *host, const char *port) {
    struct addrinfo hints, *res, *p;
    memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    if (getaddrinfo(host, port, &hints, &res) != 0) return -1;

    int fd = -1;
    for (p = res; p; p = p->ai_next) {
        fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd < 0) continue;
        if (connect(fd, p->ai_addr, p->ai_addrlen) == 0) break;
        close(fd);
        fd = -1;
    }
    freeaddrinfo(res);
    if (fd >= 0) {
        int one = 1;
        setsockopt(fd, IPPROTO_TCP, TCP_NODELAY, &one, sizeof(one));
    }
    return fd;
}

// Accepts "tcp://host:port" as well as "host port", so one binary serves both the launcher and the
// measurement harness.
static bool parse_target(int argc, char **argv, char *host, size_t host_len, char *port,
                         size_t port_len) {
    if (argc == 3) {
        snprintf(host, host_len, "%s", argv[1]);
        snprintf(port, port_len, "%s", argv[2]);
        return true;
    }
    if (argc == 2) {
        const char *s = argv[1];
        if (strncmp(s, "tcp://", 6) == 0) s += 6;
        const char *colon = strrchr(s, ':');
        if (!colon) return false;
        size_t n = (size_t)(colon - s);
        if (n >= host_len) return false;
        memcpy(host, s, n);
        host[n] = 0;
        snprintf(port, port_len, "%s", colon + 1);
        return true;
    }
    return false;
}

int main(int argc, char **argv) {
    char host[256], port[32];
    if (!parse_target(argc, argv, host, sizeof(host), port, sizeof(port))) {
        fprintf(stderr, "usage: %s tcp://host:port | %s host port\n", argv[0], argv[0]);
        return 2;
    }

    const bool fullscreen = getenv("PLAYER_WINDOWED") == NULL;
    const bool vsync = getenv("PLAYER_NO_VSYNC") == NULL;

    const AVCodec *codec = avcodec_find_decoder(AV_CODEC_ID_H264);
    if (!codec) {
        fprintf(stderr, "[player] no h264 decoder\n");
        return 1;
    }
    AVCodecContext *ctx = avcodec_alloc_context3(codec);
    // Nothing here reorders: the encoder emits no B-frames. Without this a stream whose SPS omits
    // the bitstream restriction buys a multi-frame delay purely from the level's default.
    ctx->flags |= AV_CODEC_FLAG_LOW_DELAY;
    ctx->flags2 |= AV_CODEC_FLAG2_FAST;
    ctx->thread_count = 1;
    if (avcodec_open2(ctx, codec, NULL) < 0) {
        fprintf(stderr, "[player] cannot open decoder\n");
        return 1;
    }
    AVCodecParserContext *parser = av_parser_init(AV_CODEC_ID_H264);
    if (!parser) {
        fprintf(stderr, "[player] cannot init parser\n");
        return 1;
    }

    fprintf(stderr, "[player] connecting to %s:%s\n", host, port);
    int fd = connect_to(host, port);
    if (fd < 0) {
        fprintf(stderr, "[player] cannot connect to %s:%s\n", host, port);
        return 1;
    }

    if (SDL_Init(SDL_INIT_VIDEO) != 0) {
        fprintf(stderr, "[player] SDL_Init: %s\n", SDL_GetError());
        return 1;
    }
    SDL_ShowCursor(SDL_DISABLE);
    SDL_Window *window =
        SDL_CreateWindow("PS1 LAN Link", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 1280, 800,
                         fullscreen ? SDL_WINDOW_FULLSCREEN_DESKTOP : SDL_WINDOW_SHOWN);
    if (!window) {
        fprintf(stderr, "[player] SDL_CreateWindow: %s\n", SDL_GetError());
        return 1;
    }
    SDL_Renderer *renderer = SDL_CreateRenderer(
        window, -1, SDL_RENDERER_ACCELERATED | (vsync ? SDL_RENDERER_PRESENTVSYNC : 0));
    if (!renderer) renderer = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
    if (!renderer) {
        fprintf(stderr, "[player] SDL_CreateRenderer: %s\n", SDL_GetError());
        return 1;
    }
    // The console's pixels are not square; the picture is 4:3 whatever the window is.
    SDL_RenderSetLogicalSize(renderer, 640, 480);

    // Which renderer SDL actually chose is load-bearing, not cosmetic: falling back to the software
    // one means every frame is colour-converted and upscaled on the CPU, which is enough on its own
    // to stop the player keeping up with a 60 fps source.
    SDL_RendererInfo info;
    if (SDL_GetRendererInfo(renderer, &info) == 0) {
        fprintf(stderr, "[player] renderer=%s accelerated=%d vsync=%d\n", info.name,
                (info.flags & SDL_RENDERER_ACCELERATED) != 0,
                (info.flags & SDL_RENDERER_PRESENTVSYNC) != 0);
    }

    SDL_Texture *texture = NULL;
    int tex_w = 0, tex_h = 0;

    AVPacket *packet = av_packet_alloc();
    // Two frames, not one. avcodec_receive_frame() unrefs its target before it does anything else,
    // so the call that ends the drain loop by returning EAGAIN wipes the frame the previous call
    // just produced. Whatever is going to be drawn has to be moved out of the way first.
    AVFrame *frame = av_frame_alloc();
    AVFrame *newest = av_frame_alloc();
    uint8_t *chunk = malloc(READ_CHUNK);
    uint8_t *pending = NULL;
    size_t pending_len = 0;

    unsigned long long decoded = 0, presented = 0;
    unsigned long long us_decode = 0, us_present = 0;
    unsigned long long bytes_in = 0, iterations = 0;
    const Uint64 perf_hz = SDL_GetPerformanceFrequency();
    Uint32 last_report = SDL_GetTicks();
    bool running = true;

    while (running) {
        SDL_Event event;
        while (SDL_PollEvent(&event)) {
            if (event.type == SDL_QUIT) running = false;
            if (event.type == SDL_KEYDOWN &&
                (event.key.keysym.sym == SDLK_ESCAPE || event.key.keysym.sym == SDLK_q))
                running = false;
        }
        if (!running) break;

        // Block for the first bytes, then take everything else already waiting. Draining the whole
        // socket each pass is what stops a backlog forming anywhere upstream of the decoder.
        bool more = true;
        bool have_frame = false;
        bool first = true;
        while (more) {
            if (!first) {
                struct pollfd p = {fd, POLLIN, 0};
                if (poll(&p, 1, 0) <= 0 || !(p.revents & POLLIN)) break;
            }
            ssize_t got = read(fd, chunk, READ_CHUNK);
            if (got <= 0) {
                fprintf(stderr, "[player] stream ended\n");
                running = false;
                break;
            }
            first = false;
            bytes_in += (size_t)got;

            // The parser reads ahead of the buffer it is given, so it must have zeroed padding
            // after the data; without it, access-unit boundaries are misdetected and frames are
            // silently lost rather than decoded.
            pending = realloc(pending, pending_len + (size_t)got + AV_INPUT_BUFFER_PADDING_SIZE);
            memcpy(pending + pending_len, chunk, (size_t)got);
            pending_len += (size_t)got;
            memset(pending + pending_len, 0, AV_INPUT_BUFFER_PADDING_SIZE);

            Uint64 t_decode0 = SDL_GetPerformanceCounter();
            // Access units are split here rather than by av_parser, which buffers according to
            // how the input happens to be chunked: fed the same stream in one burst it emitted
            // every frame, but fed it one frame per read - which is exactly what a live 60 fps
            // source does - it emitted one in three.
            size_t consumed = 0;
            for (;;) {
                long first = next_au_start(pending, pending_len, consumed);
                if (first < 0) break;
                long second = next_au_start(pending, pending_len, (size_t)first + 4);
                if (second < 0) break;  // the unit is not complete yet
                packet->data = pending + first;
                packet->size = (int)(second - first);
                for (;;) {
                    int sent_rc = avcodec_send_packet(ctx, packet);
                    while (avcodec_receive_frame(ctx, frame) == 0) {
                        decoded++;
                        av_frame_unref(newest);
                        av_frame_move_ref(newest, frame);
                        have_frame = true;
                    }
                    if (sent_rc != AVERROR(EAGAIN)) break;
                }
                consumed = (size_t)second;
            }
            memmove(pending, pending + consumed, pending_len - consumed);
            pending_len -= consumed;
            us_decode += (SDL_GetPerformanceCounter() - t_decode0) * 1000000 / perf_hz;
        }
        iterations++;
        if (!running) break;
        if (!have_frame) continue;

        if (newest->width <= 0 || newest->height <= 0) continue;
        if (!texture || tex_w != newest->width || tex_h != newest->height) {
            if (texture) SDL_DestroyTexture(texture);
            texture = SDL_CreateTexture(renderer, SDL_PIXELFORMAT_IYUV,
                                        SDL_TEXTUREACCESS_STREAMING, newest->width, newest->height);
            tex_w = newest->width;
            tex_h = newest->height;
            fprintf(stderr, "[player] stream is %dx%d\n", tex_w, tex_h);
        }
        Uint64 t_present0 = SDL_GetPerformanceCounter();
        SDL_UpdateYUVTexture(texture, NULL, newest->data[0], newest->linesize[0], newest->data[1],
                             newest->linesize[1], newest->data[2], newest->linesize[2]);
        SDL_RenderClear(renderer);
        SDL_RenderCopy(renderer, texture, NULL, NULL);
        SDL_RenderPresent(renderer);
        us_present += (SDL_GetPerformanceCounter() - t_present0) * 1000000 / perf_hz;
        presented++;

        // Frames in flight - what the sender has sent minus this count - is the only latency figure
        // this stack can measure honestly. A Deck's gamescope display captures black, and timing a
        // player's exit measures its window teardown as much as its buffer.
        Uint32 now = SDL_GetTicks();
        if (now - last_report >= 250) {
            fprintf(stderr,
                    "SHOWN %llu %llu decode_ms=%llu present_ms=%llu bytes=%llu iters=%llu\n",
                    presented, decoded, us_decode / 1000, us_present / 1000, bytes_in, iterations);
            fflush(stderr);
            last_report = now;
        }
    }

    fprintf(stderr, "[player] %llu decoded, %llu shown, %llu skipped as stale\n", decoded,
            presented, decoded - presented);
    if (texture) SDL_DestroyTexture(texture);
    SDL_DestroyRenderer(renderer);
    SDL_DestroyWindow(window);
    SDL_Quit();
    av_frame_free(&frame);
    av_frame_free(&newest);
    av_packet_free(&packet);
    av_parser_close(parser);
    avcodec_free_context(&ctx);
    free(chunk);
    free(pending);
    close(fd);
    return 0;
}
