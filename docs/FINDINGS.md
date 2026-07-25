# PS1 Link-Cable over LAN — Findings

**Goal:** Play the PS1 games that supported the console-to-console **Link Cable** —
*C&C: Red Alert Retaliation*, *C&C: Red Alert*, *Dune 2000* — between two people on
**two separate machines over LAN** (ultimately two Steam Decks running RetroDeck,
over Tailscale). No emulator offers this out of the box; this project is figuring out
whether it can be made to work and building it.

Date started: 2026-07-23. Machine: `the desktop PC` (X11 `DISPLAY=:1`, 5120×1440).

---

## TL;DR

- **Architecturally possible, and we proved the whole pipeline** end-to-end except the
  final serial handshake.
- The link cable is the PS1 **SIO1** serial port. **PCSX-Redux** is the *only* viable
  base: it emulates SIO1 and relays it over a **TCP socket**, is open-source, and runs
  natively on Linux/Deck. Everything else is a dead end for LAN (see below).
- We got two PCSX-Redux instances to boot Retaliation, **establish the TCP link**
  (server logs `SIO1 client connected`), and reach C&C's country/side **setup screen**,
  then fire the handshake.
- **The handshake begins then drops back to the menu, every time.** Root cause is now
  understood (below): PCSX-Redux relays serial data + flow-control faithfully but does
  **not cycle-lock the two emulators**, so they drift and C&C's tight handshake timing
  window breaks.
- **The fix is a fork** of PCSX-Redux adding a **lockstep mode to SIO1**. Tractable but a
  real multi-session dev project.

---

## Why other options don't work (for LAN)

| Emulator | Link cable? | LAN? | Verdict |
|---|---|---|---|
| **PCSX-Redux** | Yes (SIO1, TCP relay) | **Yes** | Only viable base. Native Linux, open source. |
| no$psx | Yes (these exact games) | **No** (2 windows, 1 PC) | Confirms games *can* link under emulation; Windows-only (Wine). Control test only. |
| XEBRA | Yes | No (single machine) | Windows, closed source. |
| DuckStation / SwanStation | **No** | — | No SIO1 link support. |
| RetroArch cores (Beetle PSX, PCSX-ReARMed) | **No** | — | RetroArch *netplay* shares ONE console's state between 2 pads — it is **not** a link cable (which is two separate consoles). Fundamentally can't do this. |

RetroDeck (on the Decks) uses the RetroArch cores + standalone DuckStation → **cannot**
do link cable. The plan is a **standalone PCSX-Redux alongside RetroDeck**, not inside it.

---

## What we built / proved (reproducible)

### Environment
- PCSX-Redux Linux AppImage, build **291**, version `55fbf046` (built 2026-07-21).
  - Official download is a JS SPA. The real artifact URL is reached via manifests:
    - `https://distrib.app/storage/manifests/pcsx-redux/dev-linux-x64/manifest.json`
      → lists build ids; take the highest → `manifest-<id>.json` → `.path` +
      `.hashes.md5` → download from `https://distrib.app<path>` (a `.zip` containing
      `PCSX-Redux-HEAD-x86_64.AppImage`).
  - GitHub CI artifacts are **macOS/OpenBIOS/tests only** — no Linux AppImage there.
  - AppImage runs directly (FUSE present) or via `--appimage-extract` →
    `squashfs-root/usr/bin/pcsx-redux`.

### Assets (the user's own dumps, pulled from `steamdeck` over Tailscale)
- **BIOS:** `scph7001.bin` (NTSC-U, 512 KB, md5 `1e68c231d0896b7eadcad1d7d8e76129`) from
  `~/retrodeck/bios/`.
- **Game:** Retaliation Allies disc `SLUS-00665`, stored on the Deck as
  `...Retaliation - Allies Disc [NTSC-U] [SLUS-00665].bin.ecm` (ECM-compressed) in
  `~/retrodeck/roms/psx/`. (Soviets disc = `SLUS-00667`.)
  - RetroDeck's RetroArch cores read `.ecm` directly; **standalone PCSX-Redux does not** —
    must decode to `.bin` + `.cue`.
  - Decoded with `ecm2bin` (built from `github.com/alucryd/ecm-tools`; the binary
    dispatches encode/decode by argv0 name — symlink `ecm2bin` → `bin2ecm`).
    557 MB `.ecm` → **592 MiB `.bin`** (valid: starts with CD sync `00 FF..FF 00`,
    620,645,760 bytes = 263,880 × 2352). Hand-wrote a single-track cue:
    `FILE "...bin" BINARY / TRACK 01 MODE2/2352 / INDEX 01 00:00:00`.

