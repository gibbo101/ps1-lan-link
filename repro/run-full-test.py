#!/usr/bin/env python3
"""End-to-end link test: relaunch a clean pair, drive both to LINK GAME, start setup, report.

Navigation is state-driven up to the main menu (the attract loop reclaims every screen before
that, so a blind sequence desynchronises). From the menu onward the sequence is deterministic
and tracked explicitly, because the link screens share the menu's artwork and cannot be told
apart by mean colour.

  python3 run-full-test.py [--hold SECONDS]
"""
import os, subprocess, sys, time

REPRO = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(REPRO)
sys.path.insert(0, REPRO)
from nav import where, key  # noqa: E402

SHOTDIR = os.environ.get("SHOTDIR", "/tmp")
HOLD = 40
if "--hold" in sys.argv:
    HOLD = int(sys.argv[sys.argv.index("--hold") + 1])


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, **kw)


def windows():
    out = sh(f"{REPRO}/drive.sh wins").stdout.strip().splitlines()
    ids = [line.split()[0] for line in out if line.strip()]
    # Each instance exposes a frame and a content window at the same origin; keep the content one.
    seen, keep = {}, []
    for line in out:
        wid, x, y, w, h = line.split()
        seen.setdefault(int(x) // 100, []).append((int(w) * int(h), wid))
    for _, group in sorted(seen.items()):
        keep.append(min(group)[1])
    return keep


def to_menu(wid, tag, limit=40):
    """Drive to the main menu, deciding from what is on screen at each step.

    Start on the title plays the intro; Start on the intro returns to the title, so pressing
    blindly at either just oscillates. The menu is only reachable through the black transition
    that follows the intro, which is why black is a press state rather than a wait state.
    """
    for _ in range(limit):
        state, m = where(wid, tag)
        if state == "menu":
            return True
        key(wid, "Return")
        time.sleep(4 if state in ("title", "black") else 3)
    return False


def main():
    print("relaunching pair...", flush=True)
    # Redirect inside the shell rather than capturing: the launched emulators keep the inherited
    # pipe open, so a capturing parent waits on EOF that never comes and the script appears to hang.
    subprocess.run("./repro/run-link-test.sh > /tmp/link-launch.log 2>&1",
                   shell=True, cwd=ROOT, timeout=180)
    time.sleep(25)

    ps = sh("ps -o pid,etime -C pcsx-redux").stdout.strip().splitlines()[1:]
    port = sh("ss -tnlp 2>/dev/null | grep 6699").stdout
    print(f"processes: {len(ps)}  listener: {'yes' if port else 'no'}", flush=True)
    if len(ps) != 2 or not port:
        print("ABORT: pair integrity check failed")
        return 1
    md5 = sh("md5sum work/emu/squashfs-root/usr/bin/pcsx-redux "
             + " ".join(f"/proc/{l.split()[0]}/exe" for l in ps)).stdout
    hashes = {line.split()[0] for line in md5.strip().splitlines()}
    print(f"binary hashes identical: {len(hashes) == 1}", flush=True)
    if len(hashes) != 1:
        print("ABORT: running binaries do not match the build")
        return 1

    wa, wb = windows()
    print(f"instA={wa} instB={wb}", flush=True)

    # instB first: it becomes the side that sits on WAITING TO CONNECT.
    for name, wid in (("instB", wb), ("instA", wa)):
        print(f"{name}: navigating to main menu...", flush=True)
        if not to_menu(wid, f"nav_{name}"):
            print(f"ABORT: {name} never reached the main menu")
            return 1
        print(f"{name}: menu -> LINK GAME", flush=True)
        key(wid, "Up")
        time.sleep(1)
        key(wid, "x")
        time.sleep(8)

    # The second instance to enter LINK GAME lands on COUNTRY/COLOR; the first waits. Confirming
    # on one hands the turn to the other, so START SETUP must be pressed on both.
    print("instA: START SETUP", flush=True)
    key(wa, "x")
    time.sleep(20)
    print("instB: START SETUP", flush=True)
    key(wb, "x")
    time.sleep(HOLD)

    for name, wid in (("instA", wa), ("instB", wb)):
        subprocess.run(["xdotool", "windowraise", wid],
                       env=dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":1")))
        time.sleep(0.5)
        subprocess.run([f"{REPRO}/drive.sh", "shot", wid, f"{SHOTDIR}/final_{name}.png"])
    print(f"screenshots: {SHOTDIR}/final_instA.png, final_instB.png", flush=True)

    for i in ("A", "B"):
        log = f"work/instances/inst{i}/run.log"
        burst = sh(f"grep -o 'cyc=[0-9]* RX accept' {log} | awk '{{print $1}}' | uniq -d | wc -l").stdout.strip()
        mx = sh(f"grep -o 'fifo=[0-9]*' {log} | cut -d= -f2 | sort -n | tail -1").stdout.strip()
        dtr = sh(f"grep -c 'DTR ' {log}").stdout.strip()
        rst = sh(f"grep -c 'CR_RESET discarded' {log}").stdout.strip()
        print(f"inst{i}: same-cycle bursts={burst} maxfifo={mx} dtr_toggles={dtr} reset_losses={rst}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
