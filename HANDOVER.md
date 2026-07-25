# HANDOVER — pick up here

Session 7 (2026-07-25) ran the two-Deck cable test and **closed that frontier — symmetry does not
help.** Read the session-7 section first. Session 6 remains the reference for the *root cause* of the
menu slowness, and the loopback story (session 4) for *how the link works*.
`docs/FINDINGS.md` holds the full evidence trail. Logs/screenshots: `docs/session7-evidence/`,
`docs/session5-evidence/`.

**Goal:** C&C Red Alert Retaliation link-cable play over LAN between two Steam Decks.

---

## 📺 Session 10 — instB's frames and audio come out; stage 2 step 1 is done

`patches/media-export-session10.patch`. A headless instance now publishes every displayed frame and
the mixed audio it would have played, and both were verified arriving from a real Retaliation boot.
**Not yet deployed to the Decks**, and there is no joiner yet — that is step 2.

**The emulator does not listen on the LAN, and should not start.** It publishes to a **unix domain
socket**; the encoder and whatever goes on the wire belong to a separate process. `DESIGN-unified-app.md`
warns that the stream endpoint "must not become a second route to the emulator's Lua and memory
APIs" — keeping the LAN listener out of the emulator process entirely is the strongest form of that,
and it keeps ffmpeg/x264 out of the pcsx-redux build. Preserve this when building the joiner.

| Piece | Where |
|---|---|
| `src/support/mediastream.{h,cc}` | exporter thread, socket, bounded queues, wire format, `STREAM` line |
| `src/core/psxemulator.cc` | `vsync()` → `exportFrame()`, after `vblank()` so the frame is complete |
| `src/spu/miniaudio.cc` | taps the mixed int16 **before** the mute zeroing |
| `src/main/main.cc` | `-stream` / `-stream-socket <path>` / `PCSX_STREAM_SOCKET`; `-no-stream` wins |
| `tools/stream-probe.py` | the consumer: parses the stream, writes PNGs and a wav, reports rates |

**Measured on the PC, one headless instance booting Retaliation** (`-no-ui -run -stream`):

| | |
|---|---|
| Emulation while streaming | **100.14–100.41% / 60.1 fps**, unchanged from solo |
| Frames delivered | **601 in 10.0 s = 60.1 fps**, `dropped=0`, backlog empty |
| Frame gap | median 16.4 ms, p99 21.2, max 21.3 |
| Audio | **44100 Hz, 44 104 frames/s** — exactly real time, `dropped=0` |
| Cost on the emulation thread | `pushMaxUs=11–38 µs` against a 16 600 µs frame budget |

Both pixel formats were confirmed **visually**, not just by byte count: the 24bpp FMV and the 16bpp
title screen at 512x240 with correct colours (a red/blue swap in the BGR555 unpack would show here).

**The number that decides step 2: raw video is 10.8–14.1 MiB/s, about 86–113 Mbit/s.** That is fine
over a unix socket and **too much for the Deck's wifi**, so an encoder is required rather than
optional. Encoding was deliberately left out of the emulator so its cost is measured separately
instead of baked in.

**Producers never block.** Video (emulation thread) and audio (device thread) hand to bounded queues
that drop and count; all I/O is on the exporter thread, which holds no lock while writing. Given
that every previous failure in this project was a stall propagating into the link, the export must
not be able to add one. Drop counters are cumulative and uncapped, so a silently degrading stream
cannot look like a healthy one.

### Traps found this session

1. **`-j$(nproc)` OOM-killed the whole desktop, twice.** Several translation units need ~2.2 GB in
   `cc1plus`; 20 of them exhaust 31 GB, the kernel starts killing the session, and the machine locks
   up hard enough to need a power cycle. **`make -j4` in this file is a memory limit, not a
   preference.** Build with `-j4` and `--memory=12g --cpus=4` on `docker run` so an overrun kills the
   compiler and not the user's session. Diagnose a suspected lockup with
   `journalctl -b -1 --no-pager | grep "invoked oom-killer"`.
2. **A unix socket path is capped at 108 bytes** (`sun_path`), which is shorter than a lot of temp
   directories. The failure is reported rather than silent — unlike the web server's bind — but put
   the socket in `$XDG_RUNTIME_DIR` (`/run/user/1000`), which is where the default lands anyway.
3. **Muting instB does not mute the stream, by construction.** The tap runs before the mute zeroing,
   verified with `SPU.Mute = true`: local device silent, stream still carrying audible samples.

### Wire format (little-endian; a consumer implementation is `tools/stream-probe.py`)

24-byte `Hello` on connect — magic `P1LS`, version, header size, audio rate, channels, max video
dimensions. Then 24-byte packets: magic `P1PK`, type (1 video / 2 audio), format (video: 0 BGR555,
1 RGB888; audio: 0 s16 interleaved), width (audio: channels), height (audio: frame count), aux
(audio: sample rate), payload length, and a monotonic microsecond timestamp taken when the producer
handed the data over. One consumer at a time; a slow one gets frames dropped, not the emulator
stalled.

### Next — step 2, the crudest possible joiner

A fullscreen window on the second Deck that decodes this stream and sends pad input back. Input
still goes through the pad-override path; the open question is whether it rides this same socket
(low latency, no HTTP) or the existing loopback HTTP control surface (already proven). Then step 3:
measure end-to-end latency and whether both emulators still hold 60 fps with an encoder alongside
them. **What identifies a host is still the one call that cannot be deferred** — an address in a
conf file now, per the design.

---

## 🔒 Session 9 — every listener binds loopback

`patches/loopback-bind-session9.patch`. The web server, the SIO1 listener and the GDB server all
bound `0.0.0.0` upstream and none of them authenticate anything; they now bind `127.0.0.1` through
`PCSX::Uv::makeBindAddress()` (`src/support/uvbind.h`), with `PCSX_BIND_ADDRESS` as the opt-out for
the cross-machine peer scripts. Detail, measurements and what is still exposed are in
"Security debts" below. **The Decks have not been updated** — deploy before treating this as done.

---

## 🎉 Session 8 — GAME MODE WORKS. Stage 1 of the unified app is met.

Read `docs/DESIGN-unified-app.md` first; this is the summary.

**A single Steam-library entry in Game Mode now runs both sides of a link game, and the tester played a
real linked match through it on 2026-07-25.** instA fullscreen and driven by the Deck's own
controller; instB invisible and driven over HTTP from the PC; link on loopback. His verdict:
*"worked nicely!"*

Measured across the whole session (`docs/session8-evidence/`, 148 samples per side):

| Phase | instA | instB |
|---|---|---|
| Boot + menus | 100.0% / 60.0 fps | 100.0% / 60.0 fps |
| Link handshake (~10 s) | 60.5–85.6%, median 61.4% | 100.0% |
| **Gameplay** | **100.1% / 60.1 fps** | **97.1% / 58.3 fps** |

The handshake dip is confined to instA — instB never sees it. That is the opposite of the symmetric
collapse every networked attempt produced in sessions 5–7.

**Both run logs end in a sample that looks like a crash** (instB: 30% / 18 fps, stalling 96% of
wall). That is teardown: instA is killed first and instB spends its last seconds stalling on a dead
peer. Do not quote the final line as a result.

**The unlock was `pcsx-redux -no-ui`.** It swaps the GUI for `PCSX::TUI` — no window, no GL context,
no GLFW, no terminal — so gamescope has exactly one app to composite. Every session-7 problem about
hidden Xwayland roots, `vkms`, portal capture and display capture simply does not arise. Sunshine and
Moonlight are not needed for the host side.

**What runs it:** `deploy/deck/gamemode/` — `ps1-link-gamemode.sh` (launcher), `instb-ctl.lua`
(HTTP control surface), `pad.sh` (drive either side from a shell), `install.sh` (hash-verified
deploy). It takes over the existing "PS1 LAN Link" Steam shortcut; the previous target is kept as
`ps1-link-netpeer.sh`.

**A headless instance is fully drivable and observable over HTTP**, which is how the whole of Stage 1
was verified from SSH with nobody holding a controller:

- `GET /api/v1/screen/still` — PNG of the emulated screen, works headless
- `-dofile x.lua` + `GET /api/v1/lua/<name>` — calls `PCSX.WebServer.Handlers.<name>`
- `PCSX.SIO0.slots[1].pads[1].setOverride(bit)` — forces a button with no pad and no GLFW. **Dot, not
  colon**: the binding takes the button as its first argument.

### Traps found this session — all three look like "the app is broken"

1. **`virtual-pads.py` must be stopped in Game Mode.** It is a Desktop Mode workaround. Its uinput
   pads occupy the low gamepad indices, so `PAD_ID=0` binds to a pad nobody is holding and the Deck's
   own controls appear dead. Stopping `ps1-pads` fixes it **live** — the emulator rescans on joystick
   hotplug. The unit is now disabled and the launcher stops it anyway. `PAD_ID` indexes
   *gamepad-capable devices*, not `/dev/input/js*`; the launcher logs the mapping.
2. **Windowless is not silent.** instB has no window but its SPU still opens an audio device, so the
   host hears both sides. The launcher sets `SPU.Mute` on instB. Stage 2 turns that mute into a tap.
