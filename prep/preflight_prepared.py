#!/usr/bin/env python3
"""Stage 1c: preflight the prepared files, read-only, before role files are built.

For every cycle in data/split.json, checks data/prepared/<cycle>.parquet:
the exact 5 ms grid, values, states, segments and columns; and, against the
raw files, that Stage 1b did what it claims: each row's state matches the
four-state rule for its label second, and a tone test on the cycle's own raw
timestamps shows how well its resampling keeps 10, 23 and 46 Hz. Prints PASS / WARN / FAIL per check, writes
data/preflight_prepared.json, exits 1 on any blocking FAIL.

    .venv/bin/python prep/preflight_prepared.py
"""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from larco import DATA  # noqa: E402
from preflight_common import report, roles_of  # noqa: E402
from prepare import resample  # noqa: E402
from states import CHANNELS, LABEL_COLUMNS, STATES, seconds_state  # noqa: E402

RAW = os.path.join(DATA, "raw")
PREPARED = os.path.join(DATA, "prepared")
COLUMNS = ["timestamp"] + CHANNELS + ["state", "segment"]
STEP_MS = 5
MIN_ROWS = 1024
CLIP_G = 2.2                # the sensor saturates at ±2.19 g; interpolation cannot exceed it
LOST_FAIL = 0.20            # a cycle that lost more than this share of its labelled seconds
# tone test (plan.md, Stage 1b): share of a pure tone's amplitude surviving resampling
TONES_HZ = [10, 23, 46]     # 23 Hz = spin at 1400 rpm, 46 Hz its 2nd harmonic
TONE_FAIL = 0.80            # spin itself badly damped
TONE_WARN_23, TONE_WARN_46 = 0.95, 0.90


