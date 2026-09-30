#!/usr/bin/env python3
"""Stage 3: preflight the role files against the platform's rules, read-only, before any upload.

Per file (data/roles/): the header and timestamp format; a strictly regular
5 ms timeline (a jump makes the platform reject a whole validation/test file);
finite values; labels only from the four states, none blank; whole windows;
library files (one per state, pieces from many cycles) holding one state in every piece,
matching the prepared data, with time jumps only between pieces at whole-window boundaries; delivery files
carrying no label, with a sidecar covering every row; and values scaled with
the library statistics (spot-checked against the prepared data). Per role: no
setting group or cycle in two roles, becken-flt only in delivery, the library
at 100 windows per state, and every validation and test set (pooled)
containing all four states, because the platform
scores a missing state as F1 = 0 (the full example's fit/probe_timestamps.py).

Writes data/preflight_roles.json; exits 1 on any blocking FAIL.

    python prep/preflight_roles.py       # ~2-4 min, reads all 25 GB
"""
import functools
import json
import os
import re
import sys
from collections import Counter, defaultdict
from concurrent.futures import ProcessPoolExecutor

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from larco import DATA  # noqa: E402
from preflight_common import report, roles_of  # noqa: E402
from states import CHANNELS, STATES  # noqa: E402

ROLES = os.path.join(DATA, "roles")
PREPARED = os.path.join(DATA, "prepared")
WINDOW = 1024
STEP_MS = 5
PER_STATE = 100      # build_roles.py
SPOT_ROWS = 5               # rows per file compared with the prepared data
SCALE_TOL = 6e-5            # 4-decimal rounding of z plus float32 storage


def read(path, label):
    usecols = ["timestamp"] + CHANNELS + (["label"] if label else [])
    head = open(path).readline().strip().split(",")
    d = pd.read_csv(path, dtype={"timestamp": str}, engine="pyarrow")
    return head, usecols, d


@functools.lru_cache(maxsize=2)   # library pieces are in time order, so a cycle's pieces are consecutive
def prepared(cycle):
    """(timestamps in ms, states, channel values) of a prepared cycle, cached per worker process."""
    p = pd.read_parquet(os.path.join(PREPARED, cycle[:-4] + ".parquet"), columns=["timestamp", "state"] + CHANNELS)
    return p.timestamp.astype("int64").to_numpy(), p.state.astype(str).to_numpy(), p[CHANNELS].to_numpy(np.float64)


def spot_check(d, cycle, mean, std):
    """Largest |z - (prepared - mean) / std| over a few rows spread through the file."""
    pms, _, px = prepared(cycle)
    idx = np.unique(np.linspace(0, len(d) - 1, SPOT_ROWS).astype(int))
    ms = np.round(d.timestamp.iloc[idx].astype(float).to_numpy() * 1000).astype(np.int64)
    at = np.searchsorted(pms, ms)
    if (at >= len(pms)).any() or (pms[np.minimum(at, len(pms) - 1)] != ms).any():
        return None
    want = (px[at] - mean) / std
    return float(np.abs(d[CHANNELS].to_numpy(np.float64)[idx] - want).max())