3. **Steam's `steamwebhelper` owns `127.0.0.1:8080`**, and the emulator's web server fails to bind
   *silently* — a collision looks like a clean launch with a dead control surface. Control ports are
   6680/6681 and the launcher pings both after startup.

Also: the PC, Deck 1 and the shared AppImage were each carrying a **different** emulator build.
`install.sh` now md5-verifies the binary on arrival; all sides are on `f5d3e902a8a709e7c12d1904e49786c1`.

### Where the code lives now

**`github.com/gibbo101/ps1-lan-link`, private.** Public later, once we are satisfied — the security
debts that gated it are fixed in the source (see below) but not yet on the Decks. History was
rewritten on 2026-07-25 to remove a password,
a family member's Steam account name and userdata id, and a tailnet address; every commit hash
predating that differs from anything quoted in older notes.

The repo is registered in `~/.claude/dev_journey.md` (tracker `none`, base `main`, PR flow `none`),
so `/status` and `/dev-code-review` work here. `/dev-code-review` only has branch-only mode, and
that mode diffs against `origin/main` — now that a remote exists it works normally.

### Security debts — cleared by the loopback bind patch (session 9)

Both debts were upstream binds to `0.0.0.0`: the unauthenticated control API (6680/6681), which
reads the screen, drives both pads, dumps emulated RAM and overwrites any file this user can write
via `/api/v1/screen/save?filepath=`; and the SIO1 listener (6699), which re-points the link at any
later connection, so a host on the LAN could displace instB mid-match.

**`patches/loopback-bind-session9.patch` binds every listener to `127.0.0.1`** — the web server, the
SIO1 listener (`UvFifoListener`) and the GDB server, which had the same bind and the same lack of
authentication. `PCSX::Uv::makeBindAddress()` in the new `src/support/uvbind.h` is the single place
that decides; `PCSX_BIND_ADDRESS` overrides it for the one case that genuinely needs a peer on
another machine (`repro/run-server.sh`, and `ps1-link.sh` when `ROLE=server`), and a value that is
not a valid IPv4 address falls back to loopback rather than leaving the socket unbound.

Verified on the PC with the rebuilt binary (md5 `369523e845daa10500d30524e2c01082`): all three
listeners come up on `127.0.0.1`, `PCSX_BIND_ADDRESS=0.0.0.0` puts all three back on `0.0.0.0`, a
garbage value lands on loopback, and the Stage 1 launcher still reaches **2 link sockets and
100.15% / 60.1 fps on both instances** with both control surfaces answering.

**Remote access for development is now an SSH tunnel** (`ssh -L 6681:127.0.0.1:6681 deck@steamdeck`).
`pad.sh` defaults to loopback, so it still works unchanged when run *on* a Deck; `HOST=steamdeck`
from another machine no longer connects.

**Deployed to both Decks and played.** Both now run md5 `369523e845daa10500d30524e2c01082` — Deck 2
was two builds behind (`5d496f64…`, session 6) and had the same exposure, and its previous launcher
is kept as `ps1-link-netpeer.sh`. Launched from Deck 1's Game Mode library: link sockets 2, all three
listeners on `127.0.0.1`, both instances at 100.0% / 60 fps, both pads visible to Steam Input, and
**The tester played a linked match — "running fine", with the familiar menu slowdown and full-speed
gameplay.** The menu dip is session 6's idle-poll cost, not this patch. Screens in
`docs/session9-evidence/`, including both sides at match options with identical settings and only
the host offered the start.

### Next

Stage 2 proper — bespoke streaming: instB's video and audio to the joiner, controller input
back. Input is a hard requirement rather than an option, because a headless instB has no GLFW at
all. Pad overrides are the mechanism and `takeScreenShot()` is the frame source; HTTP is the
prototype, not the transport.

**Start with the frame/audio export patch, not the UI** (agreed session 9). Stage 2 needs a joiner,
but a second Steam shortcut reading an address from a conf file is enough of one — the Host/Join
screen and LAN discovery are Stage 3. Encode latency, audio sync and CPU headroom are the risks, all
of them measurable without a front end, and any of them can force a redesign. The one call that
cannot be deferred is what identifies a host, because the wire format inherits it. See the ordering
in `docs/DESIGN-unified-app.md`.

One thing worth revisiting: the Game Mode shortcut was claimed by overwriting `ps1-link.sh` (the old
target is kept beside it as `ps1-link-netpeer.sh`). The cleaner route is `steamos-add-to-steam`,
documented in the session-5 gotchas below — it registers a shortcut from *within* Game Mode.

---

## 🎉 THE GOAL IS MET — twice over, and it is portable

Two working configurations, both verified in live gameplay on 2026-07-25:

1. **Desktop, two pads, one screen each** — the first success, and what the tester played. Section below.
2. **Portable: one Deck hosts both sides, the second Deck streams its half over wifi.** No cable, no
   monitor, no PC. See "DECK-TO-DECK STREAMED MATCH".

The remaining work is refinement — separate displays per player, robust gamepad binding, Game Mode —
not rescue. All of it is listed under "Still open".

## 🎉 The first success — two-player Retaliation link play on one host

**Session 7 ended with the tester and his son playing a real Retaliation link game, with two DualSense pads,
on one screen each.** His verdict: *"like it was cnc retaliation link up play i remember"*. That was
the entire point of the project. Everything below is refinement, not rescue.

**How to play it again — one command:**

```bash
./repro/couch-play.sh              # defaults to a 5120x1440 screen; pass W H to change
```

Both emulators run on the **desktop**, linked over **loopback**. That is not a compromise, it is the
only channel that works: the driver needs ~182 strictly-ordered round trips per payload, so it must
complete inside one frame. Loopback's 0.076 ms x 182 = **13.8 ms**, just inside the 16.6 ms budget.
The Deck cable's 2.8 ms x 182 = **510 ms**, about **30x** too slow. That single arithmetic explains
every result in this file — why loopback always worked, why no network ever will, and why no amount
of budget tuning closed the gap.

**Measured during actual play** — both instances, simultaneously, for ~30 s of wall clock:

| | instA | instB |
|---|---|---|
| SPEED | 100.0–100.5% | 99.6–100.8% |
| `timeouts` | 0–3 | 0–2 |
| `budgetUs` | 400 (full, not backed off) | 400 |

`timeouts` at ~0 with the budget staying full means stalls were being satisfied by **real arriving
data** — genuine link traffic at 60 fps on both sides at once. Nothing before this ran a playable
screen at full speed.

**Note this ran with stall backoff at its DEFAULT (8)** — `run-link-test.sh` sets no `STALL_*` vars,
so `budgetUs` moved 400 → 200 freely. Session 6 found backoff killed link setup on the
desktop↔Deck pair; **on loopback it is fine and should be left alone.**

## ✅ ONE DECK HOSTS BOTH SIDES — proven in a live match

**A single Steam Deck ran both sides of a Retaliation link game, in gameplay, on 2026-07-25.**
Screenshots: `docs/session7-evidence/deck-hosted-match-options.png` (both at match options, identical
settings, one side `GO BACK` only, the other `✕ TO START`) and `deck-hosted-gameplay.png` (purple vs
gold armies, $10000 each, both in play). `SPEED` traces: `deck-hosted-inst{A,B}-speed.log`.

| Phase | instA | instB |
|---|---|---|
| Link setup | 86.4% / 51.9 fps (42.0% of wall) | 56.9% / 34.2 fps (59.3% of wall) |
| **Gameplay** | **83–85% / ~50 fps** (75% of wall) | **100.4% / 60.2 fps** (45.5% of wall) |

**It survived the match-start handoff** — the exact transition that killed every networked attempt.
Socket stayed ESTAB throughout.

**A prediction in this file was wrong and is now corrected.** It said a Deck would collapse to ~5%
during link setup because it lacks the desktop's ~8x realtime headroom. That extrapolated from
measurements taken at `STALL_US=15000`; at the **default 400 µs budget with adaptive backoff** the
stall costs a fraction of that, and the Deck runs 85–100%. Do not size stall costs by scaling the
15000 µs figures.

### How to reproduce it on the Deck (all three parts are required)

1. **Desktop Mode**, and launch as **user units** — `setsid nohup` is reaped by logind on SteamOS:
   `systemd-run --user --collect --unit=ps1-instA --setenv=HOME=… --setenv=XDG_CONFIG_HOME=… --setenv=DISPLAY=:0 --setenv=XAUTHORITY=/run/user/1000/xauth_… --working-directory=… bash -c "exec …"`
   Restart **both together**: a lone restart leaves the survivor holding a `CLOSE-WAIT` socket to the
   dead peer and it never sees the new one — the symptom is **no LINK GAME entry** on the main menu.
   `systemctl stop` also returns *before* the unit finishes deactivating, and `systemd-run` then
   refuses to recreate the name, so wait until no `AppRun` processes remain.
2. **Virtual gamepads** — `tools/virtual-pads.py`, run as a user unit. Steam Input consumes the
   Deck's own controls and the Steam Controller in Desktop Mode and never feeds its virtual pads
   (reading them yields exactly 19 events, the init burst, and nothing when buttons are pressed).
   Keyboard is useless too: XTEST keys never reach the game under Xwayland even with correct focus.
   The script creates two X-Box 360 pads via uinput (that GUID is in the SDL database GLFW loads) and
   takes commands on `/tmp/padctl` as `<pad> <button>`, e.g. `echo "1 cross" > /tmp/padctl`.
