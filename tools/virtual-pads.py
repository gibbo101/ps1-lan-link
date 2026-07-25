#!/usr/bin/env python3
"""Create two virtual gamepads and drive them from a command FIFO.

Steam Input translates the Deck's physical controls to mouse/keyboard in Desktop Mode, so its
virtual gamepads are present but never fed, and keyboard events do not reach PCSX-Redux under
Xwayland at all. These devices bypass both: they are ordinary HID gamepads as far as the kernel,
SDL and GLFW are concerned, so the emulator polls them like any other pad.

They present as an X-Box 360 pad because that GUID is in the SDL controller database GLFW loads,
which is what makes glfwJoystickIsGamepad() accept them.

The devices exist only while this process runs. Commands arrive on the FIFO as "<pad> <button>",
e.g. "0 start" or "1 up"; each is delivered as a press-then-release.
"""
import os
import sys
import time

from evdev import AbsInfo, UInput
from evdev import ecodes as e

FIFO = "/tmp/padctl"

BUTTONS = {
    "cross": e.BTN_A,      # PCSX-Redux maps GLFW cross -> BTN_A
    "circle": e.BTN_B,
    "square": e.BTN_X,
    "triangle": e.BTN_Y,
    "start": e.BTN_START,
    "select": e.BTN_SELECT,
    "l1": e.BTN_TL,
    "r1": e.BTN_TR,
}
# The d-pad is a hat axis on an X-Box 360 pad, not discrete buttons.
HATS = {
    "up": (e.ABS_HAT0Y, -1),
    "down": (e.ABS_HAT0Y, 1),
    "left": (e.ABS_HAT0X, -1),
    "right": (e.ABS_HAT0X, 1),
}

CAPS = {
    e.EV_KEY: sorted(set(BUTTONS.values())) + [e.BTN_MODE, e.BTN_THUMBL, e.BTN_THUMBR],
    e.EV_ABS: [
        (e.ABS_X, AbsInfo(0, -32768, 32767, 16, 128, 0)),
        (e.ABS_Y, AbsInfo(0, -32768, 32767, 16, 128, 0)),
        (e.ABS_RX, AbsInfo(0, -32768, 32767, 16, 128, 0)),
        (e.ABS_RY, AbsInfo(0, -32768, 32767, 16, 128, 0)),
        (e.ABS_Z, AbsInfo(0, 0, 255, 0, 0, 0)),
        (e.ABS_RZ, AbsInfo(0, 0, 255, 0, 0, 0)),
        (e.ABS_HAT0X, AbsInfo(0, -1, 1, 0, 0, 0)),
        (e.ABS_HAT0Y, AbsInfo(0, -1, 1, 0, 0, 0)),
    ],
}


def press(dev, name, hold=0.12):
    if name in BUTTONS:
        dev.write(e.EV_KEY, BUTTONS[name], 1)
        dev.syn()
        time.sleep(hold)
        dev.write(e.EV_KEY, BUTTONS[name], 0)
        dev.syn()
    elif name in HATS:
        axis, value = HATS[name]
        dev.write(e.EV_ABS, axis, value)
        dev.syn()
        time.sleep(hold)
        dev.write(e.EV_ABS, axis, 0)
        dev.syn()
    else:
        return False
    return True


pads = [
    UInput(CAPS, name="Microsoft X-Box 360 pad", vendor=0x045E, product=0x028E,
           version=0x110, bustype=e.BUS_USB)
    for _ in range(2)
]
print("virtual pads:", [p.device.path for p in pads], flush=True)

if os.path.exists(FIFO):
    os.remove(FIFO)
os.mkfifo(FIFO, 0o666)
print(f"listening on {FIFO}", flush=True)

while True:
    with open(FIFO) as f:
        for line in f:
            parts = line.split()
            if len(parts) != 2:
                continue
            idx, button = parts
            try:
                dev = pads[int(idx)]
            except (ValueError, IndexError):
                continue
            ok = press(dev, button.lower())
            print(f"pad{idx} {button} {'ok' if ok else 'UNKNOWN'}", flush=True)
