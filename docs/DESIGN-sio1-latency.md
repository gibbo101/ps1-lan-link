# SIO1 link latency/sync — design & fix plan

> ## ⚠️ CORRECTION (2026-07-23, later same session)
>
> **The latency theory below is WRONG.** It claimed serial bytes cross the wire at ~60 Hz
> because the shared libuv loop is pumped once per frame at `ui.cc:79`. That is not what
> happens.
>
> `UvFifo` (the SIO1 data socket) inherits `UvThreadOp` and dispatches all socket work via
> `request(...)` onto **`UvThreadOp`'s dedicated IO thread** (`s_uvLoop`, `uvfile.cc:75-118`,
> running `uv_run(..., UV_RUN_DEFAULT)` continuously), handing data to the emulation thread
> through a `ConcurrentQueue<Slice>` + atomics (`uvfile.h:230-257`). The frame-pumped
> `g_system->getLoop()` is only used for the **listener** that accepts the connection.
> So wire latency is already sub-millisecond and is **not** frame-quantized.
>
> **Revised leading suspects — data loss, not latency** (all in `src/core/sio1.cc`):
> 1. **RX FIFO is discarded on overflow.** `interrupt()`: `if (m_sio1fifo->size() > 8)
>    m_sio1fifo.asA<Fifo>()->reset();` — every SIO1 interrupt, if more than 8 bytes are
>    buffered, **the entire buffer is thrown away**, destroying in-flight handshake packets.
> 2. **No TX backpressure.** `isTransmitReady()` needs `SR_TXRDY2`, which is set at reset and
>    **never cleared** — so the sender never throttles and can trivially outrun the peer's
>    8-byte FIFO, triggering (1).
> 3. **RX bytes silently dropped when RTS is clear.** `processMessage()` only keeps an arriving
>    byte `if (m_regs.control & CR_RTS)`; otherwise it is discarded with no buffering/overrun flag.
>
> This fits the evidence far better: the failure is **deterministic** (every attempt), and was
> unaffected by simultaneous entry and by `IdleSwapInterval=0` — both of which would have
> mattered if latency/scheduling were the cause.
>
> **Status:** instrumentation added to `sio1.cc` (overflow-discard, RTS-drop, TX/RX counters,
> logged via `g_system->printf` as `SIO1-DIAG:`) and built successfully. Awaiting a test run to
> confirm which suspect actually fires. Sections below are kept for the record.

## The precise problem (from source) — SUPERSEDED, see correction above

Data path for one serial byte, Protobuf mode (`src/core/sio1.cc`, `sio1-server.cc`,
`psxcounters.cc`, `ui.cc`):

- **TX:** game writes DATA → `writeData8` → `isTransmitReady()` (needs TXEN & peer-CTS &
  TXRDY2) → `transmitData` → `sendDataMessage` → protobuf-encode → `transmitMessage` writes
  size+bytes to `m_fifo` (the `UvFifo` wrapping the TCP socket). **Fire-and-forget.**
- **Wire:** bytes only actually move when the **shared libuv loop is pumped**. It's pumped at
  `src/core/ui.cc:79` — `uv_run(getLoop(), UV_RUN_NOWAIT)` — **once per UI iteration ≈ once
  per frame (~60 Hz)**. SIO1Server registers on that same shared loop
  (`startServer(g_system->getLoop(), …)`).
- **RX:** `SIO1::poll()` runs on **hSync** (`psxcounters.cc:229`, ~15.7 kHz emulated), reads
  `m_fifo`, `sio1StateMachine` → `decodeMessage` → `processMessage` → pushes byte into local
  `m_sio1fifo` and `receiveCallback` schedules an SIO1 IRQ `m_cycleCount` (~2352) cycles later.
  Game ISR reads DATA from `m_sio1fifo`.

### Why the handshake dies

