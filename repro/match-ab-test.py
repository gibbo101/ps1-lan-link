#!/usr/bin/env python3
"""A/B rig for the in-game link-stall asymmetry: drive a real match over loopback, then report
both sides' SPEED lines from the in-game window only.

Run once bare for the baseline and once with the knob under test, e.g.:

  python3 repro/match-ab-test.py --label baseline
  PCSX_LINK_LOCAL_ACK=1 python3 repro/match-ab-test.py --label localack

The launch script passes the environment through to both instances. Screenshots and a copy of
each run's logs land in SHOTDIR/<label> so runs can be compared after the fact.

Match start is the phase that historically broke ack experiments (guest desync, mutual
livelock), so the rig deliberately crosses it rather than parking on the setup screen the way
run-full-test.py does. Both failure modes are visible in what it saves: a desynced guest falls
back to the setup screen (screenshot) and its tx collapses; a livelock shows SPEED ~0.4% on
both sides.
"""
import json, os, re, shutil, subprocess, sys, time

REPRO = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(REPRO)
sys.path.insert(0, REPRO)
from nav import where, key  # noqa: E402

LABEL = "run"
if "--label" in sys.argv:
    LABEL = sys.argv[sys.argv.index("--label") + 1]
OUT = os.path.join(os.environ.get("SHOTDIR", "/tmp"), f"match-ab-{LABEL}")
LOAD_WAIT = 45      # match start pressed -> assumed in-game
SAMPLE = 90         # in-game observation window
LOGS = {i: f"{ROOT}/work/instances/inst{i}/run.log" for i in ("A", "B")}

SPEED_RE = re.compile(
    r"SPEED (?P<speed>[\d.]+)% emuFps=(?P<fps>[\d.]+) .*?stalls=(?P<stalls>\d+) "
    r"skipped=\d+ stallMs=(?P<stallms>[\d.]+) \((?P<share>[\d.]+)% of wall\).*?"
    r"timeouts=(?P<timeouts>\d+) tx=(?P<tx>\d+) awaiting=\d+ acks=(?P<acks>\d+)")


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, cwd=ROOT, capture_output=True, text=True, **kw)


def prekill():
    """The emulator rewrites pcsx.json on exit, so any survivor must be fully gone before the
    configs are prepared or its exit would silently undo them."""
    sh("pkill -x pcsx-redux; pkill -x AppRun")
    for _ in range(40):
        if not sh("pgrep -x pcsx-redux; pgrep -x AppRun").stdout.strip():
            return
        time.sleep(0.25)
    sh("pkill -9 -x pcsx-redux; pkill -9 -x AppRun")
    time.sleep(2)


def prep_configs():
    """Pin the geometry nav.py's colour signatures are calibrated for: 900x700, no menu bar,
    no debug panels, both windows fully on-screen."""
    for inst, x in (("instA", 100), ("instB", 1400)):
        cfg = f"{ROOT}/work/instances/{inst}/.config/pcsx-redux/pcsx.json"
        d = json.load(open(cfg))
        g = d.setdefault("gui", {})
        g.update(WindowPosX=x, WindowPosY=100, WindowSizeX=900, WindowSizeY=700,
                 WindowMaximized=False, Fullscreen=False, FullWindowRender=True,
                 ShowMenu=False, ShowLog=False, ShowSIO1=False)
        json.dump(d, open(cfg, "w"), indent=2)


def windows():
    """instA and instB window ids, identified by each owning process's HOME — window position
    belongs to the window manager and has already lied once."""
    out = sh(f"{REPRO}/drive.sh wins").stdout.strip().splitlines()
    env = dict(os.environ, DISPLAY=os.environ.get("DISPLAY", ":1"))
    byinst = {}
    for line in out:
        wid, x, y, w, h = line.split()
        pid = subprocess.run(["xdotool", "getwindowpid", wid], env=env,
                             capture_output=True, text=True).stdout.strip()
        if not pid:
            continue
        environ = open(f"/proc/{pid}/environ", "rb").read().decode(errors="replace")
        home = [e.split("=", 1)[1] for e in environ.split("\0") if e.startswith("HOME=")]
        if home:
            # Each instance exposes a frame and a content window; keep the smaller (content).
            byinst.setdefault(os.path.basename(home[0]), []).append((int(w) * int(h), wid))
    return min(byinst["instA"])[1], min(byinst["instB"])[1]