def check_cycle(role, name):
    out = {"file": name, "role": role, "checks": {}}
    chk = out["checks"]
    path = os.path.join(PREPARED, name[:-4] + ".parquet")
    if not os.path.exists(path):
        chk["file"] = ("FAIL", "no prepared file")
        return out
    chk["file"] = ("PASS", "")
    d = pd.read_parquet(path)

    cols_ok = list(d.columns) == COLUMNS
    types_ok = all(d[c].dtype == np.float32 for c in CHANNELS if c in d)
    chk["columns"] = ("PASS", "") if cols_ok and types_ok else (
        "FAIL", f"columns {list(d.columns)}" if not cols_ok else "channels not float32")
    if not cols_ok:
        return out

    t = d.timestamp.astype("int64").to_numpy()          # milliseconds
    seg = d.segment.to_numpy()
    same = seg[1:] == seg[:-1]
    steps = np.diff(t)
    bad_steps = int((steps[same] != STEP_MS).sum())
    order_ok = bool((np.diff(seg) >= 0).all() and (steps[~same] > STEP_MS).all())
    if bad_steps or not order_ok:
        chk["grid"] = ("FAIL", f"{bad_steps} steps within a segment not exactly {STEP_MS} ms; "
                               f"segments ordered and apart: {order_ok}")
    else:
        chk["grid"] = ("PASS", f"{len(d):,} rows, all steps {STEP_MS} ms")

    x = d[CHANNELS].to_numpy()
    n_bad = int((~np.isfinite(x)).sum())
    n_big = int((np.abs(x) > CLIP_G).sum())
    chk["values"] = ("FAIL", f"{n_bad} NaN/inf, {n_big} beyond ±{CLIP_G} g") if n_bad or n_big else ("PASS", "")

    st = d.state.astype(object).to_numpy()
    unknown = sorted(set(pd.unique(st)) - set(STATES), key=str)
    chk["states"] = ("FAIL", f"unknown or missing states {unknown}") if unknown else ("PASS", "")

    lengths = np.bincount(seg)
    short = int((lengths[lengths > 0] < MIN_ROWS).sum())
    g = pd.read_csv(os.path.join(RAW, "labels", name), usecols=["timestamp"] + LABEL_COLUMNS)
    tg = g.timestamp.to_numpy(float)
    kept = len(d) * STEP_MS / 1000 / len(tg)
    out["kept_share"] = round(float(kept), 4)
    if short:
        chk["coverage"] = ("FAIL", f"{short} segment(s) shorter than {MIN_ROWS} rows")
    elif kept < 1 - LOST_FAIL:
        chk["coverage"] = ("FAIL", f"kept {kept:.1%} of the labelled seconds")
    else:
        chk["coverage"] = ("PASS", f"{int((lengths > 0).sum())} segment(s), kept {kept:.1%}")

    # Stage 1b's claims, recomputed from the raw files
    expected = seconds_state(g)[np.clip(np.searchsorted(tg, t / 1000, side="right") - 1, 0, len(tg) - 1)]
    mism = int((expected != st).sum())
    chk["state match"] = ("FAIL", f"{mism:,} rows' state differs from the rule") if mism else ("PASS", "")

    # resampling quality on this cycle's own timestamps: sample known tones at
    # the raw times, resample with Stage 1b's function, measure what survives
    a = pd.read_parquet(os.path.join(RAW, "vibration", name[:-4] + "_acc.parquet"), columns=["timestamp"])
    T = a.timestamp.astype("int64").to_numpy() / 1e9
    kept = {f: [] for f in TONES_HZ}
    for where in (0.2, 0.5, 0.8):
        i = int(len(T) * where)
        t = T[i:i + 200 * 60]
        t = t - t[0]
        if len(t) < 1000 or t[-1] < 30:
            continue
        grid = np.arange(0.01, t[-1] - 0.01, STEP_MS / 1000)
        for f in TONES_HZ:
            y = resample(t, np.sin(2 * np.pi * f * t), grid)
            kept[f].append(2 * np.hypot((y * np.cos(2 * np.pi * f * grid)).mean(), (y * np.sin(2 * np.pi * f * grid)).mean()))
    worst = {f: round(float(min(v)), 3) for f, v in kept.items() if v}
    out["tone_kept"] = worst
    msg = ", ".join(f"{f} Hz {k:.2f}" for f, k in worst.items())
    if worst and worst[23] < TONE_FAIL:
        chk["resampling"] = ("FAIL", f"worst of 3 stretches: {msg}")
    elif worst and (worst[23] < TONE_WARN_23 or worst[46] < TONE_WARN_46):
        chk["resampling"] = ("WARN", f"worst of 3 stretches: {msg}")
    else:
        chk["resampling"] = ("PASS", msg)
    out["seconds"] = {s: round(float((st == s).sum()) * STEP_MS / 1000, 1) for s in STATES}
    return out


def main():
    split = json.load(open(os.path.join(DATA, "split.json")))
    roles = roles_of(split)
    for stage, path in (("Stage 1a", "preflight_raw.json"), ("Stage 1b", "prepare_report.json")):
        if not os.path.exists(os.path.join(DATA, path)):
            sys.exit(f"{stage} has not run (no data/{path}): run it first")
    if json.load(open(os.path.join(DATA, "preflight_raw.json"))).get("failures"):
        sys.exit("Stage 1a preflight has blocking failures: fix those first")
    names = sorted(roles)
    with ProcessPoolExecutor(min(6, os.cpu_count() or 1)) as pool:
        results = list(pool.map(check_cycle, [roles[n] for n in names], names))

    lines = ["Prepared hours per state, by role:"]
    for role in ("library", "validation", "test", "delivery"):
        rows = [r for r in results if r["role"] == role and "seconds" in r]
        lines.append(f"  {role:<10} " + "  ".join(f"{s} {sum(r['seconds'][s] for r in rows) / 3600:6.1f}" for s in STATES))
    n_fail = report(results, f"Stage 1c preflight: {len(results)} prepared cycles", "Stage 2",
                    os.path.join(DATA, "preflight_prepared.json"), notes="\n".join(lines))
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
