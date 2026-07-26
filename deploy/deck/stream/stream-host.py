#!/usr/bin/env python3
"""Host side of the bespoke stream: instB's export socket out to the joiner, pad input back.

This is the only process in the project that accepts a connection from the LAN. It carries video,
audio and pad input and nothing else — the emulator's own control surface stays on loopback, and
the emulator itself never listens on a routable address. Keep it that way.

  instB --(unix socket, raw frames + s16 audio)--> stream-host --(h264/aac mpegts over TCP)--> joiner
  joiner --(pad state over TCP)--> stream-host --(pad overrides over loopback HTTP)--> instB

Pixels are handed to ffmpeg untouched: the PS1's 16bpp VRAM word is exactly ffmpeg's bgr555le, and
its 24bpp mode is rgb24, so nothing here converts a pixel. That matters because the Deck has no
numpy and no compiler.

Two things follow from the PS1 changing display mode as it runs (FMV at 320x240 24bpp, menus at
512x240 16bpp, some screens 640x480):

  * ffmpeg scales every mode to one fixed output size, so the joiner sees a stream whose resolution
    never changes and a picture that fills its screen rather than sitting in a corner.
  * this process owns the TCP socket rather than letting ffmpeg listen, so rebuilding the encoder
    for a new input geometry does not drop the joiner's connection. mpegts is resumable, so the
    joiner sees a brief discontinuity instead of a disconnect.

Nothing here may stall the emulator. The export socket drops frames on its own when this process
reads slowly, which is the designed backpressure; the reader thread is never blocked on a write.
"""

import argparse
import os
import queue
import signal
import socket
import struct
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

HELLO_FMT = "<IHHIHHHHI"
PACKET_FMT = "<IBBHHHIQ"
HELLO_SIZE = struct.calcsize(HELLO_FMT)
PACKET_SIZE = struct.calcsize(PACKET_FMT)
HELLO_MAGIC = 0x534C3150
PACKET_MAGIC = 0x4B503150
VIDEO, AUDIO = 1, 2
BGR555, RGB888 = 0, 1

# ffmpeg's names for what the emulator already produces, so no pixels are touched in transit.
PIX_FMT = {BGR555: "bgr555le", RGB888: "rgb24"}

running = True


def log(message):
    print(f"[stream-host] {message}", flush=True)


def read_exactly(sock, count):
    buf = bytearray()
    while len(buf) < count:
        chunk = sock.recv(count - len(buf))
        if not chunk:
            return None
        buf += chunk
    return bytes(buf)


class Client:
    """The joiner's video connection, owned here rather than by ffmpeg.

    Holding the socket at this level is what lets the encoder be rebuilt without the joiner
    noticing: the byte stream continues, only its contents restart.
    """

    def __init__(self):
        self.sock = None
        self.lock = threading.Lock()
        self.connections = 0

    def attach(self, sock):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
            self.sock = sock
            self.connections += 1

    def detach(self):
        with self.lock:
            if self.sock is not None:
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None

    def connected(self):
        return self.sock is not None

    def send(self, data):
        with self.lock:
            if self.sock is None:
                return
            try:
                self.sock.sendall(data)
            except OSError as error:
                log(f"joiner video connection ended: {error}")
                try:
                    self.sock.close()
                except OSError:
                    pass
                self.sock = None


