# DESIGN — unified PS1 Link app

Scope agreed with the tester, 2026-07-25 (end of session 7). This supersedes the Sunshine/Moonlight
approach for the finished product; see `HANDOVER.md` for what already works today.

## Requirements (the tester's wishlist, verbatim intent)

1. **One unified app** that handles everything.
2. On launch, choose **Host** or **Join**.
3. **Host**: select the game, app launches instance A and B. The host **sees and hears only A**.
   Their Steam Deck controller (or any connected pad) drives **A**.
4. **Join**: a search screen finds hosts **on the LAN only** (deliberately, for a smaller attack
   surface). On joining, the player gets **instance B's screen and sound**, and their controller
   drives **B**.
5. Play.

**Game Mode is the holy grail. If it requires Desktop Mode, the idea is dead.** This is about
convenience, QoL and pick-up-and-play — no faff for the user.

## The constraint that decides the architecture

Two requirements collide:

- the host must **see only instA**, so instB has to be invisible; and
- **Game Mode** runs gamescope, which composites exactly **one** app fullscreen and provides no
  desktop, no window manager and no xdg-desktop-portal.

**Sunshine captures displays, not windows.** Every route we proved or ruled out in session 7 —
portal/PipeWire capture, `vkms` virtual displays, KMS capture, Xwayland roots, gamescope's headless
backend — assumes a desktop session. None of them survive Game Mode.

**Therefore the transport must be ours.** Sunshine and Moonlight got us a working *Desktop Mode*
product (documented in HANDOVER and worth keeping as the fallback), but they cannot reach the goal.

## Chosen architecture

One app, two modes, with streaming built in rather than bolted on:

- **Host**: runs instA visible (this is the app gamescope shows fullscreen) and instB **headless**.
  instB hands each finished frame and audio buffer to an encoder, ships them over the LAN, and
  applies controller input arriving from the joiner. The SIO1 link between A and B stays on
  **loopback**, which is the only channel fast enough (~182 ordered round trips per payload must fit
  inside a frame; loopback 0.076 ms × 182 = 13.8 ms, a real network is ~30× too slow).
- **Join**: discovers hosts by UDP broadcast, connects, decodes video/audio to a fullscreen view,
  sends its controller input back.

This removes the entire problem class that cost session 7: no compositor, no portal, no `vkms`, no
sudo, no Steam Input contention, no display capture, no pairing PINs, no mDNS, no certificates.

**LAN-only makes it simpler, not harder** — broadcast discovery, bind to the local subnet, no
accounts or pairing.

**We already build PCSX-Redux from source** (see HANDOVER "Build/deploy"), so adding a frame/audio
export path to instB is a normal change, not a fork of something we do not control.

**Cheaper variant if we want to avoid emulator internals:** an `LD_PRELOAD` shim hooking
`SwapBuffers` on instB to grab frames, the technique OBS-style game capture uses. Hackier, but no
patch to maintain. Decide at stage 2.

## What `-no-ui` settles (measured 2026-07-25, session 8)

`pcsx-redux -no-ui` swaps the GUI for `PCSX::TUI` (`src/main/textui.cc`), a stub UI with **no window,
no GL context, no GLFW and no terminal requirement**. Measured on the PC and again on the Deck: it
boots the BIOS, runs the disc and holds the loopback SIO1 link at **100.1% / 60.1 fps**, identical to
the GUI instance, while `xdotool search --class pcsx` finds exactly one window.

**This deletes the Stage 1 blocker rather than answering it.** There is no second window for
gamescope to composite, focus, or leak onto the screen, so none of the session-7 questions about
hidden Xwayland roots or unfocusable instances apply. instB is simply not on screen.

It also settles two things further down the plan:

- **instB takes no controller input**, because there is no GLFW to read a pad. Steam Input handing
  the same virtual pad to both instances — listed as a Stage 1 unknown — cannot happen. In exchange,
  injecting the joiner's input into instB becomes a *requirement* of stage 2, not an option.
- **The `LD_PRELOAD` variant is dead.** A headless instance never calls `SwapBuffers`, so there is
  nothing to hook. The frame/audio export path has to be a patch, which is what we already build.

### The control surface that makes a headless instance usable

The emulator already ships what stage 2 needs in prototype form, and it works headless:

| | |
|---|---|
| `-webserver -webserver-port N` | HTTP API, bound `127.0.0.1` (patched; upstream binds `0.0.0.0`) |
| `GET /api/v1/screen/still` | PNG of the emulated screen, straight from `m_gpu->takeScreenShot()` |
| `-dofile x.lua` + `GET /api/v1/lua/<name>` | calls `PCSX.WebServer.Handlers.<name>` |
| `PCSX.SIO0.slots[1].pads[1].setOverride(bit)` | forces a button; ANDed into `buttonStatus` on every read, so it works with no pad and no GLFW |

Pad overrides are called with a **dot, not a colon** — the binding takes the button as its first
argument, so an implicit `self` fails with "Invalid argument to setOverride".

Proven end to end: a `-no-ui` instance was driven from the FMV to Retaliation's main menu over HTTP
alone, and both sides then showed **LINK GAME** — the entry that only appears when the peer socket is
connected. So the whole of Stage 1 is verifiable from SSH with nobody holding a controller.