def check_file(role, rel, cycle, expect_state, mean, std, pieces=None):
    out = {"file": rel, "role": role, "cycle": cycle, "checks": {},
           "cycles": sorted({p["cycle"] for p in pieces}) if pieces else None}
    chk = out["checks"]
    path = os.path.join(ROLES, rel)
    labelled = role in ("validation", "test")
    head, usecols, d = read(path, labelled)
    chk["header"] = ("PASS", "") if head == usecols else ("FAIL", f"columns {head}")
    if head != usecols:
        return out

    ts = d.timestamp
    # the format as written: the CSV reader converts numbers, so check the raw text
    with open(path) as fh:
        next(fh)
        fmt_ok = all(re.fullmatch(r"\d{10}\.\d{3}", line.split(",", 1)[0]) for line, _ in zip(fh, range(2000)))
    ms = np.round(ts.astype(float).to_numpy() * 1000).astype(np.int64)
    steps = np.diff(ms)
    jump_rows = set((np.flatnonzero(steps != STEP_MS) + 1).tolist())
    if role == "library":
        # jumps allowed only where a new piece starts, always a whole-window boundary
        starts = {p["row"] for p in pieces[1:]}
        stray = sorted(jump_rows - starts)
        unaligned = sorted(r for r in starts if r % WINDOW)
        backwards = int((steps <= 0).sum())
        bad = len(stray) + len(unaligned) + backwards
        ok_msg = f"{len(d):,} rows, {STEP_MS} ms steps within {len(pieces)} pieces, forward jumps only between them"
        bad_msg = (f"jumps inside pieces {stray[:3]}, piece starts off a {WINDOW}-row boundary {unaligned[:3]}, "
                   f"{backwards} non-increasing steps")
    else:
        bad = len(jump_rows)
        ok_msg = f"{len(d):,} rows, every step {STEP_MS} ms"
        bad_msg = f"{bad} steps not exactly {STEP_MS} ms"
    if not fmt_ok or bad:
        chk["timeline"] = ("FAIL", f"{'timestamps not epoch seconds with 3 decimals; ' if not fmt_ok else ''}{bad_msg}")
    else:
        chk["timeline"] = ("PASS", ok_msg)

    x = d[CHANNELS].to_numpy(np.float64)
    n_bad = int((~np.isfinite(x)).sum())
    chk["values"] = ("FAIL", f"{n_bad} NaN/inf") if n_bad else ("PASS", "")
    windows = len(d) // WINDOW
    out["windows"] = windows
    if role == "library" and len(d) % WINDOW:
        chk["windows"] = ("FAIL", f"{len(d)} rows, not a whole number of {WINDOW}-row windows")
    elif windows < 1:
        chk["windows"] = ("FAIL", f"{len(d)} rows, less than one window")
    else:
        chk["windows"] = ("PASS", f"{windows} window(s)")

    if labelled:
        lab = d.label.astype(object)
        blank = int(lab.isna().sum() + (lab.astype(str).str.strip() == "").sum())
        unknown = sorted(set(lab.dropna()) - set(STATES))
        chk["labels"] = ("FAIL", f"{blank} blank, unknown {unknown}") if blank or unknown else ("PASS", "")
        out["seconds"] = {s: round(float((lab == s).sum()) * STEP_MS / 1000, 1) for s in STATES}
    elif role == "delivery":
        side = os.path.join(ROLES, "delivery_labels", os.path.basename(rel))
        if not os.path.exists(side):
            chk["held-back labels"] = ("FAIL", "no sidecar in delivery_labels/")
        else:
            s = pd.read_csv(side, dtype={"timestamp": str}, engine="pyarrow")
            same = len(s) == len(d) and bool((np.round(s.timestamp.astype(float).to_numpy() * 1000).astype(np.int64) == ms).all())
            unknown = sorted(set(s.label.dropna()) - set(STATES))
            ok = same and not unknown and not s.label.isna().any()
            chk["held-back labels"] = ("PASS", "") if ok else (
                "FAIL", f"sidecar rows match: {same}; unknown or blank labels: {unknown or s.label.isna().sum()}")
            out["seconds"] = {st: round(float((s.label == st).sum()) * STEP_MS / 1000, 1) for st in STATES}
    else:  # library: every row of every piece must be the file's state in its cycle's prepared data
        wrong = []
        for p in pieces:
            pms, pstate, _ = prepared(p["cycle"])
            i0 = int(np.searchsorted(pms, p["start_ms"]))
            span = pstate[i0:i0 + p["rows"]]
            here = ms[p["row"]:p["row"] + p["rows"]]
            ok = (i0 < len(pms) and len(span) == p["rows"] and (span == expect_state).all()
                  and (pms[i0:i0 + p["rows"]] == here).all())
            if not ok:
                wrong.append(f"{p['cycle'][3:-4]}@{p['start_ms']}")
        chk["one state"] = ("PASS", f"{expect_state}, {len(pieces)} pieces from {len({p['cycle'] for p in pieces})} cycles") \
            if not wrong else ("FAIL", f"{len(wrong)} piece(s) not all {expect_state} or not matching: {wrong[:3]}")

    if role == "library":
        errs = [spot_check(d.iloc[p["row"]:p["row"] + p["rows"]], p["cycle"], mean, std) for p in pieces[::25]]
        err = None if any(e is None for e in errs) else max(errs)
    else:
        err = spot_check(d, cycle, mean, std)
    if err is None:
        chk["scaling"] = ("FAIL", "timestamps not found in the prepared data")
    else:
        chk["scaling"] = ("PASS", f"max error {err:.1e}") if err <= SCALE_TOL else ("FAIL", f"max error {err:.1e}")
    return out