`poll()` checks the FIFO 15 kHz, but the FIFO is only **fed/flushed at ~60 Hz** by the single
per-frame `uv_run`. So each serial hop incurs up to ~16 ms wall-clock quantization; a
send→respond→receive round-trip is ~2–4 hops ≈ 33–66 ms wall-clock.

Both emulators run at ~real-time, and during a handshake the game **busy-waits on the STATUS
register**, burning emulated cycles the whole time. 33 ms wall-clock ≈ **~1.1 M emulated
cycles** (33.8688 MHz). C&C expects a serial reply within a small serial-timed window (order
1–10× `m_cycleCount`, i.e. thousands of cycles). Reply arrives ~100–1000× too late (in
emulated-cycle terms) → timeout → bail to menu. This matches every observation:
- socket stays ESTABLISHED (transport fine),
- handshake *begins* (first bytes do cross),
- always drops (round-trip never lands inside the window),
- unaffected by simultaneous entry / `IdleSwapInterval=0` (those don't touch wire latency).

Secondary effect: ±1 frame **clock skew** between the two free-running emulators adds jitter
on top of the base latency.

## Fix — staged

### Stage 1 (dominant, smallest change): kill the wire-latency quantization
Make SIO1 bytes cross the socket with sub-millisecond latency instead of frame-quantized.

- **1A (quick prototype):** in `SIO1::poll()` (runs every hSync), pump the loop with
  `uv_run(getLoop(), UV_RUN_NOWAIT)` before reading the FIFO, and flush once right after
  `transmitMessage`. ~5 lines. Risk: shared loop → may fire unrelated callbacks (gdb/web) in
  CPU-exec context; acceptable for a prototype with those servers off. Expected: round-trip
  drops from ~33 ms to ~sub-ms (each hop ~1 hSync ≈ 64 µs).
- **1B (clean version):** give `SIO1Server` its **own `uv_loop` on a dedicated thread** running
  `UV_RUN_DEFAULT`, servicing the socket continuously. Emulation reads/writes a **thread-safe**
  FIFO (mutex or SPSC ring) fed/drained by that thread. Fully decouples serial latency from
  frame rate. This is the shippable form.

### Stage 2 (if residual jitter): bounded-skew lockstep
Stop either emulator from running more than a small window `W` of emulated cycles ahead of the
peer. Piggyback a monotonically-increasing **cycle counter** on the existing protobuf messages
(add a field, or a periodic heartbeat). If local cycles − peer cycles > `W`, **stall the
emulation thread** (wall-clock sleep; do **not** advance the PSX cycle counter) until the peer
catches up. Keeps the two serial clocks aligned so timing assumptions hold. `W` tuned to the
smallest value that doesn't tank throughput.

### Stage 3 (robustness / upstream-ability)
- Gate behind a setting (e.g. `SIO1Mode` gains a `LowLatency`/`Lockstep` variant, or a bool)
  so existing single-machine / real-hardware (`Raw`) use is untouched.
- Handle peer disconnect cleanly (already partly present: "unreliable connection" path).
- Bound the stall so a dead peer can't hang the emulator forever.

## Prototype order of attack
1. Baseline: build unmodified in Docker, run two instances on loopback, reproduce the drop.
2. Apply **1A**. Rebuild. Re-test Retaliation LINK GAME → START SETUP.
   - Handshake holds → latency was the whole story; proceed to harden as **1B**.
   - Still drops → add **Stage 2** bounded-skew; re-test.
3. Once loopback holds: test across two machines over Tailscale; then port to the Decks.

## Build notes
- `./dockermake.sh -j$(nproc)` builds via `ghcr.io/grumpycoders/pcsx-redux-build:latest`
  (all deps in the image; project mounted at `/project`). Output binary: `pcsx-redux` in repo
  root (`bins/$(BUILD)/pcsx-redux`).
- Iterate with `./dockershell.sh` → `make -j$(nproc)` for fast incremental rebuilds.
- Fork `grumpycoders/pcsx-redux` → `gibbo101` when there's a working patch to push.
