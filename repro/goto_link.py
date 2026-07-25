#!/usr/bin/env python3
"""Drive an instance from wherever it is to the LINK GAME team-select screen.

The attract loop reclaims the menu on an idle timeout, so each step re-reads the screen
and decides from what is actually there. Team select has no idle timeout, which makes it
the rendezvous point: park the first instance there while the second catches up.

  python3 goto_link.py <window-id> [--probe]
"""
import sys, time, os
from nav import where, key

MENU_SIGS = {"title", "menu", "black"}


def step(wid, tag):
    state, m = where(wid, tag)
    print(f"  {state} {m}", flush=True)
    return state, m


def to_menu(wid, tag, limit=12):
    """Title -> intro -> black transition -> main menu.

    Start on the title begins the intro and Start on the intro returns to the title, so a fixed
    delay just oscillates between the two. The menu is only reachable by pressing through the
    black transition that ends the intro, so wait for that specifically rather than guessing.
    """
    for _ in range(limit):
        state, _m = step(wid, tag)
        if state == "menu":
            return True
        if state == "black":
            key(wid, "Return")
            time.sleep(4)
            continue
        if state == "title":
            key(wid, "Return")  # starts the intro
            for _ in range(30):  # watch for the black transition that ends it
                time.sleep(1)
                s, _ = step(wid, tag)
                if s in ("black", "menu"):
                    break
            continue
        key(wid, "Return")  # intro or attract footage: returns to the title
        time.sleep(3)
    return False


def goto_link(wid, tag="nav", limit=40):
    for i in range(limit):
        state, m = step(wid, tag)
        if state == "black":
            time.sleep(2)
            continue
        if state == "title":
            key(wid, "Return")
            time.sleep(3)
            continue
        if state == "menu":
            key(wid, "Up")
            time.sleep(1)
            st2, m2 = step(wid, tag)
            if st2 != "menu":
                continue
            key(wid, "x")
            time.sleep(5)
            st3, m3 = step(wid, tag)
            if st3 not in MENU_SIGS:
                print(f"  reached non-menu screen {m3} — assuming team select", flush=True)
                return True, m3
            continue
        # Movie or attract footage: Start returns to the title screen.
        key(wid, "Return")
        time.sleep(3)
    return False, None


if __name__ == "__main__":
    wid = sys.argv[1]
    tag = sys.argv[2] if len(sys.argv) > 2 else "nav"
    ok, m = goto_link(wid, tag)
    print("REACHED" if ok else "FAILED", m)
