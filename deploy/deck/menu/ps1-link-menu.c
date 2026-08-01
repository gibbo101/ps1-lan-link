// The unified app's front door: pick a role and a game with a controller, then get out of the way.
//
// This binary decides nothing about how a session runs. It prints one line to stdout — the payload
// of whatever entry was chosen — and exits; the launcher script turns that into the existing host
// or join path. Keeping the menu dumb means the tested session plumbing stays exactly as it is.
//
//   ps1-link-menu "Label|HOST /path/to/game.cue" "!Doom (link hangs)|" ...
//
// Each argument is one host-screen entry: display label, '|', the payload printed on selection.
// A leading '!' marks a dead entry - shown, not selectable (a game whose link protocol the
// emulation does not satisfy is a fact worth displaying, not hiding).
//
// The join screen needs no arguments: it listens for the host beacon (stream-host.py broadcasts
// one JSON datagram a second on UDP 6693) and lists what it hears. Selecting a host prints
// "JOIN <ip>". A host that stops announcing fades from the list after a few seconds.
//
// Exit codes: 0 = a line was printed; 1 = the player backed out; 2 = environment failure.
//
// MENU_WINDOWED=1 runs in a window for desktop work. MENU_AUTOPILOT="down,enter" feeds synthetic
// input one action per 100 ms so the logic is testable on a machine with no display at all.

#include <SDL2/SDL.h>
#include <SDL2/SDL_ttf.h>
#include <arpa/inet.h>
#include <fcntl.h>
#include <netinet/in.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <time.h>
#include <unistd.h>

#define MAX_ENTRIES 32
#define MAX_HOSTS 8
#define BEACON_PORT 6693
#define HOST_TTL_MS 4000

typedef struct {
    char label[128];
    char payload[256];
    bool disabled;
} Entry;

typedef struct {
    char ip[64];
    char name[64];
    char game[96];
    Uint32 last_seen;
} Host;

typedef enum { SCREEN_MAIN, SCREEN_HOST, SCREEN_JOIN } Screen;

// ---- beacon listener -----------------------------------------------------------------------

static int beacon_open(void) {
    int fd = socket(AF_INET, SOCK_DGRAM, 0);
    if (fd < 0) return -1;
    int one = 1;
    setsockopt(fd, SOL_SOCKET, SO_REUSEADDR, &one, sizeof(one));
    struct sockaddr_in addr = {0};
    addr.sin_family = AF_INET;
    addr.sin_addr.s_addr = INADDR_ANY;
    addr.sin_port = htons(BEACON_PORT);
    if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0) {
        close(fd);
        return -1;
    }
    fcntl(fd, F_SETFL, O_NONBLOCK);
    return fd;
}

// Pulls "key":"value" out of the beacon's flat JSON. The payload is produced by our own
// stream-host, so this does not need to be a JSON parser - just resilient to field order.
static bool json_field(const char *json, const char *key, char *out, size_t out_len) {
    char pattern[64];
    snprintf(pattern, sizeof(pattern), "\"%s\"", key);
    const char *p = strstr(json, pattern);
    if (!p) return false;
    p = strchr(p + strlen(pattern), ':');
    if (!p) return false;
    p++;
    while (*p == ' ') p++;
    if (*p != '"') return false;
    p++;
    size_t n = 0;
    while (*p && *p != '"' && n + 1 < out_len) out[n++] = *p++;
    out[n] = 0;
    return true;
}