PNG-per-frame over HTTP is far too slow to be the stage 2 transport, but the two hooks it proves —
`takeScreenShot()` for video and pad overrides for input — are the right ones.

**Trap: Steam's `steamwebhelper` listens on `127.0.0.1:8080`,** and the emulator's web server gives
up *silently* when it cannot bind. A collision looks like a clean launch with a dead control surface.
The launcher uses 6680/6681 and pings both after startup.

This control surface authenticates nothing and can write files, so it is confined to loopback by
`patches/loopback-bind-session9.patch` — everything Stage 1 drives is local, and development from
another machine goes through an SSH tunnel. Stage 2's transport is the first thing in this project
that *has* to accept a connection from the LAN, so it needs its own answer to that question rather
than inheriting this one: the stream endpoint should carry only frames, audio and pad input, and
must not become a second route to the emulator's Lua and memory APIs.

## Staging

### Stage 1 — Game Mode viability (make-or-break, smallest useful step)

One app, launched from the Steam library in **Game Mode**, that:

- starts **both** emulator instances with the link on loopback,
- shows **only instA** fullscreen,
- lets the Deck's own controller drive instA,
- exits cleanly, leaving nothing running.

No streaming, no join mode, no UI yet.

**Built and deployed** as `deploy/deck/gamemode/` — `ps1-link-gamemode.sh` (launcher),
`instb-ctl.lua` (headless control surface), `pad.sh` (drive either side from a shell),
`install.sh` (hash-verified deploy). It takes over the existing "PS1 LAN Link" Steam shortcut so no
`shortcuts.vdf` editing is needed; the previous target is kept as `ps1-link-netpeer.sh`.

Verified on the PC against a mirrored directory tree: both instances up, link socket ESTAB, **one
window**, both sides showing LINK GAME, both at 100.1%, and a SIGTERM leaving no process and a free
port. Verified on the Deck: the same binary, headless, at 100% with a live control surface.

**Proven in Game Mode on the Deck, 2026-07-25.** Launched from the Steam library: instA fullscreen
and visible, instB headless and invisible, link socket ESTAB, **both at ~100% / 60 fps**, the Deck's
own controller driving instA (the tester reached `WAITING TO CONNECT` and then the match-options screen),
and instB driven to `COUNTRY/COLOR` over HTTP from another machine at the same time. Game Mode is
viable; the Sunshine/Moonlight stack is not needed for the host side.

Two things had to be fixed to get there, both worth remembering:

- **`virtual-pads.py` must not be running.** It is a Desktop Mode workaround, and in Game Mode it is
  pure harm: its uinput pads occupy the low gamepad indices, so `PAD_ID=0` binds to a pad nobody is
  holding and the Deck's own controls appear dead. Stopping the `ps1-pads` unit fixed the controller
  *live*, without a relaunch — the emulator rescans on joystick hotplug. The launcher now stops it
  and logs each gamepad against the `PAD_ID` that selects it.
- **Windowless is not silent.** instB's SPU still opens an audio device, so the host hears both sides
  at once. The launcher now sets `SPU.Mute` on instB. Stage 2 replaces this: instB's audio has to be
  captured and sent, not discarded.

The unknowns that needed Game Mode, and how they landed:

- **Does Steam Input give instA a usable pad, and at which index?** Yes — Steam presents two virtual
  X-Box 360 pads and `PAD_ID=0` is correct, *once the phantom pads are gone*. The tester's hypothesis that
  Game Mode fixes the Desktop Mode controller problems holds.
- **Does gamescope present instA cleanly fullscreen?** Yes.
- **Does a Steam shortcut survive a script that spawns two processes?** Yes.

### Stage 2 — bespoke streaming

instB's video and audio out over LAN; controller input back. Real risks: encode latency, audio sync,
and CPU headroom on a Deck already running two emulators (measured: both instances hold 100% / 60 fps
with software x264 encoding for one stream, load ~3.9 of 8 threads — encouraging but not proof).

Input is no longer optional here: a headless instB has no GLFW, so the joiner's pad has to be
injected. Pad overrides are the mechanism; the HTTP route is the prototype, not the transport.

### Stage 3 — the product

Host/Join screens, LAN discovery, game picker over the RetroDeck library (with `.cue` generation and
`.ecm` handling), remembered settings, clean errors.

## What carries over from session 7

Everything except the transport:

- the loopback link itself, and the measurement that proves it (83–100% in gameplay on one Deck)
- `link-play.sh` — game resolution, `.cue` generation, per-instance server/client config, user units
- `virtual-pads.py` — uinput gamepads, if still needed in Game Mode
- `ecm2bin` and the library findings (Retaliation and Dune 2000 work; Doom hangs when linked; the
  "Red Alert" disc is mislabelled Retaliation)
- every trap in HANDOVER: pcsx.json rewritten on exit, gamepad index fragility, `setsid nohup` being
  reaped by logind, restart-both-together, static-screen measurement errors

## Fallback

If Game Mode proves impossible, the **Desktop Mode + Sunshine** stack already works end to end and is
fully documented. It costs one `modprobe vkms` (needs sudo) to give each player a full-screen view
instead of a split one.
