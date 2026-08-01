#!/usr/bin/env python3
"""The joiner's audio path: raw PCM from the host into aplay, with the latency bounded.

`socat | aplay` played the stream correctly but late, and the lateness was structural. aplay's
default ALSA buffer ran ~170 ms deep, the 64 KB pipe between the two holds another ~370 ms of
44.1 kHz stereo s16, and whatever accumulated in that pipe while aplay was opening the device
stayed there for the whole session — audio cannot skip a backlog the way the video player skips
stale frames. Once the video path dropped to ~2 frames, all of that became audible desync.

So this owns the socket and bounds every stage:

  * the backlog that accumulated before playback could start is discarded, so the session begins
    at "now" rather than at connect time;
  * aplay runs with a small explicit buffer instead of its default;
  * the pipe into aplay is shrunk to its minimum, so it cannot quietly hoard PCM;
  * if drift accumulates anyway (a wifi stall delivers a burst that is already stale), the excess
    is dropped in one aligned cut and the event is logged. A rare click beats permanent desync,
    and the log line is the measurement if it ever needs tuning.

Frames are 4 bytes (s16 stereo); every cut is frame-aligned or the channels swap and the
remainder of the session plays static.
"""

import argparse
import fcntl
import socket
import struct
import subprocess
import sys
import termios
import time

BYTES_PER_SEC = 44100 * 2 * 2
FRAME = 4

F_SETPIPE_SZ = 1031  # fcntl constant, absent from the fcntl module


def log(message):
    print(f"[join-audio] {message}", file=sys.stderr, flush=True)


def pending_bytes(sock):
    buf = fcntl.ioctl(sock.fileno(), termios.FIONREAD, struct.pack("I", 0))
    return int.from_bytes(buf, sys.byteorder)


def drain(sock):
    """Discard everything already queued, so playback starts at the live edge."""
    dropped = 0
    sock.setblocking(False)
    while True:
        try:
            data = sock.recv(65536)
        except BlockingIOError:
            break
        if not data:
            break
        dropped += len(data)
    sock.setblocking(True)
    return dropped


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("host")
    parser.add_argument("--port", type=int, default=6692)
    parser.add_argument("--buffer-ms", type=int, default=50,
                        help="aplay's ALSA buffer; the floor of the audio latency")
    parser.add_argument("--drop-over-ms", type=int, default=80,
                        help="socket backlog beyond this is cut in one drop")
    args = parser.parse_args()

    sock = socket.create_connection((args.host, args.port))
    sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

    player = subprocess.Popen(
        ["aplay", "-q", "-f", "S16_LE", "-r", "44100", "-c", "2", "-t", "raw",
         "-B", str(args.buffer_ms * 1000), "-F", str(args.buffer_ms * 250), "-"],
        stdin=subprocess.PIPE)
    try:
        fcntl.fcntl(player.stdin.fileno(), F_SETPIPE_SZ, 4096)
    except OSError:
        pass  # a bigger pipe is latency, not breakage

    # aplay opens the device between spawn and its first read; the stream that arrived meanwhile
    # is exactly the backlog that used to live in the pipe forever. Give the open a moment, then
    # start from the live edge.
    time.sleep(0.3)
    skipped = drain(sock)
    log(f"playing from {args.host}:{args.port}, skipped {skipped}B of pre-start backlog "
        f"({skipped * 1000 // BYTES_PER_SEC}ms), buffer {args.buffer_ms}ms")

    drop_threshold = args.drop_over_ms * BYTES_PER_SEC // 1000
    carry = b""
    drops = 0
    last_check = time.monotonic()

    while True:
        data = sock.recv(16384)
        if not data:
            log("stream ended")
            break
        data = carry + data
        carry = b""

        now = time.monotonic()
        if now - last_check >= 1.0:
            last_check = now
            backlog = pending_bytes(sock)
            if backlog > drop_threshold:
                # One aligned cut back to the threshold: a click now, sync for the rest.
                sock.setblocking(False)
                stale = b""
                try:
                    while len(stale) < backlog - drop_threshold:
                        chunk = sock.recv(backlog - drop_threshold - len(stale))
                        if not chunk:
                            break
                        stale += chunk
                except BlockingIOError:
                    pass
                sock.setblocking(True)
                # The cut has to remove a whole number of frames or every byte after it is
                # phase-shifted into static. The backlog is far bigger than the remainder,
                # so topping up to alignment cannot block for long.
                while len(stale) % FRAME:
                    stale += sock.recv(1)
                drops += 1
                log(f"dropped {len(stale)}B ({len(stale) * 1000 // BYTES_PER_SEC}ms) of drift "
                    f"(drop #{drops})")

        aligned = len(data) - (len(data) % FRAME)
        carry = data[aligned:]
        try:
            player.stdin.write(data[:aligned])
            player.stdin.flush()
        except BrokenPipeError:
            log("aplay exited")
            break


if __name__ == "__main__":
    main()
