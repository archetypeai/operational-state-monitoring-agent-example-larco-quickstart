#!/usr/bin/env python3
"""Stage 2: build the role files the platform receives, from the prepared cycles.

Layout follows the Volve example (data/roles/):
  zscore_stats.json   per-channel mean/std over the library cycles only (global normalisation)
  library/            one file per state, <state>__library.csv: 100 windows per state, spread
                      evenly across the library cycles that have it and evenly spaced within
                      each, as continuous pieces in time order with real timestamps (jumps only
                      between pieces, at whole-window boundaries); the manifest lists every piece
  validation/         the 3 validation cycles, one continuous file per segment, `label` column;
                      all 3 are the search-validation cycles (the manifest lists them)
  test/               the 3 test cycles, same format
  delivery/           the 5 becken-flt cycles, one file per segment, no label column
  delivery_labels/    the held-back labels for delivery: timestamp, label
  manifest.json       every file with its role, cycle, rows and windows; the library draw

Every CSV: `timestamp` (fractional epoch seconds, 3 decimals: the platform
accepts it at 200 Hz, the full example's fit/probe_timestamps.py), then the 9 channels z-scored
with the library statistics, 4 decimals. Refuses to run unless the Stage 1c
preflight passed.

    python prep/build_roles.py                         # all roles (~25 GB, several minutes)
    python prep/build_roles.py --roles library         # just some roles
    python prep/build_roles.py --only <cycle.csv> ...  # just some cycles (a quick check)
"""
import argparse
import json
import os
import shutil
import sys
import time
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from larco import DATA  # noqa: E402
from preflight_common import roles_of  # noqa: E402
from states import CHANNELS, STATES  # noqa: E402

PREPARED = os.path.join(DATA, "prepared")
OUT = os.path.join(DATA, "roles")
WINDOW = 1024
PER_STATE = 100      # 400 in the full example; 8 short library cycles, 1,024-row windows
SEED = 20260928
DECIMALS = 4


def stem(name):
    return name[3:-4]            # wm_<cycle>.csv -> <cycle>


def load(name):
    return pd.read_parquet(os.path.join(PREPARED, name[:-4] + ".parquet"))