static void beacon_poll(int fd, Host *hosts, int *n_hosts) {
    if (fd < 0) return;
    char buf[512];
    struct sockaddr_in from;
    socklen_t from_len = sizeof(from);
    ssize_t got;
    while ((got = recvfrom(fd, buf, sizeof(buf) - 1, 0, (struct sockaddr *)&from, &from_len)) > 0) {
        buf[got] = 0;
        from_len = sizeof(from);
        if (!strstr(buf, "\"ps1lanlink\"")) continue;
        char ip[64], name[64] = "?", game[96] = "?";
        inet_ntop(AF_INET, &from.sin_addr, ip, sizeof(ip));
        json_field(buf, "name", name, sizeof(name));
        json_field(buf, "game", game, sizeof(game));
        Uint32 now = SDL_GetTicks();
        for (int i = 0; i < *n_hosts; i++) {
            if (strcmp(hosts[i].ip, ip) == 0) {
                snprintf(hosts[i].name, sizeof(hosts[i].name), "%s", name);
                snprintf(hosts[i].game, sizeof(hosts[i].game), "%s", game);
                hosts[i].last_seen = now;
                goto next_datagram;
            }
        }
        if (*n_hosts < MAX_HOSTS) {
            Host *h = &hosts[(*n_hosts)++];
            snprintf(h->ip, sizeof(h->ip), "%s", ip);
            snprintf(h->name, sizeof(h->name), "%s", name);
            snprintf(h->game, sizeof(h->game), "%s", game);
            h->last_seen = now;
        }
    next_datagram:;
    }
    Uint32 now = SDL_GetTicks();
    for (int i = 0; i < *n_hosts;) {
        if (now - hosts[i].last_seen > HOST_TTL_MS) {
            hosts[i] = hosts[*n_hosts - 1];
            (*n_hosts)--;
        } else {
            i++;
        }
    }
}

// ---- rendering -----------------------------------------------------------------------------

static TTF_Font *font_open(int size) {
    const char *candidates[] = {
        "/usr/share/fonts/noto/NotoSansMono-Regular.ttf",           // SteamOS
        "/usr/share/fonts/TTF/DejaVuSansMono.ttf",                  // Arch
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf",      // Debian/Ubuntu
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        NULL,
    };
    for (int i = 0; candidates[i]; i++) {
        TTF_Font *f = TTF_OpenFont(candidates[i], size);
        if (f) return f;
    }
    return NULL;
}

static void draw_text(SDL_Renderer *r, TTF_Font *font, const char *text, int x, int y,
                      SDL_Color color) {
    if (!text[0]) return;
    SDL_Surface *s = TTF_RenderUTF8_Blended(font, text, color);
    if (!s) return;
    SDL_Texture *t = SDL_CreateTextureFromSurface(r, s);
    if (t) {
        SDL_Rect dst = {x, y, s->w, s->h};
        SDL_RenderCopy(r, t, NULL, &dst);
        SDL_DestroyTexture(t);
    }
    SDL_FreeSurface(s);
}

// ---- input ---------------------------------------------------------------------------------

typedef enum { ACT_NONE, ACT_UP, ACT_DOWN, ACT_SELECT, ACT_BACK } Action;

// Joystick instance ids that are open as game controllers. A mapped controller reports every
// press twice - once per API - so the raw joystick fallback must ignore these or one press
// becomes two.
static SDL_JoystickID g_mapped[16];
static int g_n_mapped = 0;

static bool is_mapped(SDL_JoystickID id) {
    for (int i = 0; i < g_n_mapped; i++)
        if (g_mapped[i] == id) return true;
    return false;
}

// Opens index i by whichever API understands it, and says so - which API a pad landed on is the
// first question when input is dead, so it is never left unsaid.
static void open_pad(int i) {
    if (SDL_IsGameController(i)) {
        SDL_GameController *gc = SDL_GameControllerOpen(i);
        if (gc && g_n_mapped < 16) {
            g_mapped[g_n_mapped++] = SDL_JoystickInstanceID(SDL_GameControllerGetJoystick(gc));
            fprintf(stderr, "[menu] pad %d: %s (gamecontroller)\n", i, SDL_GameControllerName(gc));
        }
    } else {
        SDL_Joystick *js = SDL_JoystickOpen(i);
        if (js) fprintf(stderr, "[menu] pad %d: %s (raw joystick fallback)\n", i, SDL_JoystickName(js));
    }
}