class Encoder:
    """One ffmpeg process, serving one input geometry.

    A rawvideo input's geometry is fixed when it opens, so a display-mode change rebuilds this.
    The *output* geometry never changes, which is what keeps that rebuild cheap for the joiner.
    """

    generation = 0

    def __init__(self, width, height, fmt, args, client):
        self.width, self.height, self.fmt = width, height, fmt
        self.client = client
        self.active = True
        self.dropped_video = 0
        self.dropped_audio = 0

        # A generation in the name: a replacement encoder must not touch the fifo the outgoing one
        # is still shutting down around.
        Encoder.generation += 1
        self.audio_path = os.path.join(args.runtime_dir, f"ps1-stream-audio-{Encoder.generation}.fifo")
        if os.path.exists(self.audio_path):
            os.unlink(self.audio_path)
        os.mkfifo(self.audio_path)

        command = [
            "ffmpeg", "-hide_banner", "-loglevel", "error",
            "-fflags", "nobuffer", "-flags", "low_delay",
            "-thread_queue_size", "64",
            "-f", "rawvideo", "-pix_fmt", PIX_FMT[fmt], "-s", f"{width}x{height}",
            "-r", str(args.fps), "-i", "pipe:0",
            "-thread_queue_size", "512",
            "-f", "s16le", "-ar", str(args.audio_rate), "-ac", "2", "-i", self.audio_path,
            "-vf", f"scale={args.out_width}:{args.out_height}:flags=bilinear",
            "-c:v", args.vcodec, "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", args.bitrate, "-g", str(args.fps), "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "128k",
            "-f", "mpegts", "-muxdelay", "0", "-muxpreload", "0", "pipe:1",
        ]
        log(f"encoder up: {width}x{height} {PIX_FMT[fmt]} -> {args.out_width}x{args.out_height} {args.vcodec}")
        self.proc = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE)

        # Opening the write end of a fifo blocks until ffmpeg opens the read end, so it happens off
        # the reader thread. Until then audio is dropped rather than queued into latency.
        self.audio_fd = None
        threading.Thread(target=self._open_audio, daemon=True).start()

        self.video_q = queue.Queue(maxsize=3)
        self.audio_q = queue.Queue(maxsize=64)
        self.threads = [
            threading.Thread(target=self._feed_video, daemon=True),
            threading.Thread(target=self._feed_audio, daemon=True),
            threading.Thread(target=self._forward, daemon=True),
        ]
        for thread in self.threads:
            thread.start()

    def _open_audio(self):
        try:
            self.audio_fd = open(self.audio_path, "wb")
        except OSError as error:
            log(f"audio fifo failed to open: {error}")

    def _feed_video(self):
        while running and self.proc.poll() is None:
            try:
                payload = self.video_q.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                self.proc.stdin.write(payload)
                self.proc.stdin.flush()
            except (BrokenPipeError, ValueError, OSError):
                return

    def _feed_audio(self):
        while running and self.proc.poll() is None:
            try:
                payload = self.audio_q.get(timeout=0.5)
            except queue.Empty:
                continue
            if self.audio_fd is None:
                continue
            try:
                self.audio_fd.write(payload)
                self.audio_fd.flush()
            except (BrokenPipeError, ValueError, OSError):
                return

    def _forward(self):
        """Encoded bytes to the joiner. Reads ffmpeg even with nobody attached, or it would block.

        Stops sending the moment this encoder is superseded. A replaced encoder keeps draining for
        a little while as it shuts down, and two muxers interleaving into one socket produce a
        stream that decodes to nothing.
        """
        while running and self.proc.poll() is None:
            chunk = self.proc.stdout.read(16384)
            if not chunk:
                return
            if self.active:
                self.client.send(chunk)

    def matches(self, width, height, fmt):
        return (width, height, fmt) == (self.width, self.height, self.fmt)

    def push_video(self, payload):
        try:
            self.video_q.put_nowait(payload)
        except queue.Full:
            self.dropped_video += 1

    def push_audio(self, payload):
        try:
            self.audio_q.put_nowait(payload)
        except queue.Full:
            self.dropped_audio += 1

    def alive(self):
        return self.proc.poll() is None

    def close(self):
        """Returns immediately; the process teardown finishes on its own thread.

        Waiting here would be waiting on the reader thread, and every millisecond spent not reading
        the export socket is a frame the emulator throws away. A display-mode change must cost the
        reader nothing.
        """

        self.active = False

        def teardown():
            for closer in (lambda: self.proc.stdin.close(),
                           lambda: self.audio_fd and self.audio_fd.close()):
                try:
                    closer()
                except Exception:
                    pass
            try:
                self.proc.terminate()
                self.proc.wait(timeout=3)
            except Exception:
                self.proc.kill()
            if os.path.exists(self.audio_path):
                os.unlink(self.audio_path)

        threading.Thread(target=teardown, daemon=True).start()


class PadBridge:
    """Applies the joiner's pad state to a headless instance.

    A windowless instance has no GLFW and therefore no pad of its own, so the joiner's buttons
    arrive as overrides through the emulator's Lua handler on loopback. State is sent only when it
    changes: a button held for a second is one message, not sixty.
    """

    def __init__(self, control_port, handler):
        self.url = f"http://127.0.0.1:{control_port}/api/v1/lua/{handler}"
        self.previous = None
        self.applied = 0
        self.failures = 0

    def apply(self, buttons):
        if buttons == self.previous:
            return
        self.previous = buttons
        try:
            urllib.request.urlopen(self.url + f"?buttons={buttons}", timeout=0.5).read()
            self.applied += 1
        except (urllib.error.URLError, OSError) as error:
            self.failures += 1
            if self.failures in (1, 10, 100) or self.failures % 1000 == 0:
                log(f"pad bridge failed {self.failures}x (latest: {error})")


def serve_video(args, client):
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    listener.bind((args.bind, args.port))
    listener.listen(1)
    listener.settimeout(1.0)
    log(f"video listening on {args.bind}:{args.port}")
    while running:
        try:
            conn, addr = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        log(f"joiner video connected from {addr[0]}")
        client.attach(conn)


