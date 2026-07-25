#!/usr/bin/env python3
"""Reads the emulator's media export socket and reports what actually arrived.

This is the consumer side of the frame/audio export: it attaches to the unix socket a streamed
instance publishes on, parses the packet stream, and prints rates rather than opinions. Video
frames are optionally written out as PNGs so a picture can be confirmed to be a picture, and two
frames spaced in time are hashed against each other — a static screen looks identical to a dead
one, and only the comparison tells them apart.

  ./tools/stream-probe.py /run/user/1000/ps1-instb.sock --seconds 10 --png-dir /tmp/frames
"""

import argparse
import hashlib
import os
import socket
import struct
import sys
import time
import zlib

HELLO_MAGIC = 0x534C3150  # "P1LS"
PACKET_MAGIC = 0x4B503150  # "P1PK"
HELLO_FMT = "<IHHIHHHHI"
PACKET_FMT = "<IBBHHHIQ"
HELLO_SIZE = struct.calcsize(HELLO_FMT)
PACKET_SIZE = struct.calcsize(PACKET_FMT)

VIDEO, AUDIO = 1, 2
BGR555, RGB888 = 0, 1


def read_exactly(sock, count):
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


def to_rgb(payload, width, height, fmt):
    """Expands a frame into packed RGB888 rows."""
    if fmt == RGB888:
        return payload[: width * height * 3]
    out = bytearray(width * height * 3)
    for i in range(width * height):
        pixel = payload[i * 2] | (payload[i * 2 + 1] << 8)
        # BGR555: 5 bits per channel, blue in the high bits, top bit is the mask bit.
        out[i * 3 + 0] = ((pixel >> 0) & 0x1F) << 3
        out[i * 3 + 1] = ((pixel >> 5) & 0x1F) << 3
        out[i * 3 + 2] = ((pixel >> 10) & 0x1F) << 3
    return bytes(out)


def write_png(path, rgb, width, height):
    """Minimal PNG writer, so the probe needs nothing installed to prove a frame decodes."""
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
    parser = argparse.ArgumentParser()
    parser.add_argument("socket_path")
    parser.add_argument("--seconds", type=float, default=10.0)
    parser.add_argument("--png-dir", default=None, help="write the first, middle and last frame here")
    parser.add_argument("--wav", default=None, help="write the received audio as a wav file")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(5.0)
    sock.connect(args.socket_path)

    hello = read_exactly(sock, HELLO_SIZE)
    if hello is None:
        sys.exit("no hello: the instance closed the connection")
    magic, version, header_size, audio_rate, channels, max_w, max_h, _, flags = struct.unpack(HELLO_FMT, hello)
    if magic != HELLO_MAGIC:
        sys.exit(f"bad hello magic {magic:#x}")
    print(f"hello: version={version} headerSize={header_size} audioRate={audio_rate} channels={channels} "
          f"maxVideo={max_w}x{max_h} flags={flags}")
    if header_size != PACKET_SIZE:
        sys.exit(f"packet header size {header_size} does not match this probe's {PACKET_SIZE}")

    start = time.monotonic()
    frames, audio_packets, audio_frames, video_bytes, audio_bytes = 0, 0, 0, 0, 0
    first_ts, last_ts = None, None
    gaps = []
    saved = []
    audio_data = bytearray()
    rates = set()
    sizes = set()

    while time.monotonic() - start < args.seconds:
        header = read_exactly(sock, PACKET_SIZE)
        if header is None:
            print("stream closed by the instance")
            break
        magic, ptype, fmt, width, height, aux, payload_len, timestamp = struct.unpack(PACKET_FMT, header)
        if magic != PACKET_MAGIC:
            sys.exit(f"desynchronised: packet magic {magic:#x}")
        payload = read_exactly(sock, payload_len) if payload_len else b""
        if payload is None:
            print("stream closed mid-packet")
            break

        if ptype == VIDEO:
            expected = width * height * (3 if fmt == RGB888 else 2)
            if payload_len != expected:
                sys.exit(f"video payload {payload_len} != {expected} for {width}x{height} fmt={fmt}")
            frames += 1
            video_bytes += payload_len
            sizes.add((width, height, fmt))
            if first_ts is None:
                first_ts = timestamp
            else:
                gaps.append((timestamp - last_ts) / 1000.0)
            last_ts = timestamp
            # Spaced by seconds, not by frame index: three consecutive frames are 16 ms apart and
            # will match on any screen that is merely calm, which proves nothing about progress.
            if args.png_dir and len(saved) < 3 and (frames == 1 or timestamp - saved[-1][5] >= 2_000_000):
                saved.append((frames, payload, width, height, fmt, timestamp))
        elif ptype == AUDIO:
            audio_packets += 1
            audio_frames += height
            audio_bytes += payload_len
            rates.add(aux)
            audio_data += payload
        else:
            sys.exit(f"unknown packet type {ptype}")

    elapsed = time.monotonic() - start
    print(f"\nelapsed {elapsed:.1f}s")
    print(f"video: {frames} frames, {frames / elapsed:.1f} fps, {video_bytes / 1048576:.1f} MiB "
          f"({video_bytes / elapsed / 1048576:.1f} MiB/s), sizes={sorted(sizes)}")
    if gaps:
        gaps.sort()
        print(f"       frame gap ms: median {gaps[len(gaps) // 2]:.1f}  p99 {gaps[int(len(gaps) * 0.99)]:.1f}  "
              f"max {gaps[-1]:.1f}")
    print(f"audio: {audio_packets} packets, {audio_frames} frames, {audio_bytes / 1024:.0f} KiB, "
          f"rates={sorted(rates)}, {audio_frames / elapsed:.0f} frames/s")

    if args.wav and audio_data:
        rate = max(rates) if rates else audio_rate
        with open(args.wav, "wb") as f:
            f.write(b"RIFF" + struct.pack("<I", 36 + len(audio_data)) + b"WAVEfmt ")
            f.write(struct.pack("<IHHIIHH", 16, 1, 2, rate, rate * 4, 4, 16))
            f.write(b"data" + struct.pack("<I", len(audio_data)) + bytes(audio_data))
        peak = max(abs(v) for v in struct.unpack(f"<{len(audio_data) // 2}h", bytes(audio_data)))
        print(f"       wrote {args.wav} (peak sample {peak}, {'silent' if peak == 0 else 'audible'})")

    if args.png_dir and saved:
        os.makedirs(args.png_dir, exist_ok=True)
        digests = []
        for index, payload, width, height, fmt, _ in saved:
            path = os.path.join(args.png_dir, f"frame{index:05d}.png")
            write_png(path, to_rgb(payload, width, height, fmt), width, height)
            digests.append((path, hashlib.md5(payload).hexdigest()))
            print(f"       wrote {path}  md5 {digests[-1][1]}")
        if len(digests) > 1:
            distinct = len({d for _, d in digests})
            print(f"       {distinct} distinct of {len(digests)} frames "
                  f"({'the picture is changing' if distinct > 1 else 'static — could be a still screen'})")


if __name__ == "__main__":
    main()