def timestamps(ms):
    """Fractional epoch seconds with exactly 3 decimals, from integer milliseconds."""
    ms = np.asarray(ms, dtype=np.int64)
    return pd.Series(ms // 1000).astype(str) + "." + pd.Series(ms % 1000).astype(str).str.zfill(3)


def write_csv(d, path, mean, std, label):
    out = pd.DataFrame({"timestamp": timestamps(d.timestamp.astype("int64").to_numpy()).to_numpy()})
    z = (d[CHANNELS].to_numpy(np.float64) - mean) / std
    for j, c in enumerate(CHANNELS):
        out[c] = z[:, j]
    if label:
        out["label"] = d.state.astype(str).to_numpy()
    out.to_csv(path, index=False, float_format=f"%.{DECIMALS}f")


# --- normalisation -----------------------------------------------------------------

def moments(name):
    x = load(name)[CHANNELS].to_numpy(np.float64)
    return len(x), x.sum(0), (x ** 2).sum(0)


def zscore_stats(library, workers):
    with ProcessPoolExecutor(workers) as pool:
        parts = list(pool.map(moments, library))
    n = sum(p[0] for p in parts)
    s1 = sum(p[1] for p in parts)
    s2 = sum(p[2] for p in parts)
    mean = s1 / n
    std = np.sqrt(np.maximum(s2 / n - mean ** 2, 0))
    return {"channels": CHANNELS, "mean": mean.round(6).tolist(), "std": std.round(6).tolist(),
            "rows": int(n), "from": "all rows of the library cycles (plan.md: global normalisation)",
            "cycles": sorted(library)}


# --- library -----------------------------------------------------------------------

def candidate_windows(name):
    """{state: [(start row, run id)]} of whole single-state windows, runs cut at state changes and segment gaps."""
    d = load(name)[["state", "segment"]]
    st = d.state.astype(str).to_numpy()
    seg = d.segment.to_numpy()
    brk = np.r_[True, (st[1:] != st[:-1]) | (seg[1:] != seg[:-1])]
    starts = np.flatnonzero(brk)
    lengths = np.diff(np.r_[starts, len(st)])
    out = defaultdict(list)
    for run, (s0, n) in enumerate(zip(starts, lengths)):
        for k in range(n // WINDOW):
            out[st[s0]].append((int(s0 + k * WINDOW), run))
    return name, dict(out)


def allocate(available, total):
    """Spread `total` as evenly as possible over cycles, capped by what each has; leftovers go round again."""
    quota = {c: 0 for c in available}
    left = total
    while left > 0:
        open_ = sorted(c for c in available if quota[c] < available[c])
        if not open_:
            break
        share = max(1, left // len(open_))
        for c in open_:
            add = min(share, available[c] - quota[c], left)
            quota[c] += add
            left -= add
            if left == 0:
                break
    return quota


def draw_library(library, workers):
    with ProcessPoolExecutor(workers) as pool:
        cands = dict(pool.map(candidate_windows, library))
    draw = {}
    for state in STATES:
        have = {c: len(cands[c].get(state, [])) for c in library if cands[c].get(state)}
        quota = allocate(have, PER_STATE)
        for c, q in quota.items():
            if q:
                idx = np.unique(np.linspace(0, have[c] - 1, q).round().astype(int))   # evenly spaced
                draw.setdefault(c, {})[state] = [cands[c][state][i] for i in idx]
    return draw, {s: sum(len(v.get(s, [])) for v in draw.values()) for s in STATES}


def library_pieces(name, chosen):
    """(state, start ms, prepared rows) per group of adjacent chosen windows in one cycle.

    A piece is continuous by construction (one run of one state, whole windows).
    """
    d = load(name)
    out = []
    for state, wins in chosen.items():
        wins = sorted(wins)
        groups, cur = [], [wins[0]]
        for w in wins[1:]:
            if w[1] == cur[-1][1] and w[0] == cur[-1][0] + WINDOW:
                cur.append(w)
            else:
                groups.append(cur)
                cur = [w]
        groups.append(cur)
        for g in groups:
            part = d.iloc[g[0][0]:g[-1][0] + WINDOW]
            out.append((state, int(part.timestamp.iloc[0].value // 1_000_000), name, part))
    return out


def write_library(draw, mean, std, workers):
    """One file per state: its pieces from every cycle, in time order, real timestamps.

    Jumps between pieces fall on whole-window boundaries, never inside a piece. One
    training example per state keeps the optimization config far under the platform's
    1 MiB config limit, which 1,990 separate files exceeded in the full example.
    Training files tolerate such jumps, even where a window crosses one
    (the full example's fit/probe_gaps.py, runs O1-O3).
    """
    with ProcessPoolExecutor(workers) as pool:
        pieces = sum(pool.map(library_pieces, list(draw), [draw[c] for c in draw]), [])
    files = []
    for state in STATES:
        mine = sorted((p for p in pieces if p[0] == state), key=lambda p: p[1])
        if not mine:
            continue
        part = pd.concat([p[3] for p in mine], ignore_index=True)
        ms = part.timestamp.astype("int64").to_numpy()
        assert (np.diff(ms) > 0).all(), f"{state}: pieces overlap in time"
        fname = f"{state}__library.csv"
        write_csv(part, os.path.join(OUT, "library", fname), mean, std, label=False)
        rows, piece_list = 0, []
        for _, ms0, cycle, p in mine:
            piece_list.append({"cycle": cycle, "start_ms": ms0, "row": rows, "rows": len(p), "windows": len(p) // WINDOW})
            rows += len(p)
        files.append({"file": f"library/{fname}", "state": state, "rows": rows, "windows": rows // WINDOW,
                      "pieces": piece_list, "cycles": sorted({p["cycle"] for p in piece_list})})
    return files


# --- continuous roles ---------------------------------------------------------------

def write_continuous(role, name, mean, std):
    d = load(name)
    files = []
    for seg, part in d.groupby("segment", sort=True):
        fname = f"{stem(name)}__seg{int(seg)}.csv"
        if role == "delivery":
            write_csv(part, os.path.join(OUT, "delivery", fname), mean, std, label=False)
            lab = pd.DataFrame({"timestamp": timestamps(part.timestamp.astype("int64").to_numpy()).to_numpy(),
                                "label": part.state.astype(str).to_numpy()})
            lab.to_csv(os.path.join(OUT, "delivery_labels", fname), index=False)
        else:
            write_csv(part, os.path.join(OUT, role, fname), mean, std, label=True)
        counts = part.state.astype(str).value_counts()
        files.append({"file": f"{role}/{fname}", "cycle": name, "segment": int(seg), "rows": len(part),
                      "windows": len(part) // WINDOW,
                      "seconds": {s: round(int(counts.get(s, 0)) * 0.005, 1) for s in STATES}})
    return files


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--roles", nargs="+", default=["library", "validation", "test", "delivery"],
                    choices=["library", "validation", "test", "delivery"])
    ap.add_argument("--only", nargs="+", help="restrict the continuous roles to these cycles (a quick check)")
    ap.add_argument("--workers", type=int, default=6)
    args = ap.parse_args()

    rep = json.load(open(os.path.join(DATA, "preflight_prepared.json")))
    if rep.get("failures"):
        sys.exit("Stage 1c preflight has blocking failures: fix those first")
    split = json.load(open(os.path.join(DATA, "split.json")))
    roles = roles_of(split)
    by_role = defaultdict(list)
    for f, r in roles.items():
        by_role[r].append(f)
    library = sorted(by_role["library"])
    t0 = time.time()

    stats_path = os.path.join(OUT, "zscore_stats.json")
    os.makedirs(OUT, exist_ok=True)
    stats = zscore_stats(library, args.workers)
    with open(stats_path, "w") as f:
        json.dump(stats, f, indent=1)
    mean, std = np.array(stats["mean"]), np.array(stats["std"])
    print(f"z-score stats from {len(library)} library cycles, {stats['rows']:,} rows ({time.time() - t0:.0f} s)", flush=True)

    manifest_path = os.path.join(OUT, "manifest.json")
    manifest = json.load(open(manifest_path)) if os.path.exists(manifest_path) else {}
    manifest.update({"window": WINDOW, "timestamp_format": "fractional epoch seconds, 3 decimals",
                     "decimals": DECIMALS, "zscore_stats": "zscore_stats.json",
                     "search_validation": sorted(by_role["validation"])})
    for role in args.roles:
        dirs = [role] + (["delivery_labels"] if role == "delivery" else [])
        for dname in dirs:
            if not args.only:
                shutil.rmtree(os.path.join(OUT, dname), ignore_errors=True)
            os.makedirs(os.path.join(OUT, dname), exist_ok=True)

        if role == "library":
            draw, per_state = draw_library(library, args.workers)
            files = write_library(draw, mean, std, args.workers)
            manifest["library"] = {"per_state_target": PER_STATE, "windows_per_state": per_state,
                                   "cycles_per_state": {s: sum(1 for v in draw.values() if s in v) for s in STATES},
                                   "files": files}
            print(f"library: {len(files)} files (one per state, {sum(len(f['pieces']) for f in files)} pieces), "
                  f"windows per state {per_state} ({time.time() - t0:.0f} s)", flush=True)
            continue

        names = sorted(by_role[role])
        if args.only:
            names = [n for n in names if n in set(args.only)]
        with ProcessPoolExecutor(args.workers) as pool:
            files = sum(pool.map(write_continuous, [role] * len(names), names, [mean] * len(names),
                                 [std] * len(names)), [])
        if args.only and role in manifest:
            keep = [f for f in manifest[role]["files"] if f["cycle"] not in set(names)]
            files = keep + files
        manifest[role] = {"cycles": len({f["cycle"] for f in files}), "files": files}
        w = sum(f["windows"] for f in files)
        print(f"{role}: {len(files)} files from {len(names)} cycles, {w:,} windows ({time.time() - t0:.0f} s)", flush=True)

    with open(manifest_path, "w") as f:
        json.dump(manifest, f, indent=1)
    total = sum(os.path.getsize(os.path.join(dp, fn)) for dp, _, fns in os.walk(OUT) for fn in fns)
    print(f"\nwrote {OUT}: {total / 1e9:.2f} GB in all; manifest {manifest_path} ({time.time() - t0:.0f} s)")


if __name__ == "__main__":
    main()