static Action translate(const SDL_Event *e) {
    if (e->type == SDL_QUIT) return ACT_BACK;
    if (e->type == SDL_KEYDOWN) {
        switch (e->key.keysym.sym) {
            case SDLK_UP: return ACT_UP;
            case SDLK_DOWN: return ACT_DOWN;
            case SDLK_RETURN: return ACT_SELECT;
            case SDLK_ESCAPE: return ACT_BACK;
        }
    }
    if (e->type == SDL_CONTROLLERBUTTONDOWN) {
        switch (e->cbutton.button) {
            case SDL_CONTROLLER_BUTTON_DPAD_UP: return ACT_UP;
            case SDL_CONTROLLER_BUTTON_DPAD_DOWN: return ACT_DOWN;
            case SDL_CONTROLLER_BUTTON_A: return ACT_SELECT;
            case SDL_CONTROLLER_BUTTON_B: return ACT_BACK;
        }
    }
    // The left stick as a d-pad, with a latch so holding it is one step, not sixty.
    if (e->type == SDL_CONTROLLERAXISMOTION && e->caxis.axis == SDL_CONTROLLER_AXIS_LEFTY) {
        static int latched = 0;
        int v = e->caxis.value;
        if (latched == 0 && v < -20000) { latched = -1; return ACT_UP; }
        if (latched == 0 && v > 20000) { latched = 1; return ACT_DOWN; }
        if (latched != 0 && v > -8000 && v < 8000) latched = 0;
    }
    // Raw joystick fallback, for a pad SDL has no controller mapping for. Xbox-layout guesses:
    // button 0 = A/select, 1 = B/back, hat for the d-pad, axis 1 for the stick.
    if (e->type == SDL_JOYBUTTONDOWN && !is_mapped(e->jbutton.which)) {
        if (e->jbutton.button == 0) return ACT_SELECT;
        if (e->jbutton.button == 1) return ACT_BACK;
    }
    if (e->type == SDL_JOYHATMOTION && !is_mapped(e->jhat.which)) {
        if (e->jhat.value & SDL_HAT_UP) return ACT_UP;
        if (e->jhat.value & SDL_HAT_DOWN) return ACT_DOWN;
    }
    if (e->type == SDL_JOYAXISMOTION && e->jaxis.axis == 1 && !is_mapped(e->jaxis.which)) {
        static int jlatched = 0;
        int v = e->jaxis.value;
        if (jlatched == 0 && v < -20000) { jlatched = -1; return ACT_UP; }
        if (jlatched == 0 && v > 20000) { jlatched = 1; return ACT_DOWN; }
        if (jlatched != 0 && v > -8000 && v < 8000) jlatched = 0;
    }
    return ACT_NONE;
}

static Action autopilot_next(void) {
    static char *script = NULL;
    static char *cursor = NULL;
    static Uint32 last = 0;
    static const char *state_file = NULL;
    if (!script) {
        // MENU_AUTOPILOT_FILE holds the remaining tokens ACROSS invocations - each consumed token
        // is removed from the file, so a test can script a whole session of menu round trips.
        state_file = getenv("MENU_AUTOPILOT_FILE");
        if (state_file) {
            FILE *f = fopen(state_file, "r");
            if (f) {
                static char buf[1024];
                size_t n = fread(buf, 1, sizeof(buf) - 1, f);
                while (n && (buf[n - 1] == '\n' || buf[n - 1] == '\r')) n--;
                buf[n] = 0;
                fclose(f);
                script = buf;
                cursor = script;
            }
        }
        if (!script) {
            const char *env = getenv("MENU_AUTOPILOT");
            if (!env) return ACT_NONE;
            script = strdup(env);
            cursor = script;
        }
    }
    if (!cursor || !*cursor || SDL_GetTicks() - last < 100) return ACT_NONE;
    last = SDL_GetTicks();
    char *comma = strchr(cursor, ',');
    size_t len = comma ? (size_t)(comma - cursor) : strlen(cursor);
    Action a = ACT_NONE;  // unknown or empty tokens burn 100 ms - that is the "wait" primitive
    if (len == 2 && strncmp(cursor, "up", 2) == 0) a = ACT_UP;
    else if (len == 4 && strncmp(cursor, "down", 4) == 0) a = ACT_DOWN;
    else if (len == 5 && strncmp(cursor, "enter", 5) == 0) a = ACT_SELECT;
    else if (len == 4 && strncmp(cursor, "back", 4) == 0) a = ACT_BACK;
    cursor = comma ? comma + 1 : cursor + len;
    if (state_file) {
        FILE *f = fopen(state_file, "w");
        if (f) {
            fputs(cursor, f);
            fclose(f);
        }
    }
    return a;
}

// ---- main ----------------------------------------------------------------------------------

