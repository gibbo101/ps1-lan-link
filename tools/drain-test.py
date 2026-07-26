#!/usr/bin/env python3
"""Measures how much video a player is holding, by cutting the stream and timing the play-out.

A live stream's latency is whatever the player has buffered. Feed it at exactly 60 fps, stop
feeding, and the time it keeps going before it runs dry is that buffer, in milliseconds, measured
on the machine the player actually runs on and with no screen capture involved.

  drain-test.py --player ffplay --frames 600
"""

import argparse
import os
import shlex
import socket
import struct
import subprocess
import sys
import threading
import time


def split_access_units(data):
    """Splits an Annex-B stream into access units, keeping parameter sets with the picture."""
    starts = []
    i = 0
    while True:
        i = data.find(b"\x00\x00\x00\x01", i)
        if i < 0:
            break
        starts.append(i)
        i += 4
    units = []
    current = None
    for n, s in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(data)
        nal = data[s + 4] & 0x1F
        first_slice = nal in (1, 5) and len(data) > s + 5 and (data[s + 5] & 0x80)
        if first_slice and current is not None:
            units.append(current)
            current = b""
        if current is None:
            current = b""
        current += data[s:end]
    if current:
        units.append(current)
    return units


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="/tmp/t.h264")
    ap.add_argument("--port", type=int, default=6790)
    ap.add_argument("--frames", type=int, default=600)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--player", required=True, help="command; {url} is substituted")
    ap.add_argument("--label", default="")
    args = ap.parse_args()

    with open(args.source, "rb") as f:
        units = split_access_units(f.read())
    if len(units) < args.frames:
        sys.exit(f"source has {len(units)} access units, need {args.frames}")
    print(f"[drain] source has {len(units)} access units", flush=True)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", args.port))
    listener.listen(1)

    url = f"tcp://127.0.0.1:{args.port}"
    command = shlex.split(args.player.replace("{url}", url))
    player = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    conn, _ = listener.accept()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print("[drain] player connected", flush=True)

    period = 1.0 / args.fps
    start = time.monotonic()
    due = start
    sent = 0
    try:
        for i in range(args.frames):
            conn.sendall(units[i])
            sent += 1
            due += period
            sleep = due - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
    except OSError as error:
        print(f"[drain] send failed after {sent}: {error}", flush=True)

    cut = time.monotonic()
    conn.close()
    listener.close()
    print(f"[drain] cut the stream after {sent} frames "
          f"({(cut - start):.2f}s of wall for {sent / args.fps:.2f}s of video)", flush=True)

    try:
        player.wait(timeout=30)
    except subprocess.TimeoutExpired:
        player.kill()
        print("[drain] player did not exit within 30s (no -autoexit?)", flush=True)
        return
    drained = (time.monotonic() - cut) * 1000.0

    print(f"\n[drain] {args.label or command[0]}: held {drained:.0f} ms of video "
          f"when the stream was cut", flush=True)


if __name__ == "__main__":
    main()
