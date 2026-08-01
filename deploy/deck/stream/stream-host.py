#!/usr/bin/env python3
"""Host side of the bespoke stream: instB's export socket out to the joiner, pad input back.

This is the only process in the project that accepts a connection from the LAN. It carries video,
audio and pad input and nothing else — the emulator's own control surface stays on loopback, and
the emulator itself never listens on a routable address. Keep it that way.

  instB --(unix socket, raw frames + s16 audio)--> stream-host --(bare h264 over TCP)--> joiner
                                                              --(raw s16 PCM over TCP)--> joiner
  joiner --(pad state over TCP)--> stream-host --(pad overrides over loopback HTTP)--> instB

Video and audio travel on separate connections with no container between them. A container carries
a clock, and every symptom the joiner had - stutter, crackle, input that felt detached - came from
players trying to lock onto that clock rather than simply playing what arrived.

Pixels are handed to ffmpeg untouched: the PS1's 16bpp VRAM word is exactly ffmpeg's bgr555le, and
its 24bpp mode is rgb24, so nothing here converts a pixel. That matters because the Deck has no
numpy and no compiler.

Two things follow from the PS1 changing display mode as it runs (FMV at 320x240 24bpp, menus at
512x240 16bpp, some screens 640x480):

  * ffmpeg scales every mode to one fixed output size, so the joiner sees a stream whose resolution
    never changes and a picture that fills its screen rather than sitting in a corner.
  * this process owns the TCP socket rather than letting ffmpeg listen, so rebuilding the encoder
    for a new input geometry does not drop the joiner's connection. mpegts is resumable, so the
    joiner sees a brief discontinuity instead of a disconnect.

Nothing here may stall the emulator. The export socket drops frames on its own when this process
reads slowly, which is the designed backpressure; the reader thread is never blocked on a write.
"""

import argparse
import os
import queue
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HELLO_FMT = "<IHHIHHHHI"
PACKET_FMT = "<IBBHHHIQ"
HELLO_SIZE = struct.calcsize(HELLO_FMT)
PACKET_SIZE = struct.calcsize(PACKET_FMT)
HELLO_MAGIC = 0x534C3150
PACKET_MAGIC = 0x4B503150
VIDEO, AUDIO = 1, 2
BGR555, RGB888 = 0, 1

# ffmpeg's names for what the emulator already produces, so no pixels are touched in transit.
PIX_FMT = {BGR555: "bgr555le", RGB888: "rgb24"}

running = True


def log(message):
    print(f"[stream-host] {message}", flush=True)


def read_exactly(sock, count):
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


class Client:
    """The joiner's video connection, owned here rather than by ffmpeg.

    Holding the socket at this level is what lets the encoder be rebuilt without the joiner
    noticing: the byte stream continues, only its contents restart.
    """

    def __init__(self):
        self.sock = None
        self.lock = threading.Lock()
        self.connections = 0

    def attach(self, sock):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = sock
            self.connections += 1

    def detach(self):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None

    def connected(self):
        return self.sock is not None

    def send(self, data):
        # A blocking sendall here would hold the lock for as long as the peer refuses to read, and
        # a joiner that quits leaves exactly that: a socket whose buffer fills and never drains.
        # The next connection then blocks in attach() waiting for this lock, so quitting the joiner
        # made it impossible to rejoin. A send timeout bounds the stall and drops the dead peer.
        with self.lock:
            if self.sock is None:
                return
            try:
                self.sock.sendall(data)
            except (OSError, socket.timeout) as error:
                log(f"joiner video connection ended: {error}")
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None