3. **Set `pads[0].ID` only while the emulator is stopped.** It rewrites `pcsx.json` on exit and will
   silently revert the value, which is what made the gamepad index look wrong for hours. GLFW does
   **not** accept Steam's phantom pads as gamepads, so the working indices were **2 and 3**.

Menu order that worked: drive each side to the main menu with `start`, then `up` (wraps to LINK GAME)
then `cross`; **both** press `cross` at COUNTRY/COLOR for START SETUP; the side showing `✕ TO START`
presses `cross` again. **Main menus fall back to the attract loop if left idle** — COUNTRY/COLOR does
not, so it is the safe place to park one side while driving the other.

The intro is **Enter/start on the FMV → title, then start on the title → main menu**. The older
"wait for the black transition" note elsewhere in this file does not match observed behaviour.

## ✅ DECK-TO-DECK STREAMED MATCH — the portable setup, proven

**Deck 1 hosts both instances; Deck 2 plays its side over wifi.** Verified 2026-07-25 in a live
Retaliation match: both sides reached match options with identical settings, one side showing only
`GO BACK` and the other `✕ TO START`, and **`MAP 47` instead of the default `MAP 1` — proof that Deck
2's controller input reached instB through the stream.** Evidence:
`docs/session7-evidence/deck-hosted-streamed-match.png`.

Nothing latency-critical crosses the network: the SIO1 link stays on loopback inside Deck 1, and wifi
carries only video, audio and input. Measured wifi path Deck 2 → Deck 1: **0% loss, 7.5 ms**.

### The streaming stack (all on Deck 1, Desktop Mode)

| Piece | State |
|---|---|
| Sunshine 2026.516 | flatpak `dev.lizardbyte.app.Sunshine`, run as user unit `ps1-sunshine` |
| Moonlight 6.1.0 | flatpak on **both** Decks; Game Mode shortcut registered on Deck 2 (second Steam account) |
| Web UI creds | set at first run; not recorded here — reset with the `/api/password` call below |
| Config | `~/.var/app/dev.lizardbyte.app.Sunshine/config/sunshine/sunshine.conf` |

**Four things had to be right, each of which cost real time:**

1. **`encoder = software` is mandatory.** Left to itself Sunshine picks `hevc_vulkan`, and Vulkan
   video encode produced an unplayable stream. Worse, with hardware encoding it negotiated
   **YUV 4:4:4**, which Moonlight's decoder cannot handle — the symptom is a **green/pink garbled
   picture with working audio**. With `encoder = software` it selects `libx264` and
   `profile Constrained Baseline, 4:2:0`, which decodes correctly. Software encoding costs nothing
   measurable: both instances still ran **100% / 60 fps** while streaming (load ~3.9).
2. **First run blocks on a KDE permission dialog** — *"Remote control requested — Screens, Input
   devices"* — displayed on **Deck 1's screen**. Until someone taps **Share**, Sunshine never opens
   its ports and Moonlight reports the host offline. Keep *"Allow restoring on future sessions"*
   ticked; that writes `portal_token`. **Deleting `portal_token` re-triggers the dialog**, which is
   the fix if the saved token points at a display that no longer exists (e.g. after undocking).
3. **Setup must be completed via the API**, not just `--creds`. `sunshine --creds …` writes
   credentials but leaves the app in first-run state, so every API call 307-redirects to `/welcome`.
   Complete it with:
   `curl -sk -X POST https://localhost:47990/api/password -H 'Content-Type: application/json' -d '{"newUsername":"deck","newPassword":"…","confirmNewPassword":"…"}'`
   then pair with:
   `curl -sk -u deck:… -X POST https://localhost:47990/api/pin -d '{"pin":"1234","name":"deck2"}'`
   **`{"status":true}` does not mean paired** — it returns true even with no pending request. Confirm
   from the log (`CLIENT CONNECTED`, `Gamepad 0 will be Xbox One controller`), not the state file,
   which is written lazily.
4. **Auto-discovery never works here** — `avahi::entry_group_new() failed: Not permitted`, because
   the flatpak sandbox cannot publish mDNS. **Add the host manually by IP** in Moonlight. Harmless;
   the connection itself is plain unicast to 47989.

### Audio: separate the two instances or it sounds garbled

Sunshine sets itself as the **default sink**, so *both* emulators land in the capture sink and the
stream carries two overlapping copies of the same game — which sounds like phasing/garbling. Route
instA to the Deck's speakers and leave only instB in the stream:

```
pactl move-sink-input <instA-sink-input> alsa_output.pci-0000_04_00.5-platform-nau8821-max.HiFi__Speaker__sink
```

Map sink-inputs to instances via `application.process.id` → `/proc/<pid>/environ` → `HOME`. **This
does not survive a relaunch** (both instances are named `pcsx-redux`, so `module-stream-restore`
sends them back to the default sink) — it needs folding into `link-play.sh`.

### Gamepad indices are fragile — this caused most of the lost time

`pads[0].ID` is an index into GLFW's list of *gamepad-capable* joysticks, ordered by **event device
number**. That list changes whenever devices appear: Steam's phantom pads, Sunshine's virtual pads
for a connected client, our own uinput pads. Enumerate it with:

```
# gamepad-capable devices in event order; position = the ID to use
grep -E "^N: Name|^H: Handlers" /proc/bus/input/devices
```

