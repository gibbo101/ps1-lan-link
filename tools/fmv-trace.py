#!/usr/bin/env python3
"""Traces EVERY consecutive frame off the export socket and reports the black-frame pattern.

The point of this tool is the lesson that cost a day: sampling every Nth frame hides a per-frame
artefact, and measuring a static title screen measures a case where the fault does not occur. So it
records one line per frame, in order, with no gaps, and prints run-length structure rather than an
average.

  fmv-trace.py /run/user/1000/x.sock --seconds 60 --dump-dir /tmp/f --dump-from 900
"""

import argparse
import os
import socket
import struct
import sys
import time
import zlib

HELLO_MAGIC = 0x534C3150
PACKET_MAGIC = 0x4B503150
HELLO_FMT = "<IHHIHHHHI"
PACKET_FMT = "<IBBHHHIQ"
HELLO_SIZE = struct.calcsize(HELLO_FMT)
PACKET_SIZE = struct.calcsize(PACKET_FMT)
VIDEO, AUDIO = 1, 2
BGR555, RGB888 = 0, 1

# Every 64th byte: a whole-frame statistic sampled sparsely in SPACE. Frames themselves are never
# skipped, because the artefact being hunted is per-frame.
STRIDE = 64


def read_exactly(sock, count):
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def to_rgb(payload, width, height, fmt):
    if fmt == RGB888:
        return payload[: width * height * 3]
    out = bytearray(width * height * 3)
    for i in range(width * height):
        pixel = payload[i * 2] | (payload[i * 2 + 1] << 8)
        out[i * 3 + 0] = ((pixel >> 0) & 0x1F) << 3
        out[i * 3 + 1] = ((pixel >> 5) & 0x1F) << 3
        out[i * 3 + 2] = ((pixel >> 10) & 0x1F) << 3
    return bytes(out)


def write_png(path, rgb, width, height):
    raw = b"".join(b"\x00" + rgb[y * width * 3 : (y + 1) * width * 3] for y in range(height))

    def chunk(tag, data):
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", header))
        f.write(chunk(b"IDAT", zlib.compress(raw, 6)))
        f.write(chunk(b"IEND", b""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("socket_path")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--dump-dir", default=None)
    ap.add_argument("--dump-from", type=int, default=0, help="first frame index to dump")
    ap.add_argument("--dump-count", type=int, default=0)
    ap.add_argument("--trace", default=None, help="write the per-frame CSV here")
    args = ap.parse_args()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(10.0)
    for attempt in range(60):
        try:
            sock.connect(args.socket_path)
            break
        except (FileNotFoundError, ConnectionRefusedError):
            time.sleep(0.5)
    else:
        sys.exit(f"never connected to {args.socket_path}")

    hello = read_exactly(sock, HELLO_SIZE)
    magic, version, header_size, audio_rate, channels, max_w, max_h, _, flags = struct.unpack(HELLO_FMT, hello)
    if magic != HELLO_MAGIC:
        sys.exit(f"bad hello magic {magic:#x}")
    print(f"hello: v{version} audio={audio_rate}x{channels} max={max_w}x{max_h}", flush=True)

    start = time.monotonic()
    rows = []
    if args.dump_dir:
        os.makedirs(args.dump_dir, exist_ok=True)

    while time.monotonic() - start < args.seconds:
        header = read_exactly(sock, PACKET_SIZE)
        if header is None:
            print("stream closed", flush=True)
            break
        magic, ptype, fmt, width, height, aux, payload_len, timestamp = struct.unpack(PACKET_FMT, header)
        if magic != PACKET_MAGIC:
            sys.exit("desynchronised")
        payload = read_exactly(sock, payload_len) if payload_len else b""
        if payload is None:
            break
        if ptype != VIDEO:
            continue

        index = len(rows)
        sample = payload[::STRIDE]
        mean = sum(sample) / len(sample)
        nonzero = sum(1 for b in sample if b) / len(sample)
        rows.append((index, timestamp, width, height, fmt, mean, nonzero))

        if args.dump_dir and args.dump_count and args.dump_from <= index < args.dump_from + args.dump_count:
            write_png(
                os.path.join(args.dump_dir, f"f{index:05d}_m{mean:06.2f}.png"),
                to_rgb(payload, width, height, fmt), width, height,
            )

    print(f"\n{len(rows)} frames in {time.monotonic() - start:.1f}s", flush=True)
    if not rows:
        return

    if args.trace:
        with open(args.trace, "w") as f:
            f.write("index,timestampUs,width,height,fmt,mean,nonzero\n")
            for r in rows:
                f.write(f"{r[0]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]:.3f},{r[6]:.4f}\n")
        print(f"wrote {args.trace}", flush=True)

    # Structure, per display mode: the fault only shows in one of them.
    modes = {}
    for r in rows:
        modes.setdefault((r[2], r[3], r[4]), []).append(r)
    for mode, group in sorted(modes.items(), key=lambda kv: -len(kv[1])):
        black = [g for g in group if g[5] < 1.0]
        print(f"\nmode {mode[0]}x{mode[1]} fmt={mode[2]}: {len(group)} frames, "
              f"{len(black)} black ({100 * len(black) / len(group):.1f}%)", flush=True)

    # Consecutive run lengths of black vs non-black across the whole capture.
    runs = []
    current, length = None, 0
    for r in rows:
        state = r[5] < 1.0
        if state == current:
            length += 1
        else:
            if current is not None:
                runs.append((current, length))
            current, length = state, 1
    runs.append((current, length))
    print("\nrun lengths (B=black n=picture): " + " ".join(f"{'B' if s else 'n'}{n}" for s, n in runs[:80]), flush=True)


if __name__ == "__main__":
    main()
