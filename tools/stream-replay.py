#!/usr/bin/env python3
"""Serve a .tchunks capture to one player, reproducing the original chunking and pacing.

The counterpart of stream-capture.py, and the reference side of the frames-in-flight measurement:
because the pacing here mirrors the live source, "what has been sent by time t" is known, and a
player's own decoded counter against it is an honest latency figure with no window teardown or
screen capture involved.

Prints SENT lines (elapsed_s, chunks, bytes) every 250 ms so a harness can line them up with the
player's SHOWN lines. Serves one connection, sends everything, then holds the socket open briefly
so a slow player is distinguishable from a starved one, and exits.
"""

import argparse
import socket
import struct
import sys
import time


def read_chunks(path):
    chunks = []
    with open(path, "rb") as f:
        while True:
            header = f.read(12)
            if len(header) < 12:
                break
            delta_us, length = struct.unpack("<QI", header)
            payload = f.read(length)
            if len(payload) < length:
                break
            chunks.append((delta_us, payload))
    return chunks


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("capture", help="a .tchunks file from stream-capture.py")
    parser.add_argument("--port", type=int, default=6690)
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--speed", type=float, default=1.0, help="1.0 = original pacing, 0 = firehose")
    parser.add_argument("--linger", type=float, default=3.0, help="seconds to hold the socket after the last byte")
    args = parser.parse_args()

    chunks = read_chunks(args.capture)
    if not chunks:
        sys.exit(f"no chunks in {args.capture}")

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.bind, args.port))
    listener.listen(1)
    print(f"[replay] {len(chunks)} chunks ready on {args.bind}:{args.port}", file=sys.stderr, flush=True)

    conn, addr = listener.accept()
    conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    print(f"[replay] player connected from {addr[0]}", file=sys.stderr, flush=True)

    start = time.monotonic()
    due = start
    sent_chunks = 0
    sent_bytes = 0
    last_report = start
    for delta_us, payload in chunks:
        if args.speed > 0:
            due += (delta_us / 1_000_000) / args.speed
            wait = due - time.monotonic()
            if wait > 0:
                time.sleep(wait)
        try:
            conn.sendall(payload)
        except OSError as error:
            print(f"[replay] player went away: {error}", file=sys.stderr, flush=True)
            sys.exit(1)
        sent_chunks += 1
        sent_bytes += len(payload)
        now = time.monotonic()
        if now - last_report >= 0.25:
            print(f"SENT {now - start:.3f} {sent_chunks} {sent_bytes}", file=sys.stderr, flush=True)
            last_report = now
    print(f"SENT {time.monotonic() - start:.3f} {sent_chunks} {sent_bytes}", file=sys.stderr, flush=True)
    print(f"[replay] done in {time.monotonic() - start:.1f}s", file=sys.stderr, flush=True)
    time.sleep(args.linger)
    conn.close()


if __name__ == "__main__":
    main()