Devices named *"Mouse passthrough (absolute)"* are **not** gamepads and are skipped. As of the
working session: **instA = 2** (our uinput pad 0), **instB = 6** (`Sunshine X-Box One (virtual) pad`,
which is how Deck 2's input arrives). Binding by **device name rather than index** would stop this
breaking every time something reconnects — currently unimplemented.

### Any-game launcher — built, and what the library actually contains

`deploy/deck/link-play.sh` (deployed to `~/ps1-lan-link/link-play.sh`) runs **any** PS1 game linked on
one Deck: `link-play.sh <name>` matches a substring against the ready-to-play cues and the RetroDeck
library, generates a `.cue` for a raw `.bin`/`.img`, starts the virtual pads if needed, restarts both
instances as user units with the right server/client + pad-id config, and tiles the windows. With no
argument it lists what is available. Verified booting **Doom** and **Dune 2000** as well as
Retaliation, so nothing in the pipeline is game-specific.

`ecm2bin` is now on the Deck at `~/ps1-lan-link/ecm2bin` — a **static** build of
`github.com/alucryd/ecm-tools`, verified by decoding the Retaliation `.ecm` and matching md5
`313e055f…` against the known-good disc. Build it with `gcc -O2 -static -o ecm2bin ecm.c`; the Deck
has no compiler, the desktop does.

**Findings about the library itself (checked, not assumed):**

| Disc | Result |
|---|---|
| Retaliation — Allies | **works linked**, the reference |
| "C&C Red Alert — Allies [SLUS-00431]" | **mislabelled — it is Retaliation.** Boots `SLUS_006.65`, same as the Retaliation rip (different file size, same game). There is no PS1 Red Alert in the library. |
| Doom | Boots and runs **solo**, but **hangs at LOADING whenever a link peer is connected** — it syncs over the serial port at boot and our SIO1 emulation does not satisfy its handshake. A per-game protocol issue, not a launcher one. |
| Dune 2000 | Boots on both instances with the link established (`SLES_022.47`), intro plays; link menu not yet reached |
| C&C GDI / NOD | Not link-cable titles, so not converted |

Identify a disc with `grep -a -m1 "boot file" run.log` → `cdrom:\SLUS_XXX.XX;1`. That is the only
reliable way to tell what a rip actually contains; filenames lie.

**Measurement discipline note:** a claim that "Doom loads fine solo" was made from a *single frame of
a static copyright screen* — precisely the trap recorded elsewhere in this file. Compare **two
captures spaced in time** (`md5sum` them) before claiming a screen is progressing.

### Goal for the finished product (the tester, session 7)

**One package, any game.** A single launcher that lets you pick *any* PS1 game you own and play it
link-up, not just Retaliation. Nothing in the link is game-specific — SIO1 is emulated generically,
so any title with link-cable support should work. What is currently hardcoded is only the ROM path
and the per-instance config, so the work is:

- a **game picker** over a ROM directory (the Deck's RetroDeck library lives at
  `~/retrodeck/roms/psx` on this Deck — an SD-card install would sit under `/run/media/` instead),
  feeding the chosen disc to both instances
- **`.ecm` handling** — RetroDeck stores discs ECM-compressed and **PCSX-Redux cannot load `.ecm`**;
  either decode on selection or filter those entries out with a clear message
- generated per-instance configs (server/client on loopback, one gamepad each) instead of the
  hand-maintained `dual/inst{A,B}` trees
- ideally a Game Mode shortcut so it launches without Desktop Mode

### Still open — the real remaining work

1. **Separate displays per Deck — the biggest gap.** Sunshine captures a whole *display*, and there
   is only one, so the streamed player sees **both windows side by side** and watches his half. The
   fix is **`vkms`**, the virtual KMS module, which **ships with SteamOS's kernel** at
   `/usr/lib/modules/$(uname -r)/kernel/drivers/gpu/drm/vkms/vkms.ko.zst` and is **not loaded**.
   Load it, KWin sees a second monitor, instB goes fullscreen there, and Sunshine's portal capture
   targets that output — each player then gets a proper full-screen view.
   **BLOCKED: `modprobe` needs root and the sudo password is not recorded.** This is the only hard
   blocker in the stack; everything else can be done over SSH as `deck`.

   Ruled out on the way: KWin exposes no `/VirtualOutputs` DBus API; `Xephyr`/`Xvfb` are not
   installed (and Xvfb would drop the emulator to software GL); gamescope's headless backend exists
   but its Xwayland root is not composited, so capture would be black — the same reason
   `ffmpeg -f x11grab` fails on `:1`.

2. **Bind gamepads by name, not index.** See the fragility note above — this is the single largest
   source of "input does nothing" and it recurs on every reconnect.

3. **Deck 1's own controls do not work in Desktop Mode.** Steam Input consumes the Deck's built-in
   controls and the Steam Controller and never feeds its virtual pads (reading them yields exactly
   19 events — the init burst — and nothing on button presses). So the local player currently has no
   controller. Quick fix: **pair a DualSense to Deck 1**. Proper fix is item 4.

4. **Game Mode.** The tester's hypothesis is that controller problems vanish in Game Mode, and he is
   probably right for *his* side — Steam Input presents a proper pad to the focused game. But
   gamescope composites **one** window fullscreen, which is why the instances were invisible and
   unfocusable there earlier; Steam Input would hand **both** instances the same pad; and Sunshine's
   portal capture is less certain. Item 1 makes Game Mode far more viable, because instA can then be
   the single fullscreen "game" while instB renders to the virtual display for the stream.

5. **Fold everything into one launcher** — virtual pads, Sunshine, both instances, audio routing,
   window placement. Audio routing in particular is manual after every relaunch and has already
   caused a silent stream once.

6. **Upstream `uv_tcp_nodelay()`** (session 4) — a clean standalone fix worth submitting regardless.

### Historical: could one Deck host both instances?

**Answered — yes.** Detail retained because the investigation records several traps.
   `repro/deck-dual-test.sh` (already deployed to Deck 1 at `~/ps1-lan-link/`) launched two full
   instances on **one Deck** with the loopback link established, and **both held 100% / 60 fps**
   (`stalls=0`, load average 3.7 on 8 threads). The prediction that a Deck lacks the CPU for two
   instances was **wrong**.

   **But this only measured the title screen** — `stalls=0` means the game was not yet polling
   `SIO_STAT`, so the expensive phase was never entered. Do not quote the 100% figure as proof a
   Deck can host a linked *game*; the link-setup and gameplay phases remain unmeasured.

   **Why it stopped there:** under Game Mode the instances render to gamescope's hidden Xwayland
   (`:1`), which has **no window manager**, so `xdotool windowactivate` fails (no `_NET_ACTIVE_WINDOW`)
   and even `windowfocus` cannot move focus off gamescope's own window — the menus cannot be driven
   remotely. `ffmpeg -f x11grab` on `:1` also captures a **black root**, because Xwayland does not
   composite window contents there, so there is no visual verification either.

   **Desktop Mode was tried and gets further, but menu driving still failed.** SteamOS Desktop Mode
   is a **Plasma Wayland** session with Xwayland at `:0`. Both instances launch there, render
   correctly, tile side by side, and the link establishes on loopback — but they could not be driven
   to a menu. Three separate obstacles, all worth knowing before anyone tries again:

   - **`setsid nohup` is not enough on SteamOS** — logind reaps the processes when the SSH session
     that spawned them ends, so the emulators die the moment a launcher command finishes or times
     out. Launch them as **user units** instead; this works and survives disconnects:
     `systemd-run --user --unit=ps1-instA --setenv=HOME=… --setenv=DISPLAY=:0 --setenv=XAUTHORITY=/run/user/1000/xauth_… …`
   - **KWin's visual window placement does not match the geometry `xdotool` reports.** `xdotool` said
     instA was at x=1 and instB at x=639, but clicking at visual x=320 focused **instB** and x=960
     focused **instA** — mirrored. Every window-id-targeted keypress therefore went to the wrong
     instance. Target by **`xdotool mousemove <visual-x> <y> click 1`** to focus, never by window id.
   - **Keyboard input is unreliable regardless** — neither `xdotool key` after `windowactivate` nor
     the Deck's own on-screen keyboard (Steam+X) reached the game. `getwindowfocus` frequently
     returns a window that is neither instance.

   **The way to finish it is gamepads, not keyboard.** PCSX-Redux polls GLFW gamepad state directly
   (`glfwGetGamepadState`, `src/core/pad.cc:598`), which is **independent of window focus** — the
   exact property that made the desktop couch session work. Pair two pads to the Deck, set
   `pads[0].ID` to 0 and 1 in `~/ps1-lan-link/dual/inst{A,B}/.config/pcsx-redux/pcsx.json`, relaunch
   as user units, and drive it by hand. That is also the portable couch setup itself, so it is the
   real test rather than a detour.

   Also note: the intro sequence is **Enter on the FMV → title, then Enter on the title → main menu**
   (the tester, from playing it). The older "wait for the black transition" note above is not how it
   behaves in practice.
2. **Streaming route** — desktop hosts both instances, each Deck streams one and returns its own
   controls as a virtual gamepad. This is the version that gets the Decks back in your hands.

   Prepared in session 7: **Moonlight 6.1.0 is now on both Decks** (Deck 2 needed a user-level
   `flathub` remote added first — it only had a system one). **Sunshine 2025.924 is installed on the
   desktop** with `Desktop` / `Low Res Desktop` / `Steam Big Picture` apps, and **Xephyr is present**
   for the per-instance virtual displays.

   Blockers, both needing a human: Sunshine has **0 paired clients** and pairing requires an
   interactive PIN, and Sunshine is **not currently running** (`systemctl --user` inactive). The real
   unknown is whether one Sunshine host can serve **two simultaneous clients** — historically it is
   one session at a time, so the likely shape is **two Sunshine instances on separate ports**, each
   bound to its own Xephyr display with one emulator inside. Test the two-client question *before*
   building any of the display plumbing; it decides the whole design.
3. **Upstream `uv_tcp_nodelay()`** (session 4) — a clean standalone fix that speeds up any SIO1 link
   use, worth submitting regardless of this project.

**Do not** reopen stall budgets, backoff, caps or acknowledgement schemes for the *network* case.
Sessions 6 and 7 exhausted that family; see the session-7 section for why it cannot work.

### Physical setup (already done, verify it survived)

Both Decks docked, **one Ethernet cable dock-to-dock**, no switch or router in the path. Static
addresses configured as NetworkManager profiles named `ps1link` (`ipv4.never-default`, so wifi and
Tailscale routing are untouched and SSH keeps working over wifi):

| Host | SSH as | Cable IP | Ethernet interface |
|---|---|---|---|
| `steamdeck` (Deck 1) | `deck@steamdeck` | **10.0.0.1** | `enp4s0f3u1u1c2` |
| `deck2` (Deck 2) | `deck@deck2` | **10.0.0.2** | `enp4s0f3u1u4c2` |

Both NICs are ASIX AX88179 on the **USB 3** bus, gigabit carrier up. `sudo` password is the one the tester
uses on both Decks (he supplied it; not recorded here). Verify with
`ip -4 -br addr show <interface>`; if an address is missing, `sudo nmcli con up ps1link`.

**`deck2` is authorised for this project** — the tester powered it on specifically for this.

### Deck 2 is deployed (done in session 7)

Package at `~/ps1-lan-link/` on `deck2`, identical layout to Deck 1. ROM `313e055f…` and BIOS
`1e68c231…` are md5-identical to Deck 1's.

**The trap that nearly invalidated the run:** the packaged `PCSX-Redux-HEAD-x86_64.AppImage` is the
**session-5** build (Jul 24). The session-6 fix was scp'd onto Deck 1 as a bare stripped binary
(Jul 25), so it exists *only* there. Running `setup.sh` alone gives a Deck the **old** build and makes
the two Decks silently asymmetric. Deck 2 therefore got Deck 1's actual binary copied over the
extracted tree (md5 `5d496f64…` on both; the AppImage's original is kept as
`pcsx-redux.appimage-orig`). **Verify `md5sum …/emu/squashfs-root/usr/bin/pcsx-redux` matches on both
Decks before trusting any comparative measurement.** Rebuild the AppImage if you want this to stop
being a hazard.

Game Mode shortcut on Deck 2 is registered under the **second Steam account** (its own `userdata`
directory) — that is the account logged into Game Mode there, so it is the only one that can see it.
Find it with `ls ~/.steam/steam/userdata/`.
`ps1-lan-link.desktop` is now in `deploy/deck/` (it was missing, which is why Deck 2 had no shortcut).

Roles: **Deck 2 = server (10.0.0.2), Deck 1 = client (10.0.0.1 → 10.0.0.2)**.

### Measured RTT — the cable buys consistency, not speed

Two instruments agree (`tools/`-adjacent `rtt_probe.py`, 4-byte ping-pong with `TCP_NODELAY`, plus ICMP):

| | Wifi | **Cable** |
|---|---|---|
| median | 2.73 ms | **2.77 ms** |
| p99 | 6.60 ms | **3.23 ms** |
| max | 11.31 ms | **3.35 ms** |
| round trips > 1 ms | 100% | **100%** |

The ~2.8 ms is a **fixed USB pipeline cost**, not the wire — both Decks reach Ethernet through a USB hub,
and usbnet batching imposes it before the copper is involved. A Deck has no native NIC, so no cable
arrangement removes it. **Do not buy adapters expecting better**; `ethtool -C` on the AX88179 is the only
untried lever and usbnet drivers usually refuse it. The driver's tolerance is ~1 ms per round trip, so
100% of round trips are still over budget — but the **jitter** is now bounded, which is a qualitatively
different channel and the reason this run is worth doing.

To re-measure: start `python3 /tmp/echosrv.py` on Deck 1 **holding the SSH session open** (SteamOS kills
detached user processes, so `setsid`/`nohup` die on disconnect — run it as a background SSH command
instead), then `python3 /tmp/rtt_probe.py client 10.0.0.1 6698 400` on Deck 2.

### Config to use, and why

`link.conf` on the Decks is currently set to **reliable link, slow menus**
(`STALL_BACKOFF_AFTER=4294967295`). Keep it: a link that completes matters more than a fast menu, and
the fast-menu setting (`=8`) is what killed setup at side select. Consider `PCSX_LINK_LOCAL_ACK=1`
(+ `PCSX_LINK_ACK_WINDOW`, default 8) — it made setup **2.5x faster and stable on both sides**
(85% / 55%) and still completes the handshake; it does not survive match start on its own.

### Menu-driving order (verified)

The **client** enters LINK GAME first and sits on WAITING TO CONNECT; then the server follows and lands
on COUNTRY/COLOR; **both sides must press START SETUP**; then the server (host) presses ✕ to start.
`LINK GAME` only appears on the main menu when a peer is actually connected — a free liveness check.
The SIO1 client only dials out **at boot**, so a severed socket needs a **relaunch**, not a reconnect.

### Traps that cost real time this session — do not repeat

- **Never quote a speed number without confirming on screen what state both sides are in.** Two gameplay
  measurements were confounded: one measured an already-dead link, one a match that never started.
- ffmpeg distinct-frame counting is **invalid on static screens** — Retaliation's title and main menu
  score ~4 distinct frames / 6 s at *any* speed. Use the `SPEED` line instead.
- `pkill -x pcsx-redux` misses the process: it runs as **`AppRun`**. A stale instance silently hijacks
  the port. `run-link-test.sh` now kills both names.
- `tools/install-build.sh` stops running instances first — a silently skipped install (ETXTBSY) means the
  next test measures the *previous* build. Verify the binary md5 before trusting a result.

---

## ⛔ Session 7 — two symmetric Decks over a cable: a real linked game, then death at match start

**The symmetry hypothesis is dead.** Session 6 reasoned that the desktop↔Deck failure was driven by
the ~8x/1x speed asymmetry feeding mutual starvation. Two Decks over a dedicated cable are symmetric,
and they failed the same way, at the same place, with the same numbers.

### What was actually reached — the strongest evidence in the project

**Both Decks entered a genuine linked game over real hardware.** Screenshots in
`docs/session7-evidence/`:

- Deck 1 (client): in gameplay, **purple** army, $10000, map revealed
- Deck 2 (server): in gameplay, **gold** army, $10000, **`CONNECTION LOST … ABORT MISSION`**

Different colours, same map, same credits — the session-4 proof of a real link, now reproduced
Deck-to-Deck across a physical cable rather than loopback. The link *does* carry a real match start.
It just cannot survive one.

### The three phases, measured (both sides, `SPEED` lines)

| Phase | Deck 1 (client) | Deck 2 (server) | `timeouts` | Reading |
|---|---|---|---|---|
| Solo, no peer | 100.2% / 60 fps | 100.0% / 60 fps | 0 | Deck hardware is fine — again |
| Main menu, peer connected | **26.6% / 95.7% of wall** | **26.3% / 94.9% of wall** | climbing | Session 6's idle-poll bug, reproduced to within 1% |
| Link setup, data flowing | **~5% / 97.8% of wall** | **~5% / 97.7% of wall** | **≈0** | Handshake genuinely works, costs 20x |
| Match start | **0.43% / 99.9% of wall** | escapes to 100% | climbing | Divergence, then `CONNECTION LOST` |

The idle-menu figures match session 6's table (`STALL_US=15000` → 96.8% of wall, 26.9%) almost
exactly. **Symmetry changed nothing.** That also retires the mutual-starvation explanation for the
menu slowness: the simpler session-6 account — a connected-but-silent peer makes every stall run its
full budget — is sufficient on its own.

**New number worth having:** during *genuine* link setup with data flowing, `timeouts` drops to ~0 and
both sides run at **~5%**. On loopback the same phase ran 35% / 17.5%. That is the honest cost of the
handshake at 2.8 ms RTT, and it is symmetric — neither side is starving the other, they are both just
paying the round trip.

### How it died (not a mutual deadlock this time)

Session 6's in-game failure was *both* sides pinned at 0.44% with `awaiting=1`. Session 7's is
**one-sided**:

- Deck 1 wedges at 0.44% / 99.9% of wall, `awaiting=1`, every stall timing out
- Deck 2 **escapes** — `stalls=0`, back to 100% / 60 fps — because its driver gave up and raised
  `CONNECTION LOST`, leaving Deck 1 blocked on a peer that is no longer in the linked state

The socket stayed `ESTAB` throughout on both sides. Nothing crashed and nothing dropped at the
network layer. Deck 1 also showed a **147–150% catch-up burst** after being starved, which is the
emulator sprinting to reclaim frozen emulated time — a useful fingerprint of this failure.

### The cable: consistency confirmed, and it is not enough

Measured this session: **2.84 ms median, 0% loss** over the dock-to-dock cable, jitter tightly bounded
(the p99 ≈ max ≈ 3.2 ms figures below held up). Still **100% of round trips exceed the driver's ~1 ms
tolerance**. The best physical channel available to a Deck is not good enough for wall-clock lockstep,
which is the point the budget-tuning family keeps running into from different directions.

### Where this leaves the project

The wall-clock stall model is finished. It can reach a linked match on real hardware; it cannot hold
one. Every remaining lever inside `pumpTransport()` has been tried (session 6's four attempts, plus
the ack family) and each broke the link somewhere else.

Getting past match start needs sync driven **above** the SIO1 register layer — speculative execution
with rollback (GGPO-style), running ahead on predicted peer input and rewinding on mismatch. That is
a substantially larger project than anything attempted so far and should be scoped deliberately, not
started as another tuning pass.

Worth doing regardless, and cheap: **upstream `uv_tcp_nodelay()`** (session 4). It is a genuine
standalone fix that speeds up any SIO1 link use.

### Traps specific to this session

- **`ROLE=client` only dials out at boot.** Launch the **server Deck first**. If the client boots
  first it never retries, and the symptom is subtle: no `LINK GAME` entry on the main menu. That
  missing menu item is a free, reliable liveness check — trust it before driving anything.
- **Launching over SSH with `DISPLAY=:1` renders to the hidden Xwayland** in Game Mode — the
  emulator runs and logs normally but nothing is visible or reachable on the Deck. Use the Game Mode
  shortcut for anything a human needs to drive.
- **`DeviceType=0` in `pcsx.json` is the *emulated* pad type (Digital), not an input source.**
  PCSX-Redux reads real GLFW gamepads and handles hotplug (`glfwSetJoystickCallback` → `scanGamepads`,
  `src/core/pad.cc:429`). If a Deck's controller does nothing, the fault is the Steam Input layout on
  that shortcut presenting keyboard/mouse instead of a gamepad — not the emulator config.

### Couch-play gotchas (found while getting two players onto one host)

- **Gamepad input does not reset the idle timer**, so the desktop tries to sleep mid-match however
  hard you are playing. X's own screensaver is already off here (`xset q` → `timeout: 0`); it is the
  desktop environment's idle handling, so **`systemd-inhibit --what=idle:sleep`** is the fix.
  `couch-play.sh` holds one for as long as an emulator is running.
- **The window manager ignores programmatic window moves** — `xdotool windowmove` applies the size
  but not the position, and `WindowPosX/Y` in `pcsx.json` is not honoured either, so instances land
  on whichever side the WM chooses. `xdotool windowmove --sync` **hangs** against a stalling
  emulator; never use `--sync` here. `couch-play.sh` therefore *reports* the actual seating instead
  of assuming it.
- **Gamepad binding follows the instance, not the screen position.** `pads[0].ID` indexes detected
  gamepads, so instA claims gamepad 0 wherever its window lands. Whoever holds pad 1 plays instA's
  window even if they are sitting at the other screen — which is exactly the confusion that made a
  player "swap sides" mid-match. Read the seating table `couch-play.sh` prints.
- **Each DualSense exposes a motion-sensor joystick too**, so two pads produce **four** `/dev/input/js*`
  devices (`js0`/`js2` real, `js1`/`js3` sensors). Harmless — `glfwJoystickIsGamepad()` filters the
  sensors out, so gamepad indices 0 and 1 are the two real pads — but do not panic at the count.
- **The sides genuinely swap at START SETUP** (instA goes to waiting, instB gets COUNTRY/COLOR).
  That is the game's behaviour, not a bug, and it compounds the pad-mapping confusion above.

---

## ✅ Session 6 — the ~1 fps is root-caused and fixed; link-setup speed is the new frontier

### It was never the Deck, and never the network

- **Both machines** are slow on the main menu when a peer is connected, and **instantly fine when
  either end is killed**. Deck CPU, the build, GL (hardware `amdgpu`, dynarec on) and the network
  are all cleared.
- Network measured under the link's real traffic shape (4-byte ping-pong, `TCP_NODELAY`):
  **median 2.7 ms, p90 3.6, p99 6.6, max 11.3**. `STALL_US=15000` covers the worst case ~1.3x over.
  Idle ICMP shows a scary ~84 ms average purely from **wifi power-save**; it does not bite under
  continuous traffic. Don't chase it. (`tools/`-adjacent probe: see session log.)

### Root cause

The program polls `SIO_STAT` from the **main menu** onward just to notice a link partner. A peer
that is connected but not transferring never answers, so the wall-clock stall in `pumpTransport()`
**runs its budget out in full, every time**. Cost is `(stalls/s) x budget`, paid on screens that
are not linking at all:

| `STALL_US` | stalls / 2 s | stall share of wall | SPEED |
|---|---|---|---|
| 15000 | 132 | **96.8%** | 26.9% |
| 400 | 484 | 9.6% | 100.2% |

The Deck looked far worse than the desktop only because this desktop emulates **~8x realtime**, so
3.2% of the wall clock still yields 27%; the Deck has almost no headroom.

### The fix (in `sio1.cc`, `pumpTransport()`)

Adaptive backoff: consecutive stalls that expire with nothing received drop the budget to
`PCSX_LINK_STALL_FLOOR_US` (default 200) after `PCSX_LINK_STALL_BACKOFF_AFTER` (default 8)
timeouts; **any received byte resets it**, so a live handshake keeps the full round-trip tolerance.

- Menu at `STALL_US=15000`: **26.9% → 100.2%** (60 fps), stall share 96.8% → 4.8%.
- **Handshake still intact**: `run-full-test.py` reaches match options with the guest showing
  identical settings and only `GO BACK` — the session-4 proof of a real link.
- Deployed to Deck #1 (md5 `88a06a18…`; old binary kept as `pcsx-redux.session5-backup`).

### The instrument that settled it — use this, not screen capture

A `SPEED` line every ~2 s of **wall** time (an emulated-time trigger reports as rarely as the bug
allows), carrying emulated speed, emuFps, pumps, uvRuns, stalls, stallMs and % of wall, budget and
timeout count. Solo baseline: `SPEED 100% emuFps=60 pumps=0`. Wired from `psxcounters.cc` vblank via
`m_sio1->speedTick()`.

**Trap that cost real time:** ffmpeg distinct-frame counting is only valid on a screen **with
motion**. Retaliation's title and main menu are **static** — ~4 distinct frames / 6 s at *any*
speed, so a solo instance with no peer scores identically to a collapsed one. An apparent local
reproduction was that artifact. Attract footage reads valid (~58 fps).

### ⛔ The decisive result: the wall-clock stall DEADLOCKS in a live game over LAN

Deck↔desktop reached **actual gameplay** (units on screen, combat, $10000 each), played briefly, then
wedged. Both sides, symmetrically:

| | Desktop | Deck |
|---|---|---|
| SPEED / emuFps | 0.44% / 0.27 | 0.45% / 0.27 |
| stalls per window (**all timing out**) | 251 | 247 |
| stall share of wall | 99.9% | 99.9% |
| `awaiting` | **1** | **1** |
| socket | ESTAB | ESTAB |

Socket healthy, so this is **not** a crash, a dropped link, or the network. It is a mutual deadlock:
the stall freezes emulated time while waiting for the peer's byte, but that byte can only be produced
by the peer advancing *its* emulated time — and the peer is frozen waiting for us. Each side withholds
exactly the progress the other needs. Loopback survives only because its RTT is small enough that one
side escapes first; at ms RTT with ~95% stall duty they lock.

**Do not try to fix this with budgets, caps or backoff.** Four attempts this session, each broke the
link elsewhere:

| Attempt | Result |
|---|---|
| Share cap 30% of wall | handshake failed outright |
| Backoff to 200 µs after 8 timeouts | menu fast, link died at side select (self-reinforcing: a starting transfer's replies land after the floor expires) |
| Gate on TX outstanding via `transmitData()` | `tx=0` — wrong hook, off the hot path; suppressed every stall |
| Full-budget probe every 16th stall | setup survived, then in-game "CONNECTION LOST"; probes alone cost 94% of wall |

**Recommended direction — never wait on the peer's *emulation*, only on the network.** Synthesise the
mechanical flow-control ack locally the moment a block arrives, rather than waiting for the peer's
emulated driver to toggle DTR and send it back. A wait then costs one network RTT and cannot depend on
a frozen peer clock, so the deadlock becomes structurally impossible. `transmitMessage()`
(`sio1.cc:364`) is the single outbound choke point, and `processMessage` already distinguishes
`DataTransfer` from `FlowControl` — that distinction is also the only honest idle-vs-transfer
discriminator (idle menus produce flow-control trickle only).

### Transport-level ack (`PCSX_LINK_LOCAL_ACK=1`) — directionally right, not yet sufficient

Acknowledging in the transport the moment a block lands (`sendSyntheticAck()`, toggling the DSR edge
without waiting for the emulated driver) **removes the setup livelock and roughly triples speed**:

| | before | with local ack |
|---|---|---|
| instA (host) | 35% / 20 fps | **75% / 45 fps** |
| instB (guest) | 17.5% / 10 fps | **45% / 27 fps** |

Handshake still completes (guest reaches match options with only `GO BACK`). **But on match start the
guest desyncs** — it falls back to COUNTRY/COLOR while the host plays on alone at 60 fps. Expected
failure mode: the host is told a block was consumed before the guest's driver actually consumed it, so
the two diverge exactly at the start handoff. `maxfifo` went 4 → **55**, which is the guest's backlog
growing as the host races ahead — the divergence made visible.

**Windowed ack (`PCSX_LINK_ACK_WINDOW`, default 8) — tried, and this is where it stands.** Acking only
while the receive backlog is within the hardware FIFO depth **fixed the desync and made setup healthy
on both sides**: instA **85% / 49 fps**, instB **55% / 33 fps**, `timeouts` ~0, both sitting stably on
match options (against 35% / 17.5% before, and against the desync the unwindowed version caused).

**But match start still livelocks.** On pressing ✕ both sides drop to **0.44% / 0.27 fps**, 99.9% of
wall stalled, every stall timing out, and `acks=0` — no data arrives, so no acknowledgement is issued,
so nothing can restart. The window bounds divergence but cannot prevent the wedge at the transition,
where both sides simultaneously need output the other can only produce by advancing a frozen clock.

**Conclusion: the acknowledgement family is exhausted.** It bought a 2.5x speedup and a stable, fast
link setup, and it cannot carry the match-start handoff. Getting past that needs a different sync
model — speculative execution with rollback (GGPO-style), i.e. run ahead on predicted peer input and
rewind on mismatch — which is a substantially larger project than anything in this file, and would
likely mean driving sync above the SIO1 register layer rather than inside it.

**Superseded idea, kept so it is not retried:** make the synthetic ack *windowed* — only issue it while
the receive backlog is below a threshold (`m_sio1fifo->size() < N`), so the peer can never run further
ahead than the FIFO can honestly absorb, and fall back to driver-produced acks beyond that. That keeps
the livelock cure while bounding divergence. Knobs already in place: `PCSX_LINK_LOCAL_ACK`,
`PCSX_LINK_ACK_BLOCK` (default 4).

**Default is OFF**, so the deployed Deck binary behaves exactly as documented above unless the env var
is set.

### Still open — link setup itself

While data genuinely flows, `timeouts=0`, the full budget stays granted, and stalls still consume
**94–96% of wall**: instA 35%, instB 16% on loopback. Average stall ~475 µs against a 76 µs loopback
RTT, because **both sides stall and each delays the other's reply**. That mutual starvation, not the
network, is what makes the link/map-select screens choppy. The spin loop also issues up to ~117M
`uv_run` calls per 2 s, which is what pins an `iou-sqp` kernel thread at 100% CPU.

Next: attack the mutual-starvation feedback (a stall that yields to the peer, or a bounded blocking
wait instead of a `UV_RUN_NOWAIT` spin), then measure **gameplay** speed separately — in-game
traffic is lighter than the handshake and may already be playable.

### Build/deploy when only the binary changes

`make -j4` in the container (**not** `make appimage` — it needs FUSE and fails at packaging), then
`tools/install-build.sh`, then scp that one binary to the Deck. `install-build.sh` re-applies the
`RUNPATH=$ORIGIN/../lib` patch linuxdeploy would have added — without it the raw binary dies on
`libcapstone.so.4`. Plain `make` keeps debug symbols so the binary is ~358 MB, not ~14 MB.

**SSH to the Deck is `deck@steamdeck`**, not `gibbo101@` (there is no `~/.ssh/config`).

---

## ⏳ Session 5 — on the Deck: the link HOLDS over real LAN, but runs at ~1 fps

Deployed to `steamdeck` (Deck #1) and got a real-network link to *hold* in setup instead of
bouncing to the menu. It is not yet playable — the Deck runs at ~1 fps with stuttering audio —
**but there is a confounder that must be cleared before judging the approach (see below).**

### Deployment (done, and repeatable)

Package lives at `~/ps1-lan-link/` on `steamdeck`, built from `deploy/deck/` here:

| On the Deck | What |
|---|---|
| `emu/squashfs-root/` | the patched AppImage **extracted** (SteamOS has no libfuse2 — never run the AppImage directly; `setup.sh` extracts it) |
| `bios/scph7001.bin`, `roms/retaliation-allies.{bin,cue}` | assets (ROM is byte-identical to the Deck's own RetroDeck `.ecm`, just decoded — PCSX-Redux can't load `.ecm`) |
| `home/.config/pcsx-redux/pcsx.json` | isolated config; SIO1 client/server + Raw mode baked in |
| `ps1-link.sh` | **single Game Mode entry point.** Reads `link.conf`, stamps ROLE/HOST/PORT into the config + exports `PCSX_LINK_STALL_US`, launches into the game, logs to `~/ps1-lan-link/run.log` |
| `link.conf` | `ROLE`/`HOST`/`PORT`/`STALL_US` — the one file to edit to retune or change peer |
| `setup.sh` | one-time: extracts the AppImage |

- **Game Mode shortcut "PS1 LAN Link" is registered** via `steamos-add-to-steam <desktop file>` on
  the active account (`userdata/<steam-id>`). It launches from Game Mode and reaches the game.
- **Redeploy:** `rsync deploy/deck/ + the AppImage` to the Deck, then `bash setup.sh` re-extracts.
  Desktop peer: `DISPLAY=:1 STALL_US=15000 repro/run-server.sh` (server, binds `0.0.0.0:6699`).

### Network — the early RTT scare was environmental, not the code

- First pings: 90–250 ms, 6% loss. Cause: a **wifi extender** (`VM5424517_5G_EXT`, −70 dBm) plus a
  **stale Tailscale route** forcing a DERP relay. After switching wifi: **direct LAN RTT
  min 2.8 / avg 4.7 / max 15.7 ms, 0% loss**; Tailscale direct 3 ms.
- **The link now targets the LAN IP (`192.168.0.106`), not Tailscale.** Same-LAN play should not
  pay WireGuard's per-packet CPU + jitter (and CPU is what the stalling Deck is short of).
  Tailscale stays as the **SSH/deploy control channel only**.

### The fix that changed behaviour — and the new wall

- `kLinkStallUs` was **400 µs**, tuned for loopback (0.076 ms) — **~10× too small for 4–5 ms LAN
  RTT**. The blocked SIO poll gave up before the peer's byte crossed the wire, emulated time ran
  past the driver's tolerance → handshake died → "back to main menu."
- `kLinkStallUs` and `kPollingWindowCycles` are now **runtime-tunable** via `PCSX_LINK_STALL_US` /
  `PCSX_LINK_POLL_CYCLES` (set from `link.conf` / `run-server.sh`). First `pumpTransport()` logs
  `LINK TUNING stallUs=… pollCycles=…` so you can confirm it's wired up. **Retuning is a config
  edit, not a rebuild.** (Source: `pcsx-redux/src/core/sio1.cc` anonymous namespace + `pumpTransport`.)
- At **15000 µs** the link **no longer bounces — it holds in link setup.** Timeout fixed. But the
  Deck runs at **~1 fps, audio badly stuttering.**

### Mechanism, and the CONFOUNDER to clear FIRST

The stall aligns the two emulated clocks by busy-waiting *host* time on a blocked SIO poll. At
~5 ms RTT × ~182 round trips per 728-byte payload that is ~1 s of frozen wall-time per payload →
~1 fps. That is the tension: too-short stall → handshake times out; long-enough stall → it holds
but crawls.

**BUT the ~1 fps was measured with the desktop peer NOT actually in link setup** — the server had
just been restarted and the drive-to-LINK-GAME step was interrupted. A non-responding peer makes
every poll stall the *full* window (worst case). With both sides handshaking and answering, the
stall early-returns the instant data arrives, so it should cost only the real RTT, not the cap.
**Do not conclude the design is unviable until it is retested with both sides genuinely in link
setup.** ([[evidence-before-behaviour-change]] — don't infer the ceiling from a confounded run.)

Logging is *not* the cause (ruled out this session): only ~4,584 diag lines over ~50 s.

### Next steps (in order)

1. **Retest with BOTH sides in link setup, 15000 µs, LAN.** Drive the desktop server to LINK GAME
   team-select first (`repro/goto_link.py <WID>` — team-select is the no-timeout rendezvous), then
   join from the Deck. Measure the real fps when the peer responds. Make-or-break measurement.
2. If still ~1 fps with a live peer: **sweep `STALL_US`** (config edit, no rebuild) — 2000 / 4000 /
   8000 — for a point where the handshake still holds but speed recovers.
3. If a value holds: **get into the game** and measure *gameplay* speed separately — the handshake
   is the heaviest link phase; in-game traffic may be lighter and playable even if setup crawls.
4. Build a **clean, non-instrumented release** for playtesting once the mechanism is settled (strip
   SIO1-DIAG). Keep instrumentation until then.
5. Only if no playable `STALL_US` exists, reconsider the design — wall-clock stalling may not scale
   to ms-RTT lockstep. **Do NOT touch the SIO registers** (ruled out across sessions 1–3).
6. **Second Deck:** `deck2` — the tester said he'd power it on for the two-Deck run; it's
   authorised for this project when he does. Deck↔Deck RTT will be similar (~5–10 ms).

### Gotchas added this session

- The AppImage process runs as comm **`AppRun`** (the symlink name), *not* `pcsx-redux` —
  `pkill -x pcsx-redux` silently misses it and you get a stale server holding 6699. Kill `AppRun`.
- **SteamOS has no libfuse2** — extract the AppImage, never run it directly.
- The **Deck sleeps aggressively**; SSH times out when idle — wake it before any scp/rsync/ssh.
- `steamos-add-to-steam <file.desktop>` registers a Game Mode shortcut from *within* Game Mode
  (Steam writes it via IPC, so it survives — unlike hand-editing `shortcuts.vdf` while Steam runs).

---

## ✅ Status: the link works

Two PCSX-Redux instances reach **a live linked game** and stay in it indefinitely. Verified
2026-07-24 on loopback:

- both sides reach the match-options screen showing **identical settings** (MAP 1, UNITS 6,
  TECH LEVEL 10, CREDITS 10000, BASES ON, ORE REGROWS ON, SHROUD REGROWS OFF, CRATES ON,
  CAPTURE FLAG OFF)
- host shows `✕ TO START`, guest shows only `GO BACK`
- host presses ✕ → **both** drop into gameplay on the same map, one gold, one purple, $10000 each
- ran continuously for **many minutes**, far past the 30 s bar
- link traffic sustained the whole time: **+54,948 / +54,927 DTR toggles in a 30 s sample**, both
  sides within 0.04% of each other — tight lockstep

### Why this is a real link and not two solo skirmishes

This was the trap to avoid, so the evidence is stated explicitly:

1. **The guest never had a start prompt.** It showed only `GO BACK`. It entered gameplay solely
   because the *host* pressed ✕. That transition can only have crossed the link.
2. The guest never configured the match options, yet displayed the host's values exactly.
3. Both sides run the same map with different player colours.
4. DTR handshake traffic continues throughout gameplay at ~1,800 toggles/second on both sides and
   stops the instant the link dies. A solo skirmish produces none.

### A note on the Select check (not load-bearing)

The original plan was to confirm the link by pressing **Select** in-game and seeing an "allied to
player" message. It did not appear on either side (taps, a held press, frame-by-frame video). Pad
input definitely reaches the game in that state (pressing Right moved the cursor: 9,492 pixels
changed), so Select is delivered but does not surface that message here — most likely the
expectation was wrong (it may need an actual alliance action, more than two players, or it appears
somewhere else). This is **not** a gap in the result: the link is proven independently and more
strongly by the four points above. Recorded only so nobody chases it as a real loose end.

---

## What actually fixed it

Two changes did essentially all the work, and neither is in the SIO register emulation that the
first three sessions concentrated on. **The protocol was fine; the transport was too slow.**

The driver acknowledges **every 4-byte block** with a DTR/DSR edge, so a 728-byte payload needs
~182 round trips. Anything above ~1 ms per round trip blows the driver's timeout.

| # | Fix | Effect on ack latency (median) |
|---|---|---|
| 5 | **`uv_tcp_nodelay()` was never called anywhere in the codebase.** Every link message is 4–6 bytes in a strict request/response pattern — a textbook Nagle victim, interacting with the peer's delayed ACK. | **98.7 ms → 5.3 ms** |
| 6 | **SIO1's socket lives on the main uv loop, which the UI pumps once per rendered frame.** Every handshake message was quantised to the frame period. Now also pumped from `SIO1::poll()` (every hsync), plus a bounded wall-clock stall while the game is spinning on `SIO_STAT`, so waiting costs host time rather than emulated time. | **5.3 ms → 0.076 ms** |

Together: **~1300× faster handshake**, and bytes transferred per attempt went 76 → 220 → 404 →
50,000+ (diagnostic cap).

Earlier sessions' defects 1–4 were real fidelity bugs but none of them was the blocker. Defect 1
was actively fixed in the *wrong direction* — see FINDINGS.

### Also changed this session

- **RX pacing arithmetic** (defect 4 was recorded as fixed but never was — the handover's own
  verification command returned 22, not 0, and the FIFO reached 16 against a hardware depth of 8).
  Idle time was banking unbounded delivery credit. Fixed, but it **cannot bind at the game's
  runtime baud** (2 cycles/byte) and did not affect the outcome. Pacing is a red herring — again.
- **TX flow control.** Bytes written while CTS is low are now held in order and released when the
  peer asserts RTS, instead of being put on the wire early. Never dropped. This removed all
  `CTS=0` transmits (was 20/92 and 4/8) and all `CR_RESET` data loss.

All changes are against upstream `55fbf046`:

| Patch | Contents |
|---|---|
| `patches/link-working-session4.patch` | **everything — use this one** |
| `patches/sio1-session3.patch` | `sio1.cc` + `sio1.h` only (kept for continuity) |
| `patches/uvfile-nodelay-session4.patch` | the one-line `uv_tcp_nodelay()` fix, upstreamable on its own |

---

## Reproducing it

```bash
cd ~/Documents/development/ps1-lan-link
./repro/run-link-test.sh                 # clean pair; refuses to start a mixed one
```

Then, **in this order** (the whole sequence is automated in `repro/run-full-test.py`):

1. drive **instB** (right window) to the main menu → `Up` → `✕`  → it sits on WAITING TO CONNECT
2. drive **instA** (left window) to the main menu → `Up` → `✕`  → it gets COUNTRY/COLOR
3. `✕` on **instA** (START SETUP) — the sides then **swap**: instA waits, instB gets COUNTRY/COLOR
4. `✕` on **instB** (START SETUP) — both reach match options
5. `✕` on the host to start the game

**Both sides must press START SETUP.** Missing the second press was why several earlier attempts
died at the match-options screen.

### Driving the emulator — corrections to earlier handovers

- **`xdotool key --window <id>` does not work.** It uses `XSendEvent`, which GLFW ignores; the
  keypress is silently dropped. The session-3 handover records this method as verified — it is not.
  Use `xdotool windowactivate --sync <id>` then `xdotool key <k>` (XTEST, a real input event).
  Simultaneity across both windows is lost but is not required.
- **Menu path:** Start → intro movie → *wait for the black transition* → Start → main menu →
  `Up` (wraps CAMPAIGNS → LINK GAME) → `✕`. Pressing Start on the title starts the intro and Start
  on the intro returns to the title, so a fixed-delay sequence just oscillates between them. Wait
  for black specifically.
- **Team select (COUNTRY / COLOR) has no idle timeout** — the rendezvous point. Every other menu
  screen falls back to the attract loop.
- `repro/nav.py` classifies screens by mean RGB. **These signatures depend on window size and on
  whether the ImGui debug windows are shown** — recalibrate after changing either. Current values
  are for a 900×700 window with `ShowLog`/`ShowSIO1` off: title `(66,65,58)`, menu `(43,16,7)`,
  black `(2,2,2)`. Navigation is somewhat flaky; retry before assuming a real failure.
- **Edit instance configs only while the emulators are stopped** — they rewrite `pcsx.json` on
  exit and will silently overwrite your changes.

---

## Ruled out — do NOT re-litigate

Each looked convincing and was killed by measurement. Details and the killing evidence are in
FINDINGS.

- **RX baud pacing / burst delivery.** Fixed properly this session; changed nothing. Cannot bind at
  the game's runtime baud. Third time this has been chased.
- **Stale/corrupt bytes in the RX FIFO.** The transmitted and consumed byte streams are
  **byte-identical over their overlap** — zero corruption, zero loss, zero reordering. The receiver
  was merely behind.
- **A length disagreement between the sides.** The descriptor traces are in exact lockstep — same
  lengths, same buffer addresses, same order.
- **"Both sides are in `write()` simultaneously"** as a root cause. They do end up there, but only
  *after* the timeout, as a consequence of the slow transport.
- Anti-phase DSR, the DSR latch, `CR_RESET` destroying buffered bytes, initial DSR state, message
  framing, RXEN drops, lockstep/clock drift (sessions 1–3).

**"Network latency" was wrongly ruled out in session 1.** The LAN was never the issue; the *local*
TCP stack was. That mistake cost three sessions.

---

## Next steps

1. **Two machines over Tailscale.** Set `CLIENT_HOST` to the server's Tailscale IP. Real-network
   latency is the whole ballgame now: the handshake needs ~182 round trips per 728-byte payload, so
   anything above ~1 ms RTT will likely reintroduce the timeout. If it fails there, the fix is to
   raise the per-round-trip tolerance, not to touch the SIO registers.
2. **Then the Steam Decks.** (`deck2` is off-limits unless the tester asks.)
3. **Upstream.** `uv_tcp_nodelay()` is a clean standalone fix worth submitting on its own — it will
   speed up *any* SIO1 link use, not just this game. Defects 1–4 are separately worth upstreaming;
   note that defect 1's original fix should be revised, not kept as-is.
4. Work out what actually surfaces the Select ally message.

---

## Gotchas that cost real time

- **`-j4` and `--cpus=4 --memory=12g`, never `-j20`.** `-j20` OOM-killed the machine.
- **`make appimage`**, not plain `make`.
- **`-stdout` + `stdbuf -o0`** or the log file stays empty.
- **Do not pipe `run-link-test.sh` through a capturing parent.** The launched emulators inherit the
  pipe, so a `subprocess.run(capture_output=True)` waits forever on an EOF that never comes and the
  script looks hung. Redirect to a file inside the shell instead.
- **Never infer absence from a sampled or capped counter.** This has now produced *five* confidently
  wrong conclusions across four sessions, including "DTR is never asserted" (it is *toggled*, so a
  periodic sample lands on whichever parity is current).
- **Verify your own change is actually wired up, then verify it did something.** Defect 4 was
  declared fixed, had a verification command written for it, and that command was never re-run —
  it returned 22 when 0 meant success.
- **Logs only truncate on relaunch.** Driving already-running instances appends.
- **A stale instance can silently hijack the test.** Always check `ps -o pid,etime -C pcsx-redux`
  shows exactly two, both seconds old, and that the 6699 listener is one of them.
- The Deck sleeps; wake it before any `scp`/`rsync`.

---

## What's on disk

| Path | What |
|---|---|
| `pcsx-redux/` | clone + submodules + built AppImage (fixed + instrumented) |
| `work/bios/scph7001.bin` | NTSC-U BIOS, md5 `1e68c231d0896b7eadcad1d7d8e76129` |
| `work/roms/retaliation-allies.{bin,cue}` | decoded disc |
| `work/exe/SLUS_00665.exe` | extracted PSX-EXE for disassembly, md5 `1cce7bdaff0eb351ebf0c05c70c7a142`. Loads at `0x80010000`; file offset = `addr - 0x80010000 + 0x800` |
| `tools/mipsdis.py` | `python mipsdis.py 0x80154038 20 "label"` — needs capstone in `venv/`. Run it from `work/exe/`. |
| `tools/emuspeed.py` | emulation speed from `t=`/`cyc=` pairs in a log (beware multi-run logs: cycles reset) |
| `tools/install-build.sh` | install a fresh binary into the extracted tree + re-apply the RUNPATH patch |
| `tools/framerate.sh`, `tools/framerate-region.sh` | distinct-frame rate of a window/region — **only valid on a moving screen** |
| `repro/stall-sweep.sh` | launch a loopback pair at a given `STALL_US`, confirm tuning, report speed |
| `repro/menu-collapse-test.sh` | one-command menu-vs-title comparison on a loopback pair |
| `repro/to-menu.py` | drive one instance to the main menu, raising it before each capture |
| `repro/run-link-test.sh` | clean pair launcher (kills `AppRun` *and* `pcsx-redux` — see gotchas) |
| `repro/run-full-test.py` | end-to-end: relaunch, navigate both, start setup on both, report |
| `repro/nav.py`, `repro/goto_link.py`, `repro/drive.sh` | screen classification and window driving |
| `patches/link-working-session4.patch` | **all fixes** |
| `docs/FINDINGS.md` | full evidence trail incl. corrected wrong turns |
| `docs/DESIGN-sio1-latency.md` | superseded; kept for the record |

Keyboard → pad: arrows = D-pad · **X**=✕ · **D**=○ · **S**=△ · **Z**=□ · **Enter**=Start ·
**Backspace**=Select.
