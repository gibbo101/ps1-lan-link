#!/usr/bin/env python3
"""Configure the loopback instance pair for two-player couch play.

Places both emulator windows side by side across one wide display and assigns each
instance its own host gamepad, so two people play with two controllers on one machine.
The SIO1 link stays on loopback, where round-trip latency fits inside a frame.

Run with the emulators stopped — PCSX-Redux rewrites pcsx.json on exit and will
otherwise overwrite these settings.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INSTANCES = ROOT / "work" / "instances"

# One window per player, splitting the display down the middle. The PS1 renders 4:3,
# so each half letterboxes rather than stretching.
SCREEN_W = int(sys.argv[1]) if len(sys.argv) > 1 else 5120
SCREEN_H = int(sys.argv[2]) if len(sys.argv) > 2 else 1440
HALF_W = SCREEN_W // 2


def configure(instance, pos_x, pad_id):
    cfg = INSTANCES / instance / ".config" / "pcsx-redux" / "pcsx.json"
    d = json.loads(cfg.read_text())

    gui = d.setdefault("gui", {})
    gui["WindowPosX"] = pos_x
    gui["WindowPosY"] = 0
    gui["WindowSizeX"] = HALF_W
    gui["WindowSizeY"] = SCREEN_H
    gui["WindowMaximized"] = False
    gui["Fullscreen"] = False
    gui["FullWindowRender"] = True
    # Debug panels overlay the play area and change nav.py's screen signatures.
    gui["ShowLog"] = False
    gui["ShowSIO1"] = False

    # ID indexes the emulator's list of detected gamepads, so each instance claims a
    # different physical controller.
    pad = d["pads"][0]
    pad["ID"] = pad_id
    pad["Connected"] = True

    cfg.write_text(json.dumps(d, indent=2))
    print(f"{instance}: window {HALF_W}x{SCREEN_H} at x={pos_x}, gamepad #{pad_id}")


for name in ("instA", "instB"):
    if not (INSTANCES / name).is_dir():
        sys.exit(f"missing instance dir: {INSTANCES / name}")

configure("instA", 0, 0)
configure("instB", HALF_W, 1)
print("\nBoth configured. Launch with: ./repro/run-link-test.sh")
print("Player 1 = left window (host, presses X to start). Player 2 = right window.")
