#!/usr/bin/env python3
"""Drive one instance from the title screen to the main menu.

Differs from goto_link.py in one way that matters when the two windows overlap: the window is
raised (activated) before every capture, so the screenshot is of that instance and not of whatever
happens to be stacked on top of it.

Start on the title begins the intro and Start on the intro returns to the title, so a fixed delay
just oscillates between them; the menu is only reachable through the black transition that ends
the intro, so wait for black specifically.

  python3 to-menu.py <client-window-id>
"""
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from nav import SIGS  # noqa: E402

DISPLAY = os.environ.get("DISPLAY", ":1")
ENV = dict(os.environ, DISPLAY=DISPLAY)


def geometry(wid):
    out = subprocess.run(["xdotool", "getwindowgeometry", "--shell", str(wid)],
                         env=ENV, capture_output=True, text=True, check=True).stdout
    d = dict(l.split("=", 1) for l in out.strip().splitlines() if "=" in l)
    return int(d["X"]), int(d["Y"]), int(d["WIDTH"]), int(d["HEIGHT"])


def raise_window(wid):
    subprocess.run(["xdotool", "windowactivate", "--sync", str(wid)], env=ENV, check=False)
    time.sleep(0.4)


def mean_colour(wid):
    """Mean RGB of the window, captured after raising it so nothing else is in frame."""
    x, y, w, h = geometry(wid)
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "x11grab", "-video_size", f"{w}x{h}",
         "-i", f"{DISPLAY}+{x},{y}", "-frames:v", "1",
         "-vf", "scale=16:9", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        env=ENV, capture_output=True, check=True).stdout
    n = len(raw) // 3
    return tuple(sum(raw[i * 3 + c] for i in range(n)) // n for c in range(3))


def classify(wid):
    m = mean_colour(wid)
    for name, sig in SIGS.items():
        if all(abs(m[i] - sig[i]) <= 6 for i in range(3)):
            return name, m
    return "other", m


def press(wid, k):
    raise_window(wid)
    subprocess.run(["xdotool", "key", k], env=ENV, check=True)


def to_menu(wid, limit=15):
    for _ in range(limit):
        raise_window(wid)
        state, m = classify(wid)
        print(f"  {state} {m}", flush=True)
        if state == "menu":
            return True
        if state == "black":
            press(wid, "Return")
            time.sleep(4)
            continue
        if state == "title":
            press(wid, "Return")  # starts the intro
            for _ in range(30):   # watch for the black transition that ends it
                time.sleep(1)
                s, _m = classify(wid)
                if s in ("black", "menu"):
                    break
            continue
        press(wid, "Return")      # intro or attract footage: returns to the title
        time.sleep(3)
    return False


if __name__ == "__main__":
    wid = sys.argv[1]
    print("REACHED MENU" if to_menu(wid) else "FAILED")
