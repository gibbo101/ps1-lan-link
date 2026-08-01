#!/usr/bin/env python3
"""Record the joiner's video stream exactly as it arrives, for honest replay later.

A raw `nc > file` capture loses how the bytes were chunked in time, and the player's history says
chunking is load-bearing: av_parser silently dropped two frames in three when fed one frame per
read, and a splitter bug survived a whole-file test only to fail live. So this records what each
read() returned and when, and stream-replay.py can hand a player the same bytes in the same pieces
at the same pace.

Format: repeating [u64 delta_us][u32 len][payload], little-endian. delta_us is measured from the
previous chunk (first chunk = 0). A plain .h264 concatenation is written alongside for ffprobe.
"""

import argparse
import socket
import struct
import sys
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("port", type=int)
    parser.add_argument("out", help="output basename; writes <out>.tchunks and <out>.h264")
    parser.add_argument("--seconds", type=float, default=120.0)
    args = parser.parse_args()

    sock = socket.create_connection((args.host, args.port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    sock.settimeout(5.0)

    chunks = 0
    total = 0
    start = None
    prev = None
    with open(args.out + ".tchunks", "wb") as tf, open(args.out + ".h264", "wb") as rf:
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            try:
                data = sock.recv(65536)
            except socket.timeout:
                # Quiet until the encoder starts; give up only if nothing ever arrives.
                if start is None and time.monotonic() < deadline:
                    continue
                break
            if not data:
                break
            now = time.monotonic()
            if start is None:
                start = now
                delta_us = 0
            else:
                delta_us = int((now - prev) * 1_000_000)
            prev = now
            tf.write(struct.pack("<QI", delta_us, len(data)))
            tf.write(data)
            rf.write(data)
            chunks += 1
            total += len(data)

    elapsed = (prev - start) if start else 0.0
    print(f"[capture] {chunks} chunks, {total} bytes over {elapsed:.1f}s", file=sys.stderr)
    sys.exit(0 if total > 0 else 1)


if __name__ == "__main__":
    main()
