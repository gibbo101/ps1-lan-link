#!/usr/bin/env python3
"""Measures a player's latency as frames in flight: what has been sent, minus what is on screen.

Two instruments were tried before this one and both were wrong. Timing how long a player runs after
the stream is cut measures its window teardown as much as its buffer - ffplay's figure barely moves
between a full queue and an empty one. Screen capture would settle it, but a Deck's gamescope
display captures black, which is the same wall session 7 hit.

What both players can report without either problem is how many frames they have put on screen.
ffplay's own stats line carries it as a playback position, and the bespoke presenter prints a count.
Subtract from what the sender has sent at that instant and the difference is the pipeline's depth,
in frames, with no teardown and no capture anywhere in it.

  inflight-probe.py --player ffplay
  inflight-probe.py --player presenter
"""

import argparse
import os
import re
import shlex
import socket
import statistics
import subprocess
import threading
import time

# ffplay's stats line begins with the master clock position in seconds.
FFPLAY_POSITION = re.compile(r"^\s*([0-9]+\.[0-9]+)\s")
PRESENTER_SHOWN = re.compile(r"^SHOWN\s+(\d+)\s+(\d+)")


def split_access_units(data):
    starts = []
    i = 0
    while True:
        i = data.find(b"\x00\x00\x00\x01", i)
        if i < 0:
            break
        starts.append(i)
        i += 4
    units, current = [], None
    for n, s in enumerate(starts):
        end = starts[n + 1] if n + 1 < len(starts) else len(data)
        nal = data[s + 4] & 0x1F
        first = nal in (1, 5) and len(data) > s + 5 and (data[s + 5] & 0x80)
        if first and current is not None:
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
    ap.add_argument("--port", type=int, default=6791)
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--fps", type=int, default=60)
    ap.add_argument("--kind", choices=["ffplay", "presenter"], required=True)
    ap.add_argument("--player", required=True, help="command; {url} is substituted")
    args = ap.parse_args()

    with open(args.source, "rb") as f:
        units = split_access_units(f.read())
    total = int(args.seconds * args.fps)
    if len(units) < total:
        units = units * (total // len(units) + 1)

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", args.port))
    listener.listen(1)

    url = f"tcp://127.0.0.1:{args.port}"
    command = shlex.split(args.player.replace("{url}", url))
    player = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

    sent = 0
    sent_lock = threading.Lock()
    samples = []

    def watch():
        # ffplay rewrites its stats line with a carriage return rather than ending it, so iterating
        # by line never yields anything until the process exits.
        fd = player.stderr.fileno()
        buf = ""
        while True:
            data = os.read(fd, 4096)
            if not data:
                return
            now = time.monotonic()
            buf += data.decode("utf-8", "replace")
            parts = re.split(r"[\r\n]", buf)
            buf = parts.pop()
            for part in parts:
                shown = None
                if args.kind == "ffplay":
                    m = FFPLAY_POSITION.match(part)
                    if m:
                        shown = float(m.group(1)) * args.fps
                else:
                    m = PRESENTER_SHOWN.match(part)
                    if m:
                        # Frames consumed, not frames drawn. A player that deliberately skips a
                        # stale frame has not fallen behind by it, and charging it for the skip
                        # would report its whole reason for existing as latency.
                        shown = float(m.group(2))
                if shown is None:
                    continue
                with sent_lock:
                    current = sent
                if current > 0:
                    samples.append((now, current - shown))

    threading.Thread(target=watch, daemon=True).start()

    conn, _ = listener.accept()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    period = 1.0 / args.fps
    start = time.monotonic()
    due = start
    try:
        for i in range(total):
            conn.sendall(units[i])
            with sent_lock:
                sent = i + 1
            due += period
            sleep = due - time.monotonic()
            if sleep > 0:
                time.sleep(sleep)
    except OSError as error:
        print(f"[probe] send failed: {error}")

    conn.close()
    listener.close()
    time.sleep(0.3)
    player.terminate()

    # Ignore the first two seconds: the player is still starting up and has shown nothing.
    steady = [d for t, d in samples if t - start > 2.0]
    print(f"[probe] {len(samples)} samples seen, {len(steady)} after warm-up")
    if len(steady) < 5:
        print("[probe] not enough to conclude")
        return

    steady.sort()
    median = statistics.median(steady)
    print(f"[probe] {args.kind}: n={len(steady)}  frames in flight: "
          f"median {median:.1f}  p10 {steady[int(len(steady) * 0.1)]:.1f}  "
          f"p90 {steady[int(len(steady) * 0.9)]:.1f}")
    print(f"[probe] => latency {median * 1000.0 / args.fps:.0f} ms at {args.fps} fps")


if __name__ == "__main__":
    main()
