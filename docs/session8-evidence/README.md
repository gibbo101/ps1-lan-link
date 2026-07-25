# Session 8 evidence — a linked match in Game Mode

Captured 2026-07-25 on Deck 1, launched from the Steam library as a single Game Mode app.
`gamemode-launcher.log` is the launcher's own output; the two `*-speed.log` files are the
`SIO1-DIAG` lines from each instance (the raw logs also carry BIOS/kernel tracing and run to
millions of lines, so only the diagnostics are kept).

instA is the visible instance, driven by the Deck's own controller. instB ran `-no-ui`: no window,
no GLFW, driven entirely over HTTP from another machine on the tailnet.

## What the traces show

148 samples each, covering 4s–300s of emulated time, in three phases:

| Phase | instA | instB |
|---|---|---|
| Boot + menus | 100.0% / 60.0 fps | 100.0% / 60.0 fps |
| Link handshake (~147–160s) | **60.5–85.6%**, median 61.4% | 100.0% |
| Gameplay | **100.1%** / 60.1 fps | **97.1%** / 58.3 fps |

Both sides hold full speed through an actual linked match. The handshake dip is confined to instA
and lasts about ten seconds; instB does not see it, which is the opposite of the symmetric collapse
that networked attempts produced in sessions 5–7.

## Reading the last sample

Both logs end with a sample that looks like a collapse — instB's final line is **30.1% / 18.0 fps
with stalling at 96.4% of wall and 60975 timeouts**. That is **teardown, not gameplay**: the player
quit, instA was killed first, and instB spent its last two seconds stalling on a peer that no longer
existed. The preceding 147 samples are the record of the run. Do not quote the final line as a
result.
