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

## Staging

### Stage 1 — Game Mode viability (make-or-break, smallest useful step)

One app, launched from the Steam library in **Game Mode**, that:

- starts **both** emulator instances with the link on loopback,
- shows **only instA** fullscreen,
- lets the Deck's own controller drive instA,
- exits cleanly, leaving nothing running.

No streaming, no join mode, no UI yet. This answers the only question that can kill the project:
**does Steam Input give our app a usable controller in Game Mode, and can instB run invisibly
alongside without gamescope showing it?** The tester's hypothesis is that Game Mode fixes the controller
problems that plague Desktop Mode; this tests it.

Known unknowns to resolve here:

- Whether gamescope will run two instances with only the focused one presented (session 7 saw
  instances render to gamescope's hidden Xwayland `:1` — invisible, which is what we now *want* for
  instB, but they were also unfocusable and uncapturable).
- Whether Steam Input presents one virtual pad to the whole session (both instances would then see
  the same input) and whether our uinput pads are still needed for instB.
- Whether a Steam shortcut can launch a script that spawns two processes and survives.

### Stage 2 — bespoke streaming

instB's video and audio out over LAN; controller input back. Real risks: encode latency, audio sync,
and CPU headroom on a Deck already running two emulators (measured: both instances hold 100% / 60 fps
with software x264 encoding for one stream, load ~3.9 of 8 threads — encouraging but not proof).

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