def serve_input(args, bridge):
    """Accepts the joiner's pad connection. Takes button bitmasks and nothing else."""
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind((args.bind, args.input_port))
    listener.listen(1)
    listener.settimeout(1.0)
    log(f"pad input listening on {args.bind}:{args.input_port}")
    while running:
        try:
            conn, addr = listener.accept()
        except socket.timeout:
            continue
        except OSError:
            return
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        log(f"joiner pad connected from {addr[0]}")
        conn.settimeout(2.0)
        with conn:
            while running:
                try:
                    data = read_exactly(conn, 4)
                except socket.timeout:
                    continue
                except OSError:
                    break
                if data is None:
                    break
                bridge.apply(struct.unpack("<I", data)[0])
        log("joiner pad disconnected")
        bridge.apply(0)  # nothing pressed, or a button held at disconnect outlives the joiner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--export-socket", default=None, help="the emulator's -stream socket")
    parser.add_argument("--bind", default="0.0.0.0", help="LAN address to serve the stream on")
    parser.add_argument("--port", type=int, default=6690)
    parser.add_argument("--input-port", type=int, default=6691)
    parser.add_argument("--control-port", type=int, default=6681, help="instB's loopback HTTP surface")
    parser.add_argument("--pad-handler", default="padstate", help="Lua handler applying the pad state")
    parser.add_argument("--vcodec", default="libx264")
    parser.add_argument("--bitrate", default="8M")
    parser.add_argument("--fps", type=int, default=60)
    parser.add_argument("--out-width", type=int, default=640)
    parser.add_argument("--out-height", type=int, default=480)
    parser.add_argument("--audio-rate", type=int, default=44100)
    parser.add_argument("--runtime-dir", default=os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    parser.add_argument("--stats-interval", type=float, default=5.0)
    args = parser.parse_args()

    if args.export_socket is None:
        args.export_socket = os.path.join(args.runtime_dir, "pcsx-redux-stream.sock")

    global running

    def stop(signum, frame):
        global running
        running = False

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    client = Client()
    bridge = PadBridge(args.control_port, args.pad_handler)
    threading.Thread(target=serve_video, args=(args, client), daemon=True).start()
    threading.Thread(target=serve_input, args=(args, bridge), daemon=True).start()

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    for attempt in range(60):
        try:
            sock.connect(args.export_socket)
            break
        except OSError:
            if attempt == 0:
                log(f"waiting for {args.export_socket}")
            time.sleep(1.0)
    else:
        sys.exit(f"never saw the export socket at {args.export_socket}")

    hello = read_exactly(sock, HELLO_SIZE)
    if hello is None or struct.unpack(HELLO_FMT, hello)[0] != HELLO_MAGIC:
        sys.exit("the export socket did not speak the stream protocol")
    log(f"attached to {args.export_socket}")

    encoder = None
    frames = audio_packets = 0
    last_stats = time.monotonic()

    while running:
        header = read_exactly(sock, PACKET_SIZE)
        if header is None:
            log("the emulator closed the export socket")
            break
        magic, ptype, fmt, width, height, aux, payload_len, timestamp = struct.unpack(PACKET_FMT, header)
        if magic != PACKET_MAGIC:
            log("desynchronised from the export stream")
            break
        payload = read_exactly(sock, payload_len) if payload_len else b""
        if payload is None:
            break

        if ptype == VIDEO:
            # No joiner, no encoder. Encoding for nobody would spend a Deck's headroom on frames
            # that go straight in the bin, and this host is already running two emulators.
            if encoder and not client.connected():
                log("joiner gone — stopping the encoder")
                encoder.close()
                encoder = None
            if encoder and not encoder.alive():
                log("ffmpeg exited — rebuilding")
                encoder.close()
                encoder = None
            if encoder and not encoder.matches(width, height, fmt):
                log(f"display mode changed to {width}x{height} fmt={fmt}")
                encoder.close()
                encoder = None
            if encoder is None and client.connected():
                encoder = Encoder(width, height, fmt, args, client)
            if encoder:
                encoder.push_video(payload)
            frames += 1
        elif ptype == AUDIO:
            if encoder:
                encoder.push_audio(payload)
            audio_packets += 1

        now = time.monotonic()
        if now - last_stats >= args.stats_interval:
            elapsed = now - last_stats
            log(f"in {frames / elapsed:.1f} fps, {audio_packets / elapsed:.0f} audio pkt/s | "
                f"joiner={'yes' if client.connected() else 'no'} conns={client.connections} | "
                f"encoder drops v={encoder.dropped_video if encoder else 0} "
                f"a={encoder.dropped_audio if encoder else 0} | "
                f"pad applied={bridge.applied} failed={bridge.failures}")
            frames = audio_packets = 0
            last_stats = now

    if encoder:
        encoder.close()
    client.detach()
    sock.close()
    log("stopped")


if __name__ == "__main__":
    main()
