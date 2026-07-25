#!/usr/bin/env python3
"""Derive emulation speed from a PCSX-Redux SIO1-DIAG log.

Every diag line carries host wall time (t=) and the emulated cycle counter (cyc=), so
speed = dcyc / (PSX_CLOCK * dt). 1.0 = full speed; 0.017 = ~1 fps on a 60 fps target.
Reports a per-window trace so stalls are visible rather than averaged away.

  emuspeed.py <log> [window_seconds]
"""
import re, sys

PSX_CLOCK = 33_868_800.0
LINE = re.compile(r"\bt=(\d+\.\d+).*?\bcyc=(\d+)")


def main(path, window=5.0):
    pts = []
    with open(path, errors="replace") as f:
        for ln in f:
            m = LINE.search(ln)
            if m:
                pts.append((float(m.group(1)), int(m.group(2))))
    if len(pts) < 2:
        print(f"only {len(pts)} timestamped diag points — cannot measure")
        return
    t0, c0 = pts[0]
    t1, c1 = pts[-1]
    print(f"diag points: {len(pts)}")
    print(f"wall span:   {t1 - t0:.1f} s   ({t0:.3f} -> {t1:.3f})")
    print(f"emu span:    {(c1 - c0) / PSX_CLOCK:.1f} s emulated")
    overall = (c1 - c0) / PSX_CLOCK / (t1 - t0) if t1 > t0 else 0
    print(f"OVERALL:     {overall * 100:.2f}% speed  (~{overall * 60:.2f} fps equivalent)")
    print()
    print(f"per-{window:g}s windows:")
    wstart, cstart = pts[0]
    prev = pts[0]
    for t, c in pts[1:]:
        if t - wstart >= window:
            sp = (c - cstart) / PSX_CLOCK / (t - wstart)
            bar = "#" * min(50, int(sp * 50))
            print(f"  t+{wstart - t0:7.1f}s  {sp * 100:6.2f}%  ~{sp * 60:5.2f} fps  {bar}")
            wstart, cstart = t, c
        prev = (t, c)
    # Largest single wall-clock gap: the worst individual stall.
    gaps = [(pts[i + 1][0] - pts[i][0], pts[i][0] - t0) for i in range(len(pts) - 1)]
    gaps.sort(reverse=True)
    print()
    print("largest wall gaps between diag lines (s @ t+):")
    for g, at in gaps[:5]:
        print(f"  {g:.3f} s  @ t+{at:.1f}s")


if __name__ == "__main__":
    main(sys.argv[1], float(sys.argv[2]) if len(sys.argv) > 2 else 5.0)