def shot(wid, name):
    subprocess.run([f"{REPRO}/drive.sh", "shot", str(wid), f"{OUT}/{name}.png"],
                   capture_output=True)


def to_menu(wid, tag, limit=40):
    for _ in range(limit):
        state, _ = where(wid, tag)
        if state == "menu":
            return True
        key(wid, "Return")
        time.sleep(4 if state in ("title", "black") else 3)
    return False


def tail_speed(log, offset):
    """SPEED lines appended after `offset`, parsed."""
    with open(log, errors="replace") as f:
        f.seek(offset)
        text = f.read()
    rows = []
    for m in SPEED_RE.finditer(text):
        rows.append({k: float(v) for k, v in m.groupdict().items()})
    return rows, text


def summarize(name, rows):
    if not rows:
        print(f"  {name}: NO SPEED LINES in window")
        return
    mid = lambda k: sorted(r[k] for r in rows)[len(rows) // 2]
    print(f"  {name}: n={len(rows)}  SPEED median={mid('speed'):.1f}% "
          f"(min {min(r['speed'] for r in rows):.1f}, max {max(r['speed'] for r in rows):.1f})  "
          f"emuFps median={mid('fps'):.1f}  stall share median={mid('share'):.1f}%  "
          f"timeouts={int(sum(r['timeouts'] for r in rows))}  "
          f"tx={int(sum(r['tx'] for r in rows))}  acks={int(sum(r['acks'] for r in rows))}")


def main():
    os.makedirs(OUT, exist_ok=True)
    os.environ["SHOTDIR"] = OUT
    knobs = {k: v for k, v in os.environ.items() if k.startswith("PCSX_LINK")}
    print(f"label={LABEL}  knobs={knobs or 'none (baseline)'}", flush=True)

    print("relaunching pair...", flush=True)
    prekill()
    prep_configs()
    subprocess.run("./repro/run-link-test.sh > /tmp/link-launch.log 2>&1",
                   shell=True, cwd=ROOT, timeout=180)
    time.sleep(25)

    ps = sh("ps -o pid= -C pcsx-redux").stdout.strip().splitlines()
    port = sh("ss -tn 2>/dev/null | grep -c 6699").stdout.strip()
    if len(ps) != 2 or int(port) < 2:
        print(f"ABORT: pair integrity failed (procs={len(ps)} conns={port})")
        return 1

    wa, wb = windows()
    print(f"instA={wa} instB={wb}", flush=True)

    # instB first: it becomes the side that sits on WAITING TO CONNECT.
    for name, wid in (("instB", wb), ("instA", wa)):
        print(f"{name}: navigating to main menu...", flush=True)
        if not to_menu(wid, f"nav_{name}"):
            print(f"ABORT: {name} never reached the main menu")
            return 1
        key(wid, "Up")
        time.sleep(1)
        key(wid, "x")
        time.sleep(8)

    print("both: START SETUP", flush=True)
    key(wa, "x")
    time.sleep(20)
    key(wb, "x")
    time.sleep(15)
    shot(wa, "setup_instA")
    shot(wb, "setup_instB")

    # Only the in-game window is measured; setup traffic would pollute the numbers.
    offsets = {i: os.path.getsize(LOGS[i]) for i in ("A", "B")}

    # The sides swap at START SETUP, and which window ends up with "X TO START" has already been
    # misjudged once. Only that side has X bound (GO BACK is triangle), so press X on both.
    print("match start: X on both windows", flush=True)
    key(wb, "x")
    time.sleep(2)
    key(wa, "x")
    time.sleep(15)
    shot(wa, "load_instA")
    shot(wb, "load_instB")
    time.sleep(LOAD_WAIT - 15)
    shot(wa, "ingame_instA")
    shot(wb, "ingame_instB")

    print(f"sampling in-game for {SAMPLE}s...", flush=True)
    time.sleep(SAMPLE)
    shot(wa, "final_instA")
    shot(wb, "final_instB")

    print(f"\n=== {LABEL}: from match start (load + {SAMPLE}s sample) ===")
    for i in ("A", "B"):
        rows, text = tail_speed(LOGS[i], offsets[i])
        with open(f"{OUT}/inst{i}-window.log", "w") as f:
            f.write(text)
        summarize(f"inst{i}", rows)
        # The last few lines are the settled in-game state, past loading transients.
        summarize(f"inst{i} last5", rows[-5:])
        shutil.copy(LOGS[i], f"{OUT}/inst{i}-run.log")

    print(f"\nartifacts: {OUT}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