def role_checks(manifest, split, stats, results):
    """Checks on whole roles, reported as one pseudo-file per role."""
    out = []
    roles = roles_of(split)
    by = defaultdict(list)
    for r in results:
        by[r["role"]].append(r)

    # separation
    group_of = {f: g for role in ("library", "validation", "test") for g, fs in split[role].items() for f in fs}
    seen = defaultdict(set)
    for r in results:
        for c in (r.get("cycles") or [r["cycle"]]):
            seen[c].add(r["role"])
    crossed = sorted(c for c, rs in seen.items() if len(rs) > 1)
    wrong = sorted(c for c, rs in seen.items() if rs != {roles.get(c)})
    flt = sorted(c for c, rs in seen.items() if "becken-flt" in c and rs != {"delivery"})
    g_roles = defaultdict(set)
    for c, rs in seen.items():
        if c in group_of:
            g_roles[group_of[c]] |= rs
    g_crossed = sorted(g for g, rs in g_roles.items() if len(rs) > 1)
    msg = f"cycles in two roles {crossed}, off-split {wrong}, becken-flt outside delivery {flt}, groups in two roles {g_crossed}"
    out.append({"file": "(all roles)", "role": "roles", "scope": "role", "checks": {
        "separation": ("FAIL", msg) if crossed or wrong or flt or g_crossed else ("PASS", "no cycle or setting group in two roles")}})

    # normalisation provenance
    lib_cycles = sorted({f for fs in split["library"].values() for f in fs})
    prov_ok = sorted(stats["cycles"]) == lib_cycles
    out.append({"file": "zscore_stats.json", "role": "roles", "scope": "role", "checks": {
        "normalisation": ("PASS", f"from the {len(lib_cycles)} library cycles only") if prov_ok
        else ("FAIL", "statistics not computed from exactly the library cycles")}})

    # library balance
    per_state = Counter()
    for r in by["library"]:
        per_state[os.path.basename(r["file"]).split("__")[0]] += r.get("windows", 0)
    lib_ok = all(per_state[s] == PER_STATE for s in STATES)
    out.append({"file": "library/", "role": "library", "scope": "role", "checks": {
        "balance": ("PASS", dict(per_state)) if lib_ok else ("FAIL", f"windows per state {dict(per_state)}")}})

    # every state present, pooled, where a score is computed
    pools = {"validation": by["validation"], "test": by["test"], "delivery": by["delivery"]}
    for name, rows in pools.items():
        secs = Counter()
        for r in rows:
            secs.update(r.get("seconds", {}))
        missing = [s for s in STATES if secs[s] == 0]
        hours = {s: round(secs[s] / 3600, 2) for s in STATES}
        out.append({"file": name, "role": name.split()[0], "scope": "role", "checks": {
            "all four states": ("FAIL", f"missing {missing}; hours {hours}") if missing else ("PASS", f"hours {hours}")}})
    return out


def main():
    for stage, path in (("Stage 1c", "preflight_prepared.json"),):
        if json.load(open(os.path.join(DATA, path))).get("failures"):
            sys.exit(f"{stage} preflight has blocking failures: fix those first")
    manifest = json.load(open(os.path.join(ROLES, "manifest.json")))
    stats = json.load(open(os.path.join(ROLES, "zscore_stats.json")))
    split = json.load(open(os.path.join(DATA, "split.json")))
    mean, std = np.array(stats["mean"]), np.array(stats["std"])

    jobs = []
    for role in ("library", "validation", "test", "delivery"):
        for f in manifest[role]["files"]:
            jobs.append((role, f["file"], f.get("cycle", ""), f.get("state"), f.get("pieces")))
    jobs.sort(key=lambda j: j[2])          # a cycle's files together, so each worker reads it once
    on_disk = {os.path.join(r, n) for r in ("library", "validation", "test", "delivery")
               for n in os.listdir(os.path.join(ROLES, r))}
    listed = {j[1] for j in jobs}
    with ProcessPoolExecutor(min(6, os.cpu_count() or 1)) as pool:
        results = list(pool.map(check_file, *[list(c) for c in zip(*[j[:4] for j in jobs])],
                                [mean] * len(jobs), [std] * len(jobs), [j[4] for j in jobs], chunksize=4))
    extra = {"file": "manifest.json", "role": "roles", "scope": "role", "checks": {
        "manifest": ("PASS", f"{len(listed)} files listed and on disk") if on_disk == listed
        else ("FAIL", f"on disk but not listed {sorted(on_disk - listed)[:3]}, listed but missing {sorted(listed - on_disk)[:3]}")}}
    results += [extra] + role_checks(manifest, split, stats, results)

    lines = ["Windows per role:"]
    for role in ("library", "validation", "test", "delivery"):
        rows = [r for r in results if r["role"] == role and "windows" in r]
        lines.append(f"  {role:<10} {len(rows):>5} files  {sum(r['windows'] for r in rows):>8,} windows")
    n_fail = report(results, f"Stage 3 preflight: {len(jobs)} role files", "Stage 4",
                    os.path.join(DATA, "preflight_roles.json"), notes="\n".join(lines))
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
