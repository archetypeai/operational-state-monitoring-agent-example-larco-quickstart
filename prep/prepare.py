#!/usr/bin/env python3
"""Stage 1b: resample each cycle's vibration to an exact 200 Hz grid and attach its state.

Per cycle: cut into segments at vibration gaps over 1 s and label-timeline
gaps over 1.5 s (never interpolate across a gap); remove physically impossible raw samples
(beyond ±2.2 g); resample each segment onto a 5 ms grid aligned to whole
milliseconds, by cubic spline per channel, rounded to 1e-4 g; give every row the four-state label of the label second it falls
in; drop segments shorter than one window (1,024 rows). Only the 9 vibration
channels are kept: the 1 Hz measurements the labels are derived from stay in
data/raw/labels/ for analysis (plan.md, Scope).

Writes data/prepared/<cycle>.parquet (timestamp, 9 channels as float32,
state, segment) and data/prepare_report.json. Refuses to run unless the
Stage 1a preflight passed.

    .venv/bin/python prep/prepare.py                 # all 199 cycles
    .venv/bin/python prep/prepare.py --only <file>   # one cycle, e.g. to size the output
"""
import argparse
import json
import os
import sys
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd
from scipy.interpolate import CubicSpline

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from larco import DATA  # noqa: E402
from states import CHANNELS, LABEL_COLUMNS, STATES, seconds_state  # noqa: E402

RAW = os.path.join(DATA, "raw")
OUT = os.path.join(DATA, "prepared")
STEP_MS = 5                 # 200 Hz
VIB_GAP_S = 1.0
LABEL_GAP_S = 1.5
MIN_ROWS = 1024             # one window
# The sensor resolves 0.0043 g; rounding the interpolated values to 1e-4 g
# changes nothing measurable (max error 5e-5 g) and cuts the output by ~60%,
# since unrounded interpolation makes every value unique and compresses badly.
DECIMALS = 4
# Physically impossible raw samples (the sensor saturates at ±2.19 g) are
# glitches, e.g. one side.x = 71.5 g; they are removed before resampling.
GLITCH_G = 2.2


def resample(t, v, grid):
    """Cubic-spline resampling of irregular samples (t, v) at `grid` (seconds from the segment start).

    Chosen over linear interpolation by a tone test on the real timestamps
    (plan.md, Stage 1b): it keeps 98-100% of a 23 Hz tone (spin) and 97-99% at
    46 Hz, where linear keeps 94-96% and 82-84%. Times must be relative to the
    segment start: at epoch seconds (~1.7e9) the spline is numerically unstable.
    """
    return CubicSpline(t, v)(grid)


def runs(t, max_gap):
    """(first, last) index pairs of stretches of sorted times with no step over max_gap."""
    cut = np.flatnonzero(np.diff(t) > max_gap)
    starts = np.r_[0, cut + 1]
    ends = np.r_[cut, len(t) - 1]
    return list(zip(starts, ends))


def prepare(name):
    g = pd.read_csv(os.path.join(RAW, "labels", name), usecols=["timestamp"] + LABEL_COLUMNS)
    a = pd.read_parquet(os.path.join(RAW, "vibration", name[:-4] + "_acc.parquet"))
    tg = g.timestamp.to_numpy(float)
    state = seconds_state(g)
    ta = a.timestamp.astype("int64").to_numpy() / 1e9
    x = a[CHANNELS].to_numpy(float)
    glitch = (np.abs(x) > GLITCH_G).any(axis=1)
    ta, x = ta[~glitch], x[~glitch]

    # each label second covers [tg[i], tg[i] + 1); a vibration run covers [ta[s], ta[e]]
    label_spans = [(tg[s], tg[e] + 1.0) for s, e in runs(tg, LABEL_GAP_S)]
    vib_spans = [(s, e) for s, e in runs(ta, VIB_GAP_S)]
    frames, segments, dropped = [], [], []
    for vs, ve in vib_spans:
        for ls, le in label_spans:
            lo, hi = max(ta[vs], ls), min(ta[ve], le)
            if hi <= lo:
                continue
            grid_ms = np.arange(int(np.ceil(lo * 1000 / STEP_MS)) * STEP_MS, int(np.floor(hi * 1000)), STEP_MS,
                                dtype=np.int64)
            if len(grid_ms) < MIN_ROWS:
                dropped.append({"start": float(lo), "seconds": round(float(hi - lo), 1)})
                continue
            origin = ta[vs]
            grid = grid_ms / 1000.0
            seg = pd.DataFrame({"timestamp": pd.to_datetime(grid_ms, unit="ms", utc=True)})
            t_rel, g_rel = ta[vs:ve + 1] - origin, grid - origin
            for j, c in enumerate(CHANNELS):
                y = resample(t_rel, x[vs:ve + 1, j], g_rel)
                seg[c] = np.clip(y, -GLITCH_G, GLITCH_G).round(DECIMALS).astype(np.float32)
            seg["state"] = state[np.clip(np.searchsorted(tg, grid, side="right") - 1, 0, len(tg) - 1)]
            seg["segment"] = np.int16(len(segments))
            segments.append({"start": grid_ms[0] / 1000, "rows": len(grid_ms)})
            frames.append(seg)

    out = pd.concat(frames, ignore_index=True)
    out["state"] = pd.Categorical(out.state, categories=STATES)
    path = os.path.join(OUT, name[:-4] + ".parquet")
    out.to_parquet(path + ".part", index=False, compression="zstd")
    os.replace(path + ".part", path)
    counts = out.state.value_counts()
    return {"file": name, "rows": len(out), "segments": segments, "dropped_short": dropped,
            "glitches_removed": int(glitch.sum()),
            "seconds": {s: round(int(counts.get(s, 0)) * STEP_MS / 1000, 1) for s in STATES},
            "label_seconds": len(tg), "bytes": os.path.getsize(path)}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="+", help="prepare just these cycle files (label CSV names)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    pre = json.load(open(os.path.join(DATA, "preflight_raw.json")))
    if pre.get("failures"):
        sys.exit(f"Stage 1a preflight has {pre['failures']} blocking failure(s): fix or acknowledge them first")
    split = json.load(open(os.path.join(DATA, "split.json")))
    names = sorted({f for r in ("library", "validation", "test") for grp in split[r].values() for f in grp}
                   | set(split["delivery"]))
    if args.only:
        names = [n for n in names if n in set(args.only)]
    os.makedirs(OUT, exist_ok=True)

    t0, results = time.time(), []
    with ProcessPoolExecutor(args.workers) as pool:
        for i, r in enumerate(pool.map(prepare, names), 1):
            results.append(r)
            print(f"  {i}/{len(names)}  {r['file'][3:-4]:<44} {r['rows']:>9,} rows  {len(r['segments'])} segment(s)"
                  f"  {r['bytes'] / 1e6:5.1f} MB  ({time.time() - t0:.0f} s)", flush=True)

    report_path = os.path.join(DATA, "prepare_report.json")
    old = {r["file"]: r for r in json.load(open(report_path))["cycles"]} if os.path.exists(report_path) else {}
    old.update({r["file"]: r for r in results})
    with open(report_path, "w") as f:
        json.dump({"step_ms": STEP_MS, "cycles": [old[k] for k in sorted(old)]}, f, indent=1)
    total = sum(r["bytes"] for r in results)
    print(f"\n{len(results)} cycles, {sum(r['rows'] for r in results):,} rows, {total / 1e9:.2f} GB; "
          f"report {report_path}")


if __name__ == "__main__":
    main()
