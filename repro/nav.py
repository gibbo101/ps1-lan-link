#!/usr/bin/env python3
"""Drive one instance through the menus to the LINK GAME team-select screen.

Classifies the screen from a downsampled mean colour rather than assuming a fixed key
sequence, because the attract loop reclaims the menu on a timeout and a blind sequence
desynchronises. Team select is the first screen with no idle timeout, so it is a safe
place to park one instance while the other catches up.
"""
import subprocess, sys, time, os
from PIL import Image

REPRO = os.path.dirname(os.path.abspath(__file__))
SHOTDIR = os.environ.get("SHOTDIR", "/tmp")

# Mean colour of the whole window, so these depend on the window size (the letterbox around the
# 4:3 output changes the average) and on whether the ImGui debug windows are shown. Recalibrate
# after changing either: capture each screen and print the 16x9 downsampled mean. Values below are
# for a 900x700 window with ShowLog and ShowSIO1 disabled.
SIGS = {
    "title": (66, 65, 58),
    "menu": (43, 16, 7),
    "black": (2, 2, 2),
}


def shot(wid, name):
    path = os.path.join(SHOTDIR, name)
    subprocess.run([os.path.join(REPRO, "drive.sh"), "shot", str(wid), path],
                   check=True, capture_output=True)
    return path


def classify(path):
    im = Image.open(path).convert("RGB").resize((16, 9))
    px = list(im.getdata())
    m = tuple(sum(c[i] for c in px) // len(px) for i in range(3))
    for name, sig in SIGS.items():
        if all(abs(m[i] - sig[i]) <= 6 for i in range(3)):
            return name, m
    return "other", m


def key(wid, k):
    env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":1"))
    subprocess.run(["xdotool", "windowactivate", "--sync", str(wid)], env=env, check=True)
    time.sleep(0.3)
    subprocess.run(["xdotool", "key", k], env=env, check=True)


def where(wid, tag="probe"):
    return classify(shot(wid, f"{tag}.png"))


if __name__ == "__main__":
    wid = sys.argv[1]
    tag = sys.argv[2] if len(sys.argv) > 2 else "probe"
    state, m = where(wid, tag)
    print(f"{state} {m}")