int main(int argc, char **argv) {
    Entry games[MAX_ENTRIES];
    int n_games = 0;
    for (int i = 1; i < argc && n_games < MAX_ENTRIES; i++) {
        const char *arg = argv[i];
        Entry *e = &games[n_games];
        e->disabled = (arg[0] == '!');
        if (e->disabled) arg++;
        const char *bar = strchr(arg, '|');
        if (!bar) continue;
        snprintf(e->label, sizeof(e->label), "%.*s", (int)(bar - arg), arg);
        snprintf(e->payload, sizeof(e->payload), "%s", bar + 1);
        n_games++;
    }

    const bool windowed = getenv("MENU_WINDOWED") != NULL;
    SDL_SetHint(SDL_HINT_VIDEO_ALLOW_SCREENSAVER, "0");
    // Under gamescope the menu can find itself unfocused - especially relaunched after a crashed
    // session - and SDL's default is to deliver controller input only to the focused window,
    // which reads as "every button is dead". A fullscreen menu is never a background app in any
    // sense that matters, so take events regardless.
    SDL_SetHint(SDL_HINT_JOYSTICK_ALLOW_BACKGROUND_EVENTS, "1");
    if (SDL_Init(SDL_INIT_VIDEO | SDL_INIT_GAMECONTROLLER) != 0 || TTF_Init() != 0) {
        fprintf(stderr, "[menu] SDL init: %s\n", SDL_GetError());
        return 2;
    }
    SDL_ShowCursor(SDL_DISABLE);
    // SDL starts with text input active, and under gamescope an app accepting text input summons
    // Steam's on-screen keyboard over the menu. Nothing here ever wants a keyboard.
    SDL_StopTextInput();
    for (int i = 0; i < SDL_NumJoysticks(); i++) open_pad(i);

    SDL_Window *window = SDL_CreateWindow(
        "PS1 Link Cable", SDL_WINDOWPOS_CENTERED, SDL_WINDOWPOS_CENTERED, 1280, 800,
        windowed ? SDL_WINDOW_SHOWN : SDL_WINDOW_FULLSCREEN_DESKTOP);
    if (!window) {
        fprintf(stderr, "[menu] SDL_CreateWindow: %s\n", SDL_GetError());
        return 2;
    }
    SDL_Renderer *rend = SDL_CreateRenderer(window, -1, SDL_RENDERER_ACCELERATED | SDL_RENDERER_PRESENTVSYNC);
    if (!rend) rend = SDL_CreateRenderer(window, -1, SDL_RENDERER_SOFTWARE);
    SDL_RenderSetLogicalSize(rend, 1280, 800);

    TTF_Font *font_big = font_open(52);
    TTF_Font *font = font_open(34);
    TTF_Font *font_small = font_open(22);
    if (!font || !font_big || !font_small) {
        fprintf(stderr, "[menu] no usable font found\n");
        return 2;
    }

    const SDL_Color WHITE = {235, 235, 235, 255};
    const SDL_Color DIM = {110, 110, 110, 255};
    const SDL_Color GOLD = {224, 178, 74, 255};
    const SDL_Color BG = {16, 18, 24, 255};
    const SDL_Color BAR = {46, 52, 70, 255};

    Screen screen = SCREEN_MAIN;
    int sel = 0;
    Host hosts[MAX_HOSTS];
    int n_hosts = 0;
    int beacon_fd = -1;
    char result[320] = "";
    bool quit = false;

    while (!quit && !result[0]) {
        Action act = autopilot_next();
        SDL_Event e;
        while (act == ACT_NONE && SDL_PollEvent(&e)) {
            if (e.type == SDL_JOYDEVICEADDED) open_pad(e.jdevice.which);
            act = translate(&e);
            // The first few raw events are the whole diagnosis when input seems dead: they say
            // whether anything arrives at all, and on which API.
            static int logged = 0;
            if (logged < 20 && (e.type == SDL_CONTROLLERBUTTONDOWN || e.type == SDL_JOYBUTTONDOWN ||
                                e.type == SDL_JOYHATMOTION || e.type == SDL_KEYDOWN)) {
                fprintf(stderr, "[menu] input ev=0x%x act=%d\n", e.type, (int)act);
                logged++;
            }
        }

        if (screen == SCREEN_JOIN) beacon_poll(beacon_fd, hosts, &n_hosts);

        int count = screen == SCREEN_MAIN ? 3 : screen == SCREEN_HOST ? n_games : n_hosts;
        if (count > 0) {
            if (act == ACT_UP) sel = (sel + count - 1) % count;
            if (act == ACT_DOWN) sel = (sel + 1) % count;
        }
        if (sel >= count) sel = count ? count - 1 : 0;

        if (act == ACT_BACK) {
            if (screen == SCREEN_MAIN) quit = true;
            else {
                if (screen == SCREEN_JOIN && beacon_fd >= 0) { close(beacon_fd); beacon_fd = -1; }
                screen = SCREEN_MAIN;
                sel = 0;
            }
        } else if (act == ACT_SELECT) {
            if (screen == SCREEN_MAIN) {
                if (sel == 0 && n_games > 0) { screen = SCREEN_HOST; sel = 0; }
                else if (sel == 1) {
                    screen = SCREEN_JOIN;
                    sel = 0;
                    n_hosts = 0;
                    beacon_fd = beacon_open();
                } else if (sel == 2) quit = true;
            } else if (screen == SCREEN_HOST && sel < n_games && !games[sel].disabled) {
                snprintf(result, sizeof(result), "%s", games[sel].payload);
            } else if (screen == SCREEN_JOIN && sel < n_hosts) {
                snprintf(result, sizeof(result), "JOIN %s", hosts[sel].ip);
            }
        }

        SDL_SetRenderDrawColor(rend, BG.r, BG.g, BG.b, 255);
        SDL_RenderClear(rend);
        draw_text(rend, font_big, "PS1 LINK CABLE", 90, 70, GOLD);

        int y = 220;
        if (screen == SCREEN_MAIN) {
            const char *items[] = {"Host a game", "Join a game", "Quit"};
            for (int i = 0; i < 3; i++, y += 70) {
                bool dead = (i == 0 && n_games == 0);
                if (i == sel) {
                    SDL_Rect bar = {70, y - 8, 620, 56};
                    SDL_SetRenderDrawColor(rend, BAR.r, BAR.g, BAR.b, 255);
                    SDL_RenderFillRect(rend, &bar);
                }
                draw_text(rend, font, items[i], 90, y, dead ? DIM : WHITE);
            }
            draw_text(rend, font_small, "D-pad: move    A: select    B: quit", 90, 730, DIM);
        } else if (screen == SCREEN_HOST) {
            draw_text(rend, font, "Choose a game to host", 90, 160, WHITE);
            for (int i = 0; i < n_games; i++, y += 60) {
                if (i == sel) {
                    SDL_Rect bar = {70, y - 6, 900, 50};
                    SDL_SetRenderDrawColor(rend, BAR.r, BAR.g, BAR.b, 255);
                    SDL_RenderFillRect(rend, &bar);
                }
                draw_text(rend, font, games[i].label, 90, y, games[i].disabled ? DIM : WHITE);
            }
            draw_text(rend, font_small, "A: host this game    B: back", 90, 730, DIM);
        } else {
            draw_text(rend, font, "Looking for hosts on your network...", 90, 160, WHITE);
            if (n_hosts == 0) {
                draw_text(rend, font, "(none yet - start hosting on the other Deck)", 90, y, DIM);
            }
            for (int i = 0; i < n_hosts; i++, y += 60) {
                if (i == sel) {
                    SDL_Rect bar = {70, y - 6, 1100, 50};
                    SDL_SetRenderDrawColor(rend, BAR.r, BAR.g, BAR.b, 255);
                    SDL_RenderFillRect(rend, &bar);
                }
                char line[256];
                snprintf(line, sizeof(line), "%s  -  %s", hosts[i].name, hosts[i].game);
                draw_text(rend, font, line, 90, y, WHITE);
            }
            draw_text(rend, font_small, "A: join    B: back", 90, 730, DIM);
        }
        SDL_RenderPresent(rend);
    }

    if (beacon_fd >= 0) close(beacon_fd);
    TTF_CloseFont(font_big);
    TTF_CloseFont(font);
    TTF_CloseFont(font_small);
    SDL_DestroyRenderer(rend);
    SDL_DestroyWindow(window);
    TTF_Quit();
    SDL_Quit();

    if (result[0]) {
        printf("%s\n", result);
        return 0;
    }
    return 1;
}