class AudioServer:
    """Raw PCM straight to the joiner on its own connection.

    Audio deliberately does not share a container with the video. Putting them together means a
    container with a clock, and then both ends spend their time negotiating a timeline instead of
    playing: players reported "no reference clock" and "PCR called too late", and no amount of
    muxer tuning fixed it. Sent raw, the joiner's sound card paces the audio and the picture is
    shown on arrival — neither waits for the other.

    44.1 kHz stereo s16 is ~1.4 Mbit/s, which is nothing on a LAN, so there is no reason to encode
    it and no decoder needed at the far end.
    """

    def __init__(self, args):
        self.args = args
        self.sock = None
        self.lock = threading.Lock()
        self.connections = 0
        self.dropped = 0

    def serve(self):
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((self.args.bind, self.args.audio_port))
        listener.listen(1)
        listener.settimeout(1.0)
        log(f"audio listening on {self.args.bind}:{self.args.audio_port}")
        while running:
            try:
                conn, addr = listener.accept()
            except socket.timeout:
                continue
            except OSError:
                return
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            conn.settimeout(3.0)
            log(f"joiner audio connected from {addr[0]}")
            with self.lock:
                if self.sock is not None:
                    try:
                        self.sock.close()
                    except OSError:
                        pass
                self.sock = conn
                self.connections += 1

    def send(self, payload):
        with self.lock:
            if self.sock is None:
                return
            try:
                self.sock.sendall(payload)
            except (OSError, socket.timeout) as error:
                log(f"joiner audio connection ended: {error}")
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None
                self.dropped += 1


