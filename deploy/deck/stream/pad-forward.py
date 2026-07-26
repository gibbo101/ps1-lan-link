#!/usr/bin/env python3
"""Reads this machine's gamepad and sends its state to the stream host.

Uses the legacy joystick device (/dev/input/js*) through the standard library alone: SteamOS has no
compiler, no numpy and no evdev module, so anything needing a build is not an option on a Deck.

The wire format is one little-endian uint32 per change: bit N set means the emulator's pad button N
is pressed. Whole state, not edges, so a dropped message corrects itself on the next one.
"""

import argparse
import fcntl
import glob
import os
import select
import socket
import struct
import sys
import time

# The emulator's PCSX.CONSTS.PAD.BUTTON bit numbers, which is what the padstate handler expects.
BUTTON = {
    "select": 0, "l3": 1, "r3": 2, "start": 3,
    "up": 4, "right": 5, "down": 6, "left": 7,
    "l2": 8, "r2": 9, "l1": 10, "r1": 11,
    "triangle": 12, "circle": 13, "cross": 14, "square": 15,
}

# Linux joystick button order for an X-Box style pad, which is what Steam Input presents and what
# the Deck's own controls arrive as. Index here is the js event number.
JS_BUTTONS = ["cross", "circle", "square", "triangle", "l1", "r1", "select", "start",
              None, "l3", "r3"]

# js axes 6 and 7 are the d-pad hat on an X-Box layout; 2 and 5 are the analogue triggers.
HAT_X, HAT_Y = 6, 7
TRIGGER_L, TRIGGER_R = 2, 5
TRIGGER_THRESHOLD = 8000

JS_EVENT = "<IhBB"  # time, value, type, number
JS_EVENT_SIZE = struct.calcsize(JS_EVENT)
JS_BUTTON, JS_AXIS, JS_INIT = 0x01, 0x02, 0x80


JSIOCGBUTTONS = 0x80016A12
JSIOCGNAME = 0x80806A13  # JSIOCGNAME(128)


def describe(fd):
    """Button count and name for a joystick fd, so sensors can be told from controllers."""
    try:
        buttons = bytearray(1)
        fcntl.ioctl(fd, JSIOCGBUTTONS, buttons)
        name = bytearray(128)
        fcntl.ioctl(fd, JSIOCGNAME, name)
        return buttons[0], name.split(b"\x00")[0].decode("utf-8", "replace")
    except OSError:
        return 0, "?"


def open_pads(preferred):
    """Every real controller, not a guessed index.

    Which /dev/input/js* a controller lands on changes whenever anything else appears — Steam's
    phantom pads, a virtual pad, the Deck's own controls — and picking the wrong index is the single
    most common way this project has produced "input does nothing". Reading them all and merging
    their buttons removes the choice: whichever one the player is holding, its presses arrive.

    Each DualSense also exposes a motion-sensor joystick, which reports axes but no buttons. Those
    are skipped by button count, or their axes would arrive as phantom d-pad presses.
    """
    paths = [preferred] if preferred else sorted(glob.glob("/dev/input/js*"))
    pads = []
    for path in paths:
        try:
            fd = open(path, "rb")
        except OSError as error:
            print(f"[pad] {path}: {error}", flush=True)
            continue
        buttons, name = describe(fd)
        if buttons < 4 and not preferred:
            print(f"[pad] {path}: skipping '{name}' ({buttons} buttons — a sensor, not a controller)",
                  flush=True)
            fd.close()
            continue
        print(f"[pad] {path}: reading '{name}' ({buttons} buttons)", flush=True)
        pads.append(fd)
    return pads


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=6691)
    parser.add_argument("--device", default=None, help="defaults to the lowest /dev/input/js*")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    pads = open_pads(args.device)
    if not pads:
        sys.exit("no controller found — connect one, or pass --device")

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    sock.connect((args.host, args.port))
    sock.settimeout(None)
    print(f"[pad] sending to {args.host}:{args.port}", flush=True)

    # Per-device state, merged on the way out: a button held on any pad is a button held.
    states = {pad.fileno(): 0 for pad in pads}
    by_fd = {pad.fileno(): pad for pad in pads}
    sent = 0
    previous = None
    try:
        while True:
            ready, _, _ = select.select(list(by_fd.values()), [], [], 1.0)
            if not ready:
                continue
            source = ready[0]
            data = source.read(JS_EVENT_SIZE)
            if not data or len(data) < JS_EVENT_SIZE:
                break
            state = states[source.fileno()]
            _, value, etype, number = struct.unpack(JS_EVENT, data)
            # The init burst describes the resting position of every control; applying it would
            # report a full hand of buttons before anyone has touched the pad.
            if etype & JS_INIT:
                continue

            if etype & JS_BUTTON:
                name = JS_BUTTONS[number] if number < len(JS_BUTTONS) else None
                if name is None:
                    continue
                bit = 1 << BUTTON[name]
                state = state | bit if value else state & ~bit
            elif etype & JS_AXIS:
                def set_bit(name, pressed):
                    nonlocal state
                    bit = 1 << BUTTON[name]
                    state = state | bit if pressed else state & ~bit

                if number == HAT_X:
                    set_bit("left", value < -16000)
                    set_bit("right", value > 16000)
                elif number == HAT_Y:
                    set_bit("up", value < -16000)
                    set_bit("down", value > 16000)
                elif number == TRIGGER_L:
                    set_bit("l2", value > TRIGGER_THRESHOLD)
                elif number == TRIGGER_R:
                    set_bit("r2", value > TRIGGER_THRESHOLD)
                else:
                    continue
            else:
                continue

            states[source.fileno()] = state
            merged = 0
            for pad_state in states.values():
                merged |= pad_state
            if merged == previous:
                continue
            previous = merged
            sock.sendall(struct.pack("<I", merged))
            sent += 1
            if not args.quiet and sent % 20 == 0:
                print(f"[pad] {sent} updates, state={merged:#06x}", flush=True)
    except (BrokenPipeError, ConnectionResetError, OSError) as error:
        print(f"[pad] link to the host ended: {error}", flush=True)
    finally:
        try:
            sock.sendall(struct.pack("<I", 0))
        except OSError:
            pass
        sock.close()
        for pad in pads:
            pad.close()


if __name__ == "__main__":
    main()
