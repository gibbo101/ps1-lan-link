# Session 9 evidence — the loopback bind patch, on the Deck

All captured from Deck 1 in Game Mode on 2026-07-25, running the patched binary
(md5 `369523e845daa10500d30524e2c01082`) with every listener bound to `127.0.0.1`.
Screens come from the emulator's own `takeScreenShot()` via `pad.sh <A|B> shot`, so instB is
observable despite having no window.

State at capture time, from the same run:

```
LISTEN 127.0.0.1:6680      instA control surface
LISTEN 127.0.0.1:6681      instB control surface
LISTEN 127.0.0.1:6699      SIO1 link
link sockets: 2 (connected)
instA SPEED 100.01% emuFps=60.01
instB SPEED  99.99% emuFps=60.00
```

| File | What it shows |
|---|---|
| `insta-waiting-to-connect.png` | instA (host, on screen) parked on `WAITING TO CONNECT` |
| `instb-attract-fallback.png` | instB back in the attract loop — a main menu left idle falls back to it, which is why instA waits |
| `instb-main-menu-link-game.png` | instB's main menu **with `LINK GAME`**. That entry exists only while the peer socket is connected, so it is a free liveness check for the loopback link |
| `instb-country-color.png` | instB at `COUNTRY/COLOR`, `✕ START SETUP` |
| `instb-match-options-go-back.png` | instB at match options: `MAP 3`, `UNITS 6`, `TECH LEVEL 10`, `CREDITS 10000`, only `GO BACK` |
| `insta-match-options-to-start.png` | instA at match options, **identical settings**, `✕ TO START` |

The last two are the pair that matters. Two instances showing the same generated match settings, with
only the host offered the start, is this project's standing proof of a real link — reproduced here on
loopback-bound listeners. The tester then played the match: full-speed gameplay, with the familiar
slowdown on menu screens (session 6's idle-poll cost, unrelated to this patch).
