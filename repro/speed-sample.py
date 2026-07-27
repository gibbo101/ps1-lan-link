#!/usr/bin/env python3
"""Summarize the most recent SPEED lines from both loopback instances' run.logs.

For measuring a phase a human just played: get in the state to measure, then run this with
roughly how long that state has held. SPEED lines land every ~2s of wall clock.

  python3 repro/speed-sample.py [--seconds 90] [--label note]
"""
import os, re, sys

REPRO = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(REPRO)

SPEED_RE = re.compile(
    r"SPEED (?P<speed>[\d.]+)% emuFps=(?P<fps>[\d.]+) .*?stalls=(?P<stalls>\d+) "
    r"skipped=\d+ stallMs=(?P<stallms>[\d.]+) \((?P<share>[\d.]+)% of wall\).*?"
    r"timeouts=(?P<timeouts>\d+) tx=(?P<tx>\d+) awaiting=\d+ acks=(?P<acks>\d+)")

seconds = 90
if "--seconds" in sys.argv:
    seconds = int(sys.argv[sys.argv.index("--seconds") + 1])
label = ""
if "--label" in sys.argv:
    label = sys.argv[sys.argv.index("--label") + 1]
n = max(1, seconds // 2)

print(f"last {seconds}s (~{n} SPEED lines) {label}")
for i in ("A", "B"):
    log = f"{ROOT}/work/instances/inst{i}/run.log"
    with open(log, errors="replace") as f:
        rows = [{k: float(v) for k, v in m.groupdict().items()}
                for m in SPEED_RE.finditer(f.read())][-n:]
    if not rows:
        print(f"  inst{i}: no SPEED lines")
        continue
    mid = lambda k: sorted(r[k] for r in rows)[len(rows) // 2]
    print(f"  inst{i}: n={len(rows)}  SPEED median={mid('speed'):.1f}% "
          f"(min {min(r['speed'] for r in rows):.1f}, max {max(r['speed'] for r in rows):.1f})  "
          f"emuFps median={mid('fps'):.1f}  stall share median={mid('share'):.1f}%  "
          f"timeouts={int(sum(r['timeouts'] for r in rows))}  "
          f"tx={int(sum(r['tx'] for r in rows))}  acks={int(sum(r['acks'] for r in rows))}")