### Two-instance SIO1 link setup
- SIO1 settings live in `pcsx.json` under `emulator.Debug`:
  `SIO1Server` (bool), `SIO1ServerPort` (6699), `SIO1Client` (bool),
  `SIO1Clienthost` (string), `SIO1ClientPort` (6699), `SIO1Mode` (0 = redux/protobuf;
  the other is "raw" for real hardware and the **client doesn't support raw**).
  Show the link window with `gui.ShowSIO1 = true`.
- Config path is `$XDG_CONFIG_HOME/pcsx-redux/pcsx.json` (default `~/.config`). To run
  **two isolated instances**, give each its own `HOME` **and** `XDG_CONFIG_HOME` (also
  keeps memcards/shaders separate, since those are CWD-relative).
- Launch flags: `-bios <path> -iso <cue> -run` (also `-loadiso`/`-disk`; `-fastboot`).
- Server config: `SIO1Server=true`. Client config: `SIO1Client=true`,
  `SIO1Clienthost="127.0.0.1"`. **Start the server first**, then the client.
- See `repro/` for the config generator and launch scripts.

### Keyboard → PS1 pad (default mapping)
Arrows = D-pad · **X** = ✕Cross · **D** = ○Circle · **S** = △Triangle · **Z** = □Square ·
**Enter** = Start · **Backspace** = Select · Q/R = L1/R1 · A/F = L2/R2.
Input goes to the **focused** window only (keyboard). A physical **controller** is polled
globally by GLFW regardless of focus (so one pad drives both windows).

### Result of the link attempt
- ✅ Both boot Retaliation on the real BIOS.
- ✅ TCP link **ESTABLISHED** (`ss` shows two conns on 6699); server log: `SIO1 client connected`.
- ✅ Both reach main menu → **LINK GAME** → country/color **setup** (England vs Russia) →
  **START SETUP** (✕).
- ❌ Handshake starts then **drops to main menu**. The **TCP socket never dies** — it's a
  serial-protocol/timing failure, not a connection failure.
- Things tried that did **not** fix it: firing START on both within ~50 ms via `xdotool`
  (rules out menu-entry timing); `gui.IdleSwapInterval = 0` to stop the unfocused window
  render-throttling (rules out focus-throttle as the sole cause).

---

## Root cause (read from source)

Source: `src/core/sio1.cc`, `src/core/sio1-server.cc`, `src/core/sio1.h`.

- The relay is a **protobuf protocol** carrying serial **data + flow-control** fields. It
  **does** propagate control lines (DSR/CTS) across the link, peer-driven — status bits
  reflect the remote side, not faked locally. RX-ready tracks the FIFO; TX-ready is local.
- Incoming bytes are delivered to the CPU via **IRQ**, timed by a **baud-rate timer**
  (`m_cycleCount = psxClockSpeed / (baudRate * 8)`).
- **Critical gap:** the emulator **never stalls the CPU to wait for the peer.** Each
  instance **free-runs**; it presents whatever is buffered and sends flow-control messages
  hoping the peer throttles. There is **no cycle-level lockstep**.
- Consequence: two independently-running emulators drift in instantaneous speed (OS
  scheduling, GPU, focus). C&C's link handshake expects responses inside a tight serial
  window; the drift blows it → bail to menu. This is *continuous* drift over the multi-
  second handshake, which is why one-shot timing fixes (simultaneous entry, no-throttle)
  don't help.

---

## The fix (fork scope)

Fork `grumpycoders/pcsx-redux` → `gibbo101/pcsx-redux`, add a **lockstep / blocking
flow-control mode** to SIO1: the transmitting instance **blocks (or the emulation stalls)
until the peer has consumed/acked** the serial data, so neither console races ahead of the
byte stream. The protobuf protocol and flow-control plumbing already exist — this builds a
sync layer on an existing foundation rather than from scratch.

**Risks / unknowns:**
- Introducing blocking into timing-sensitive core code may cause stutter; may need a
  bounded look-ahead window rather than strict per-byte lockstep.
- C&C may have *further* timing assumptions beyond the handshake (in-match sync).
- Upstream is a PS1 *research* emulator — a clean lockstep mode could be a welcome PR.

**Alternative if the fork stalls:** no$psx control test (confirms the games' link logic
works under emulation at all; local-only, Wine) to isolate game-vs-sync — not a LAN path.

---

## Session 2 (same day) — built from source, instrumented, two real bugs fixed

Built PCSX-Redux from source (Docker: `ghcr.io/grumpycoders/pcsx-redux-build`). **Use
`--cpus=4 --memory=12g` and `-j4`** — `-j20` OOM-killed the machine and forced a reboot
(these TUs peak ~2 GB each). Run `make appimage` (not just `make`): the raw binary links
against container-only libs (`libcapstone.so.4`). Launch with **`-stdout` + `stdbuf -o0`**,
else `g_system->printf` output is block-buffered and the log file stays empty.

### Two genuine emulator bugs found and fixed (`src/core/sio1.cc`)

1. **TX bytes silently destroyed by a CTS gate.** `isTransmitReady()` required `SR_CTS`.
   Games *pulse* RTS, so the peer's CTS is low exactly when the game writes TX data, and the
   byte was dropped outright. Trace before: `game wrote DATA=ab ready=0 [TXEN=1 CTS=0 …]`
   — the entire `AB FE 00 00` handshake destroyed, **zero bytes ever reached the wire**.
   Fix: a transfer begins on a TX-data write with `TXEN` set; `/CTS` is a flow-control input,
   not a discard condition. After: `ready=1` on every write, traffic flows.
2. **RX bytes gated on the wrong bit.** `processMessage()` accepted a byte only
   `if (m_regs.control & CR_RTS)`. RTS is an *output* handshake line the game pulses;
   reception is enabled by **`CR_RXEN`** (which the game holds high throughout). Fix: gate on
   `CR_RXEN`.

Both are real fidelity bugs worth upstreaming regardless of whether C&C ends up working.

### A regression I caused (reverted)
Forcing `setDsr(true)/setCts(true)` on any connected peer made the **LINK GAME menu option
disappear** — the game saw a cable at boot and took a different path. Don't fake these; keep
DSR/CTS semantically accurate.

### The wire is NOT the problem (proven)
Message-level accounting, both directions:
`server TXMSG=79 ↔ client RXMSG=79`, `client TXMSG=63 ↔ server RXMSG=63`,
`declared == got` on every message, zero loss, zero framing desync. Delivery is perfect.

### Current state of the failure
Server transmits ~76 data bytes of a real protocol (`FF FF FF FF 00 00 00 00 39 74 …`, not
just the `AB FE` probe); the client replies with only **8** bytes then goes quiet; the server
times out and the link dies. Server status stays **`0005` (DSR=0)** — it never latches
"peer present", because the client asserts DTR only momentarily (`CTRL=0227 [DTR=1]`) and
flow-control messages are sent **on change only**, so pulses get flattened.
**Live hypothesis: the DSR latch.** Unconfirmed.

### Wire-format bug worth fixing
`FlowControl{false,false}` protobuf-encodes to **nothing** (defaults omitted), so the whole
payload serialises to a **0-byte message** (`TXMSG size=0`). The receiver decodes an empty
payload, `hasData()` is false, and it is read as "flow control, both lines low". So *"no
signal"* and *"both lines low"* are indistinguishable on the wire — fragile, and it makes
DSR/CTS state unreliable.

### Instrumentation lesson (cost us three wrong conclusions)
Sampled logging (`% 16`, `% 64`) and capped counters (`<= 20/30`) produced **false
negatives** that I read as real findings — most notably a bogus "the link is one-directional"
conclusion when the client had simply sent fewer than 16 bytes. Static counters also persist
per-process, so **restart the emulator to reset them**. Log failure events *uncapped*; sample
only high-volume success events, and never infer absence from a sampled counter.

## Session 3 — two more defects fixed, three hypotheses killed, blocker isolated

Method this session: instrument uncapped, measure, *then* change behaviour. It paid off — every
hypothesis carried in from session 2 turned out to be wrong, and each was killed by data rather
than argument.

### Hypotheses refuted (with the measurement that killed each)

1. **The DSR latch** (session 2's live lead). Client→server flow-control: `11×(0,0) 18×(0,1)
   9×(1,0) 17×(1,1)` sent, *identical counts and identical order* received — zero pulses lost. The
   server's DSR was held for 3.3 M cycles at a time with ~47,000 STAT polls during each window.
   The "server status stays `0005`" evidence came from `updateStat()`'s `<= 10` log cap; all ten
   reads happened at boot, before the handshake. **The cap, not the hardware, produced the finding.**
2. **`CR_RESET` discarding unread RX bytes as the root cause.** Instrumented both FIFO-discard
   paths with uncapped accounting. Result: **1 event, 4 bytes out of 92 accepted.** Real, but a
   symptom of the death rather than its cause.
3. **Initial DSR state causing anti-phase.** Both instances' first `game read STAT` is identical
   (`0005`, DSR=0) and lands on the *same CPU cycle*. They start in phase.

### Defect 3 — flow-control pulses coalesced in the drain loop

`poll()` runs on hsync and drained *every* queued message in one loop, applying each DSR/CTS edge
immediately. Caught in the act — four peer line states applied in a single CPU cycle:

```
cyc=4083552585  drainPos=1: peer dxr=1 xts=1 → CTS 0->1
cyc=4083552585  drainPos=2: peer dxr=0 xts=1 → DSR 1->0
cyc=4083552585  drainPos=3: peer dxr=0 xts=0 → CTS 1->0  held=0 cyc, 0 STAT reads
cyc=4083552585  drainPos=4: peer dxr=0 xts=1 → CTS 0->1
cyc=4083552626  game read STAT=0105  ← sees ONLY the final state
```

Games sample these lines by polling `SIO_STAT` between instructions, so a state that lasts zero
cycles cannot be observed. Immediately before this the sender had polled **22,120 times** waiting
for exactly this handshake, timed out, and sent its chunk 530 cycles early.

Fix: yield after each line change, resume next hsync. Real invisible pulses **2/1 → 0**.
Session 1's instinct ("a brief pulse can be flattened") was right about the mechanism and wrong
about the location — the flattening is on the *receiving* side, after delivery, not on the wire.

### Defect 4 — no baud-rate pacing on the receive path

Bytes were dumped into the RX FIFO as fast as TCP delivered them — four accepted in the same cycle,
FIFO growing to **16** (real hardware depth is 8). The sender's 4-byte chunk crossed in ~300 cycles
against ~347 µs on real hardware at 115200 baud, ~40× too fast, so the receiver's per-transaction
read cadence was buried.

Fix: one data byte per `poll()`. Measured after: **median 2146 cycles/byte** (~126 kbaud vs the
game's configured 115200), FIFO peak 16 → 8.

### Rendezvous — session 1's "ruled out" was invalid

Session 1 dismissed menu-entry timing after firing both STARTs within ~50 ms. That test ran *before*
defects 1–2 were fixed, when `ready=0` on every write and zero bytes reached the wire — it could not
have succeeded regardless of timing. Re-measured in session 3 using the boot flow-control exchange
as a shared clock reference: the two handshake windows were **completely disjoint**, instB running
8.3 s and giving up 394 ms *before* instA spoke at all. That is why both sides transmitted the
identical initiator sequence — each was retrying into a peer that was not listening.

Firing both STARTs with a **physical controller** (GLFW polls the pad globally regardless of window
focus) produces a genuine alternating conversation. The link still dies, so simultaneity was a real
problem but not the whole one.

### The remaining blocker — anti-phase DSR deadlock

Extracted the game executable from the disc (`tools/extract.py` → `work/exe/SLUS_00665.exe`) and
disassembled the stall loop with capstone:

```
80154044  lhu   $v0, 4($v0)           ; SIO_STAT
80154048  lw    $v1, ($s1)            ; remembered DSR level, $s1 = 0x801F85E8
8015404c  andi  $v0, $v0, 0x80        ; SR_DSR only
80154050  beq   $v0, $v1, 0x80154010  ; loop while DSR == remembered
```

A pure **edge detector on DSR**. CTS is never tested anywhere in it — every CTS-based theory was
chasing a bit this code does not read. The read loop at `80153ce4` fires on RXRDY with DSR=0 and
CTS=0, so reading requires neither line.

With every edge delivered (57/57), every edge individually visible, and bytes arriving at hardware
rate, both sides still converge on waiting for *opposite* edges — instA for a fall, instB for a
rise. The desync must therefore be in **when each side latches its remembered value**, not in
emulated line behaviour.

### Instrumentation lessons (now four wrong conclusions across three sessions)

Sampled/capped counters have produced a false negative in every session so far, including the whole
of session 2's DSR-latch lead. All caps in `sio1.cc` now emit a one-time `LOG CAP REACHED` line so
absence is never ambiguous. Two new traps found the hard way:
- **Logs only truncate on relaunch.** Driving the same running instances again appends, making
  counts cumulative — this corrupted one round of analysis.
- **Verify your own change is wired up.** A pacing fix was declared, reset, and checked but never
  incremented; a no-op that burned two test attempts before a same-cycle check exposed it.

---

# Session 4 (2026-07-24) — the protocol recovered by disassembly

Static analysis of the four driver entry points, costing no test cycles. This **supersedes the
"anti-phase DSR" framing entirely**: the DSR edge is not a line-level artefact, it is the
protocol's per-block acknowledgement, and the failure is a phase problem between the two sides.

## There are two descriptors, not one

Sessions 2–3 watched a single 4-word struct at `0x801F85DC` and called it "the link state". There
are in fact **two parallel descriptors**, one per direction:

| Struct | Base | Armed by | Fields |
|---|---|---|---|
| **WRITE** | `0x801F85DC` | `0x80153dd0` | `{active, buf, remaining, dsrLatch}` |
| **READ** | `0x801F85EC` | `0x80153af0` | `{active, buf, remaining, —}` |

`0x801F85DC` — the struct both sides were observed "walking correctly" — is the **write**
descriptor. So that observation says both sides were *transmitting*, which is the whole problem.
The read descriptor had never been looked at.

## The dispatchers

Two dispatchers, each selecting a sync or async variant on `flags & 0x8000` (FCB `+0x0`):

| Dispatcher | `0x8000` set | `0x8000` clear |
|---|---|---|
| `0x80154ab0` (**read**) | `0x80153af0` — arm async: `CTRL \|= 0x800` (RX irq) + `\|= 0x20` (RTS) | `0x80153b50` — sync read |
| `0x80154afc` (**write**, requires `$a1 == 2`) | `0x80153dd0` — arm async: latch `STAT & 0x80` → `0x801F85E8`, `CTRL \|= 0x400` (TX irq) | `0x80153e34` — sync write |

The direction is settled by what each arm does to the hardware: the read arm asserts RTS and
enables the RX interrupt; the write arm latches DSR and enables the TX interrupt.

## The actual protocol: a per-block DTR/DSR handshake

Block size `$s5` comes from a 4-entry table at `0x801ACE30` = **`{1, 2, 4, 8}`**, indexed by
`(*0x801F85D2 & 0x300) >> 7`.

**Writer** (`0x80153e34`):
1. at each block start (`$s1 == 0`) latch `STAT & 0x80` → `[0x801F85E8]` (`80153f7c`–`80153f98`)
2. send `$s5` bytes, `buf++`, `remaining--`
3. at block end compare live DSR against the latch (`80153fe8`): `bne` → **if it already differs,
   skip the wait**; otherwise spin at `80154038` until it differs

**Reader** (`0x80153b50`):
1. abort if `STAT & 0x38` (parity/overrun/framing)
2. spin on `STAT & 2` (RXRDY), consume byte, `buf++`, `remaining--`, `$s0++`
3. **when `$s0 == $s5`: `CTRL ^= 2` — toggle DTR** (`80153d64`), reset `$s0`

So every block costs exactly one DTR toggle *from a peer that is inside a read call*. A writer
whose peer is not currently reading blocks forever — and would do so on real hardware too. The
game must therefore alternate phases, and the failure is that both sides were in `write()`
simultaneously.

## Refuted this session

- **"instB holds `ctrl=0205`, DTR=0, and never asserts DTR."** DTR is *toggled*, once per block.
  A periodic sample lands on whichever parity is current, so a DTR-low sample proves nothing. This
  is the fifth wrong conclusion from a sampled counter in this project.
- **"The block size is larger than the bytes transferred, so the toggle is never reached."**
  Killed by reading the table: block sizes are 1/2/4/8 bytes, and 68 bytes were read.
- **`STAT & 0x38` (parity/overrun/framing) aborting the read.** `SR_PARITYERR`, `SR_RXOVERRUN` and
  `SR_FRAMINGERR` appear in `sio1.cc` only where `CR_ACK` *clears* them — the emulator never sets
  them, so this branch cannot fire. Worth noting the emulator is unfaithful here (defect 4 showed
  the FIFO reaching 16 against a hardware depth of 8, which real hardware would flag as overrun),
  but it is not this bug.
- **CTRL readback losing the DTR bit**, which would corrupt the `xori` toggle. `writeCtrl16`
  mirrors `m_regs.control` to the hardware register, so readback is correct.

## Measured with the two-descriptor instrumentation

First run with both descriptors and every DTR edge logged. Verified pair: two processes seconds
old, listener among them, all three binary hashes identical.

**Refuted — "both sides are in `write()` simultaneously."** This was the headline conclusion of the
disassembly above and it is wrong. instA's RD descriptor walks while instB's WR descriptor walks:
the two sides *do* take complementary turns. Block size at runtime is **4** (`cfg=0205`), and DTR
toggles fire on both sides (A: 18, B: 2) at `pc=80153d50`, exactly the reader's per-block
acknowledgement. The handshake mechanism works.

**What actually happens.** instA aborts the link first, at `t=1138301.959`:

```
t=1138297.652  RD[buf=801ffbe0 cnt=4] fifo=8  ctrl=0227   <- read armed, 8 bytes already queued
t=1138297.657  reads 01 00 00 00, fifo 8 -> 4
t=1138297.667  DTR 1->0 (toggle #18) pc=80153d50          <- block acknowledged, read satisfied
t=1138301.956  game wrote CTRL=0255 [ACK=1 RESET=1]
t=1138301.959  RX LOSS: CR_RESET discarded 4 unread bytes
```

The read succeeds. 4.3 ms later the game resets the SIO (`ctrl |= 0x50`, the `0x801543e0`
handler) and throws away the 4 bytes still queued. instB's 724-byte write then advances exactly
one 4-byte block and stalls — it is waiting on a peer that has already given up. **The 724-byte
stall is a consequence of instA's abort, not a cause.**

### Defect 4 (RX baud pacing) was never actually fixed

The handover records it as fixed and confirmed. It is not. The handover's own check
(`grep -o "cyc=[0-9]* RX accept" | uniq -d | wc -l`, expected 0) returns **22**, and the RX FIFO
reached **16 bytes against a hardware depth of 8**:

```
RX accept #73 fifo=5 / #74 fifo=6 / #75 fifo=7 / #76 fifo=8   <- all at cyc=38633566728
DRAIN 4 messages in one poll
```

The counter *is* wired up this time; the arithmetic is wrong. `poll()` computed
`m_rxBytesAllowed = (now - m_lastRxDeliverCycle) / per` where `m_lastRxDeliverCycle` only advances
when a byte is delivered, so **idle time banks unbounded credit** (capped at 64, eight times the
FIFO depth) and any pause is followed by a burst. Fixed by clamping the backlog to
`per * kRxFifoDepth` and advancing the delivery clock by exactly one baud period per byte, so the
rate stays continuous instead of resetting on every byte.

This is the fifth wrong "fixed/ruled out" claim in the project's history, and the second where the
mechanism was declared working on the strength of a check that was never re-run afterwards.

### Adjacent defect, not yet changed

`interrupt()` discards the **entire** FIFO when `size() > 8`. Real hardware drops the incoming byte
and raises `SR_RXOVERRUN`. Left alone deliberately: one behaviour change at a time, and with
correct pacing an overrun should not arise. No `FIFO OVERFLOW` events fired in this run.

## Driving the emulator — corrections to the handover

- **`xdotool key --window <id>` does not work.** It uses `XSendEvent`, which GLFW ignores; the
  keypress is silently dropped. The handover records this method as verified — it is not. Use
  `xdotool windowactivate --sync <id>` followed by `xdotool key <k>`, which goes through XTEST as a
  real input event. Simultaneity across both windows is lost, but is not required.
- **Menu path from the title screen:** Start → intro movie → Start (only once the title has
  actually reappeared) → main menu → **Up** (wraps from CAMPAIGNS to LINK GAME) → ✕ → team select.
- **Team select (COUNTRY / COLOR / START SETUP) has no idle timeout**, so it is the correct
  rendezvous: park the first instance there while the second navigates. Every other menu screen
  falls back to the attract loop, which is what makes blind key sequences desynchronise.
- Screen classification by mean RGB: title `(49,48,43)`, main menu `(33,12,5)`, black `(1,1,1)`.
  The link screens share the menu's artwork, so mean colour alone cannot distinguish
  WAITING TO CONNECT or COUNTRY select from the menu — crop a text region for those.

## The pacing fix changed nothing — and could not have

Rebuilt with the corrected pacing and re-ran with both sides confirmed on START SETUP. The failure
reproduced **byte-for-byte identically**, with the roles swapped (this time instB burst and aborted,
same `cumulative lost=4 of 76 accepted`). The failure is fully deterministic.

The reason the fix cannot bind: the game reprograms the port to `baud=1` → **2 cycles per byte**.
Credit per hsync poll is then ~1000 bytes, clamped to 8, and the observed bursts are only 4 bytes —
the cap never binds. At that baud real hardware genuinely would deliver 4 bytes that fast. So
**pacing is confirmed a red herring**, independently re-deriving what session 3 suspected. The
change is kept because banking idle credit and a 64-byte cap against an 8-byte FIFO are wrong on
their own terms, but it is a fidelity fix, not this bug.

## The actual failure: a mutual write-wait deadlock

With both sides instrumented, the two logs are mirror images at the moment of failure:

```
instB:  DSR 1->0 held=3592401 cyc, game STAT reads while high=60456   (pc=80154038)
instA:  DSR 1->0 held=3611728 cyc, game STAT reads while high=60732   (pc=80154038)
```

**Both sides sit in the writer's block-end wait at `pc=80154038` at the same time for ~107 ms**,
each spinning ~60,000 STAT polls for a DSR edge that only a *reader* produces. Neither is reading.
Nothing breaks the deadlock except the driver's yield callback (`0x801F85D8`) timing out, which
takes the `0x80153e78` abort branch, delivers `EvSpTIMOUT`, and returns a short count. By then the
two sides are out of phase, the next read consumes a stale block, and the game issues
`ioctl(2, 0)` to reinitialise the port and gives up.

Note this supersedes the "complementary phases" observation earlier in this session: the sides *do*
alternate correctly for a while, then both end up writing at once. Both statements are true of
different moments, which is exactly why a single sampled snapshot was misleading in both directions.

### Why they end up writing at once: TX ignores CTS

The sync read asserts RTS on entry (`CTRL |= 0x20` at `80153c68`) and clears it on exit
(`CTRL &= ~0x20` at `80153d90`). Across the link cable RTS↔CTS are crossed, so **CTS is the
program's signal that the far end is currently inside a read call.** Measured this run:

| | DATA writes | with CTS=1 | **with CTS=0** |
|---|---|---|---|
| instA | 92 | 72 | **20** |
| instB | 8 | 4 | **4** |

Those CTS=0 writes are bytes put on the wire while the peer was not listening. They land in a FIFO
with no read armed, and become the stale block that the next read mis-consumes.

### Defect 1 was fixed in the wrong direction

Session 1 found `isTransmitReady()` gating TX on `SR_CTS`, observed `ready=0` on every write and
zero bytes on the wire, and removed the gate. The genuine bug there was narrower: a not-ready write
**silently destroyed the byte**. Deleting the flow control removed the symptom and the mechanism
together. It looked correct at the time only because CTS was never being asserted properly —
RXEN gating (defect 2) and coalesced line transitions (defect 3) were both still live, and both
were fixed later. Flow control demonstrably works now: 72 of instA's 92 writes already see CTS=1,
and CTS transitions appear throughout the log.

Reinstated as hardware expresses it: `TXRDY`/`TXRDY2` are withheld while CTS is low, so the game
blocks in its own `STAT & 5 == 5` wait at `0x80153f08` until the peer enters a read — and a byte
handed to the port is **always** transmitted, never dropped. Related: `SR_TXRDY` was previously
only ever set and never cleared, so the transmitter was never throttled at all.

## CTS gating: refuted as implemented, then corrected

Gating `TXRDY` on CTS did stop the early transmits (CTS=0 writes went 20→0 and 4→0), but it
**converted the corruption into a hard stall**: both sides ended at `stat=0000` with CTS never
rising, and the game rebooted. That is the "deadlock moved rather than fixed" outcome predicted
above. The model was wrong in one specific way — real flow control *holds* a byte until CTS
allows, it does not refuse to accept it. Replaced with an 8-byte TX holding buffer flushed on the
CTS edge. Bytes are never dropped and never sent early.

## The byte streams were never corrupted

Decisive measurement, from data already in the logs — no rebuild needed. Extract every
`game wrote DATA=` on the sender and every `game read DATA=` on the receiver, in order, and diff:

```
instA transmitted 76 bytes, instB consumed 68 bytes
first mismatch at index: NONE - streams identical over overlap
```

**Zero corruption, zero loss, zero reordering.** The receiver was simply 8 bytes behind. That kills
the stale-byte theory outright, and with it the whole "misaligned FIFO" line of reasoning. The
descriptor traces are likewise in exact lockstep — same lengths, same buffer addresses, same order:

```
instA:  WRITE(4)x13 -> WRITE(4) 80027510 -> WRITE(728) 801a9b84 -> WRITE(4) 801a9718
instB:  READ (4)x13 -> READ (4) 80027510 -> READ (728) 801a9b84 -> READ (4) 801a9718
                                                                -> WRITE(4) 801a9798
```

The protocol was working. The transport was too slow.

## Root cause: transport latency, not SIO emulation

The driver acknowledges **every 4-byte block** with a DTR/DSR edge, so the 728-byte payload needs
~182 round trips. Measuring the per-block acknowledgement directly:

```
instB DTR toggles (ack emitted)  median=98.71ms   p90=106.68ms
instA DSR edges  (ack received)  median=101.30ms  p90=239.97ms
instA byte writes                median=0.01ms    p90=98.68ms
```

Bytes *within* a block moved in 0.01 ms; every block then stalled ~100 ms. 182 blocks × 100 ms is
~18 seconds against a driver timeout of ~280 ms — the transfer could never finish.

### Defect 5 — Nagle

`uv_tcp_nodelay()` **is never called anywhere in the codebase**. Every link message is 4–6 bytes in
a strict request/response pattern, which is the textbook case where Nagle interacts with the peer's
delayed ACK to produce tens-to-hundreds of milliseconds per exchange. One line in
`UvFifo::startRead()` (the choke point for both the accepted and connected sockets):

**median 98.71 ms → 5.32 ms**, bytes per attempt 76 → 220, `CR_RESET` losses → 0.

### Defect 6 — the link socket is serviced once per rendered frame

`SIO1Server::startServer` / `startClient` are both passed `g_system->getLoop()` — the main loop,
which `UI::tick()` pumps once per frame. Every handshake message was quantised to the frame period,
which for a per-block protocol is the dominant cost. Fixed by pumping from `SIO1::poll()` (every
hsync), plus a bounded wall-clock stall while the program is spinning on `SIO_STAT`: on real
hardware an ack costs a byte time, across two processes it costs a host round trip, and if emulated
time advances meanwhile the program sees a delay its driver will not tolerate. Stalling keeps the
two clocks aligned; the bound means an unresponsive peer costs at most that much per scanline.

**median 5.32 ms → 0.076 ms.** Bytes per attempt 220 → 404 → 50,000+ (diagnostic cap).
DTR toggles 57 → 203,190.

Cumulative: **~1300× faster handshake.**

## Result

Both instances reach a live linked game and hold it indefinitely — identical match options on both,
host-only start prompt, gold vs purple players on the same map, and **+54,948 / +54,927 DTR toggles
in a 30 s sample** (within 0.04% of each other). Full verification, including why this cannot be two
solo skirmishes, is in `HANDOVER.md`.

## Scoreboard of refuted hypotheses

Worth keeping visible, because the ratio matters:

| Hypothesis | Killed by |
|---|---|
| Block size larger than bytes transferred | reading the table: `{1,2,4,8}` |
| `STAT & 0x38` error bits aborting the read | those bits are never set by the emulator |
| CTRL readback losing the DTR bit | `writeCtrl16` mirrors `m_regs.control` |
| "instB never asserts DTR" | DTR is *toggled*; a periodic sample lands on either parity |
| Both sides in `write()` simultaneously (as cause) | descriptor traces show correct alternation |
| RX pacing / burst delivery | fixed properly; changed nothing; cannot bind at the runtime baud |
| Stale/corrupt bytes in the FIFO | transmitted and consumed streams byte-identical |
| Length disagreement between the sides | descriptor traces in exact lockstep |
| CTS-gated `TXRDY` | converted corruption into a hard stall |

Nine refuted, two correct. The two that were correct were both found by *measuring the thing
itself* (latency distributions) rather than reasoning about the protocol.

## Open next steps
1. Two machines over Tailscale, then the Steam Decks. Real-network RTT is now the binding
   constraint: ~182 round trips per payload means anything above ~1 ms RTT likely reintroduces the
   timeout.
2. Upstream `uv_tcp_nodelay()` as a standalone fix — it benefits any SIO1 link use.
3. Work out what actually surfaces the in-game Select "allied to player" message; it did not appear
   here despite pad input demonstrably reaching the game.
2. If confirmed, find what puts the sides out of phase: the driver is only correct when one side
   reads while the other writes.
3. If loopback holds → test across two machines (Tailscale), then port to the Decks.
4. Upstream defects 1–4 to `grumpycoders/pcsx-redux` — all four are genuine fidelity bugs
   independent of whether Retaliation ends up working.