class Encoder:
    """One ffmpeg process, serving one input geometry.

    A rawvideo input's geometry is fixed when it opens, so a display-mode change rebuilds this.
    The *output* geometry never changes, which is what keeps that rebuild cheap for the joiner.
    """

    generation = 0
    # When the first encoder started. Every later one offsets its output timestamps by how long ago
    # that was, so a rebuild continues the timeline instead of restarting it at zero. The joiner
    # holds one TCP connection across rebuilds, and a decoder shown time running backwards drops
    # everything it has and flashes.
    epoch = None

    def __init__(self, width, height, fmt, args, client):
        if Encoder.epoch is None:
            Encoder.epoch = time.monotonic()
        self.ts_offset = time.monotonic() - Encoder.epoch

        self.width, self.height, self.fmt = width, height, fmt
        self.client = client
        self.active = True
        self.dropped_video = 0
        self.dropped_audio = 0

        Encoder.generation += 1

        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-thread_queue_size", "64",
            "-f", "rawvideo", "-pix_fmt", PIX_FMT[fmt], "-s", f"{width}x{height}",
            "-r", str(args.fps), "-i", "pipe:0",
            # Encode at the console's own resolution and let the joiner's player scale to its
            # screen. Upscaling here cost 2.3x the pixels for no extra detail, and those bits are
            # exactly what the encoder was running out of.
            #
            # Quality-targeted rather than a fixed bitrate: a hard 8 Mbit cap starved the encoder
            # during FMV, so quality collapsed across each second and snapped back at every
            # keyframe - a 1 Hz pulse, seen as flashing and as the picture "losing quality".
            # veryfast rather than ultrafast because the host has the headroom (the encoder used
            # under a fifth of one core) and it buys a lot of quality per bit.
            "-c:v", args.vcodec, "-preset", "veryfast", "-tune", "zerolatency",
            "-crf", str(args.crf), "-maxrate", args.bitrate, "-bufsize", args.bufsize,
            # Intra-refresh was tried here to remove the once-a-second keyframe burst. It made the
            # input lag measurably worse in play and did not touch the flashing, which turned out to
            # be black frames coming out of the emulator. Reverted; do not re-add it without first
            # measuring latency with and without.
            "-g", str(args.fps), "-pix_fmt", "yuv420p",
            # The console's 512x240 is not square-pixel: it is meant to fill a 4:3 screen. Encoding
            # at native size is right, but the display aspect has to be declared or the player
            # stretches the coded shape across the panel. Declaring it costs nothing; upscaling to
            # 640x480 to imply it cost 2.3x the pixels.
            "-aspect", args.aspect,
            # A bare H.264 elementary stream: no container, so no clock to negotiate and nothing
            # for the player to wait on. Frames are decoded and shown as they arrive, which is the
            # whole point when the picture is being played rather than watched.
            "-bsf:v", "h264_mp4toannexb", "-f", "h264", "pipe:1",
        ]
        log(f"encoder up: {width}x{height} {PIX_FMT[fmt]} -> {args.out_width}x{args.out_height} {args.vcodec}")
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        self.video_q = queue.Queue(maxsize=3)
        self.threads = [
            threading.Thread(target=self._feed_video, daemon=True),
            threading.Thread(target=self._forward, daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def _feed_video(self):
        while running and self.proc.poll() is None:
            try:
                payload = self.video_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.proc.stdin.write(payload)
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                return

    def _forward(self):
        """Encoded bytes to the joiner, forwarded the moment ffmpeg produces them.

        Reads the raw pipe, never a buffered wrapper: a buffered read(n) waits for n bytes, and at
        a static menu's bitrate 16 KB is *seconds* of encoded output — all of it sitting here as
        input lag while the joiner watches a stale frame. os.read returns whatever the pipe holds.

        Reads ffmpeg even with nobody attached, or it would block. Stops sending the moment this
        encoder is superseded. A replaced encoder keeps draining for a little while as it shuts
        down, and two muxers interleaving into one socket produce a stream that decodes to nothing.
        """
        fd = self.proc.stdout.fileno()
        while running and self.proc.poll() is None:
            chunk = os.read(fd, 65536)
            if not chunk:
                return
            if self.active:
                self.client.send(chunk)

    def matches(self, width, height, fmt):
        return (width, height, fmt) == (self.width, self.height, self.fmt)

    def push_video(self, payload):
        try:
            self.video_q.put_nowait(payload)
        except queue.Full:
            self.dropped_video += 1

    def alive(self):
        return self.proc.poll() is None

    def close(self):
        """Returns immediately; the process teardown finishes on its own thread.

        Waiting here would be waiting on the reader thread, and every millisecond spent not reading
        the export socket is a frame the emulator throws away. A display-mode change must cost the
        reader nothing.
        """

        self.active = False

        def teardown():
            for closer in (lambda: self.proc.stdin.close(),):
                try:
                    closer()
                except Exception:
                    pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()

        threading.Thread(target=teardown, daemon=True).start()


class PadBridge:
    """Applies the joiner's pad state to a headless instance.

    A windowless instance has no GLFW and therefore no pad of its own, so the joiner's buttons
    arrive as overrides through the emulator's Lua handler on loopback. State is sent only when it
    changes: a button held for a second is one message, not sixty.
    """

    def __init__(self, control_port, handler):
        self.url = f"http://127.0.0.1:{control_port}/api/v1/lua/{handler}"
        self.previous = None
        self.applied = 0
        self.failures = 0

    def apply(self, buttons):
        if buttons == self.previous:
            return
        self.previous = buttons
        try:
            urllib.request.urlopen(self.url + f"?buttons={buttons}", timeout=0.5).read()
            self.applied += 1
        except (urllib.error.URLError, OSError) as error:
            self.failures += 1
            if self.failures in (1, 10, 100) or self.failures % 1000 == 0:
                log(f"pad bridge failed {self.failures}x (latest: {error})")


def serve_video(args, client):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind((args.bind, args.port))
    listener.listen(1)
    listener.settimeout(1.0)
    log(f"video listening on {args.bind}:{args.port}")
    while running:
        try:
            conn, addr = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        conn.settimeout(3.0)
        log(f"joiner video connected from {addr[0]}")
        client.attach(conn)


def serve_input(args, bridge):
    """Accepts the joiner's pad connection. Takes button bitmasks and nothing else."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.bind, args.input_port))
    listener.listen(1)
    listener.settimeout(1.0)
    log(f"pad input listening on {args.bind}:{args.input_port}")
    while running:
        try:
            conn, addr = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        log(f"joiner pad connected from {addr[0]}")
        conn.settimeout(2.0)
        with conn:
            while running:
                try:
                    data = read_exactly(conn, 4)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if data is None:
                    break
                bridge.apply(struct.unpack("<I", data)[0])
        log("joiner pad disconnected")
        bridge.apply(0)  # nothing pressed, or a button held at disconnect outlives the joiner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-socket", default=None, help="the emulator's -stream socket")
    parser.add_argument("--bind", default="0.0.0.0", help="LAN address to serve the stream on")
    parser.add_argument("--port", type=int, default=6690)
    parser.add_argument("--input-port", type=int, default=6691)
    parser.add_argument("--audio-port", type=int, default=6692)
    parser.add_argument("--control-port", type=int, default=6681, help="instB's loopback HTTP surface")
    parser.add_argument("--pad-handler", default="padstate", help="Lua handler applying the pad state")
    parser.add_argument("--vcodec", default="libx264")
    parser.add_argument("--bitrate", default="12M", help="ceiling, not a target")
    parser.add_argument("--bufsize", default="2M")
    parser.add_argument("--crf", type=int, default=20)
    parser.add_argument("--aspect", default="4:3")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--out-width", type=int, default=640)
    parser.add_argument("--out-height", type=int, default=480)
    parser.add_argument("--audio-rate", type=int, default=44100)
    parser.add_argument("--runtime-dir", default=os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    parser.add_argument("--stats-interval", type=float, default=5.0)
    args = parser.parse_args()

    if args.export_socket is None:
        args.export_socket = os.path.join(args.runtime_dir, "pcsx-redux-stream.sock")

    global running

    def stop(signum, frame):
        global running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    client = Client()
    audio = AudioServer(args)
    bridge = PadBridge(args.control_port, args.pad_handler)
    threading.Thread(target=serve_video, args=(args, client), daemon=True).start()
    threading.Thread(target=audio.serve, daemon=True).start()
    threading.Thread(target=serve_input, args=(args, bridge), daemon=True).start()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    for attempt in range(60):
        try:
            sock.connect(args.export_socket)
            break
        except OSError:
            if attempt == 0:
                log(f"waiting for {args.export_socket}")
            time.sleep(1.0)
    else:
        sys.exit(f"never saw the export socket at {args.export_socket}")

    hello = read_exactly(sock, HELLO_SIZE)
    if hello is None or struct.unpack(HELLO_FMT, hello)[0] != HELLO_MAGIC:
        sys.exit("the export socket did not speak the stream protocol")
    log(f"attached to {args.export_socket}")

    encoder = None
    frames = audio_packets = 0
    age_samples = []
    last_stats = time.monotonic()

    while running:
        header = read_exactly(sock, PACKET_SIZE)
        if header is None:
            log("the emulator closed the export socket")
            break
        magic, ptype, fmt, width, height, aux, payload_len, timestamp = struct.unpack(PACKET_FMT, header)
        if magic != PACKET_MAGIC:
            log("desynchronised from the export stream")
            break
        payload = read_exactly(sock, payload_len) if payload_len else b""
        if payload is None:
            break

        if ptype == VIDEO:
            # The emulator stamps frames from steady_clock and this runs on the same machine, so
            # the two clocks are the same CLOCK_MONOTONIC and the difference is real: how old a
            # frame already is before any encoding happens. It bounds how much of the lag can
            # possibly be upstream of ffmpeg.
            age_us = int(time.monotonic() * 1_000_000) - timestamp
            age_samples.append(age_us)
            # No joiner, no encoder. Encoding for nobody would spend a Deck's headroom on frames
            # that go straight in the bin, and this host is already running two emulators.
            if encoder and not client.connected():
                log("joiner gone — stopping the encoder")
                encoder.close()
                encoder = None
            if encoder and not encoder.alive():
                log("ffmpeg exited — rebuilding")
                encoder.close()
                encoder = None
            if encoder and not encoder.matches(width, height, fmt):
                log(f"display mode changed to {width}x{height} fmt={fmt}")
                encoder.close()
                encoder = None
            if encoder is None and client.connected():
                encoder = Encoder(width, height, fmt, args, client)
            if encoder:
                encoder.push_video(payload)
            frames += 1
        elif ptype == AUDIO:
            audio.send(payload)
            audio_packets += 1

        now = time.monotonic()
        if now - last_stats >= args.stats_interval:
            elapsed = now - last_stats
            ages = sorted(age_samples) or [0]
            log(f"frame age at host: median {ages[len(ages)//2]/1000:.1f}ms "
                f"p99 {ages[int(len(ages)*0.99)]/1000:.1f}ms max {ages[-1]/1000:.1f}ms")
            age_samples = []
            log(f"in {frames / elapsed:.1f} fps, {audio_packets / elapsed:.0f} audio pkt/s | "
                f"joiner={'yes' if client.connected() else 'no'} conns={client.connections} "
                f"audio-conns={audio.connections} | "
                f"encoder drops v={encoder.dropped_video if encoder else 0} | "
                f"pad applied={bridge.applied} failed={bridge.failures}")
            frames = audio_packets = 0
            last_stats = now

    if encoder:
        encoder.close()
    client.detach()
    sock.close()
    log("stopped")


if __name__ == "__main__":
    main()
