#!/usr/bin/env python3
"""Stage 1a: preflight the raw cycles, read-only, before anything is prepared.

For every cycle in data/split.json (both units), checks what later stages
assume: files complete, schema, label values, the label/vibration clock join,
vibration rate and gaps, flat or clipped channels, and that each cycle has
fill and spin. Prints one PASS / WARN / FAIL line per check, lists the cycles
behind every WARN and FAIL, and writes data/preflight_raw.json. Exits 1 on
any FAIL, so Stage 1b does not run on bad input.

Also measures, per cycle, the vibration level in each state, including the
four states (heating is folded into its drum/water state, prep/states.py).

    .venv/bin/python prep/preflight_raw.py
"""
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from larco import DATA  # noqa: E402
from preflight_common import report  # noqa: E402
from states import CHANNELS, STATES, seconds_state as phases  # noqa: E402

RAW = os.path.join(DATA, "raw")
LABEL_COLUMNS = ["timestamp", "power", "centrifuge_label", "water_label", "heating_label"]
# water_label 2 = inlet and outlet flowing at once, 2-8 s at a fill -> drain switch (Stage 1a finding)
LABEL_VALUES = {"centrifuge_label": {0, 1}, "water_label": {-1, 0, 1, 2}, "heating_label": {0, 1}}
CLIP_G = 2.18                # the sensors saturate at ±2.19 g
GLITCH_G = 2.2               # beyond this a sample is physically impossible
COVER_WARN, COVER_FAIL = 0.98, 0.5   # share of label seconds with vibration
# vibration vs label at spin ends: over all 370 measurable edges the median is
# +3 s, 95% of cycles' medians fall within ±19 s, and edges scatter ~20 s
# within one cycle (label precision). Only a cycle beyond that is flagged.
ALIGN_WARN_S = 30
RATE_HZ = (140.0, 220.0)
RATE_DRIFT = 0.10                    # hourly median rate changing by more than this
GAP_S = 1.0
FLAT_STD = 1e-4
CLIP_WARN_FRACTION = 0.001
MIN_STATE_S = 60
PHASES = STATES



def check_cycle(role, unit, name):
    out = {"file": name, "role": role, "unit": unit, "checks": {}}
    chk = out["checks"]
    label_path = os.path.join(RAW, "labels", name)
    vib_path = os.path.join(RAW, "vibration", name[:-4] + "_acc.parquet")
    missing = [p for p in (label_path, vib_path) if not os.path.exists(p)]
    if missing:
        chk["files"] = ("FAIL", f"missing {', '.join(os.path.basename(p) for p in missing)}")
        return out
    chk["files"] = ("PASS", "")

    g = pd.read_csv(label_path)
    a = pd.read_parquet(vib_path)
    lack = [c for c in LABEL_COLUMNS if c not in g] + [c for c in ["timestamp"] + CHANNELS if c not in a]
    if lack:
        chk["schema"] = ("FAIL", f"missing columns {lack}")
        return out
    chk["schema"] = ("PASS", "")

    bad = {c: sorted(set(g[c].dropna().unique()) - v) for c, v in LABEL_VALUES.items()}
    bad = {c: v for c, v in bad.items() if v}
    nan = {c: int(g[c].isna().sum()) for c in LABEL_VALUES if g[c].isna().any()}
    chk["label values"] = ("FAIL", f"unexpected {bad} NaN {nan}") if bad or nan else ("PASS", "")

    tg = pd.to_numeric(g.timestamp, errors="coerce").to_numpy(float)
    ta = a.timestamp.astype("int64").to_numpy() / 1e9
    out["date"] = datetime.fromtimestamp(tg[0], timezone.utc).strftime("%Y-%m-%d")
    out["minutes"] = round((tg[-1] - tg[0]) / 60, 1)
    out["offset_s"] = {"start": round(float(ta[0] - tg[0]), 1), "end": round(float(ta[-1] - tg[-1]), 1)}
    label_gaps = np.diff(tg)
    if (label_gaps > 1.5).any() or (label_gaps <= 0).any():
        chk["label timeline"] = ("WARN", f"{int((label_gaps > 1.5).sum())} gaps > 1.5 s, "
                                 f"longest {label_gaps.max():.0f} s, {int((label_gaps <= 0).sum())} non-increasing")
    else:
        chk["label timeline"] = ("PASS", "")

    dt = np.diff(ta)
    rate = 1 / np.median(dt)
    out["rate_hz"] = round(float(rate), 1)
    hourly = [1 / np.median(c) for c in np.array_split(dt, max(1, int(out["minutes"] // 60)))]
    drift = (max(hourly) - min(hourly)) / max(hourly)
    gaps = dt[dt > GAP_S]
    notes = []
    if drift > RATE_DRIFT:
        notes.append(f"rate changes {max(hourly):.0f} -> {min(hourly):.0f} Hz within the cycle")
    if len(gaps):
        notes.append(f"{len(gaps)} gaps > {GAP_S:.0f} s, longest {gaps.max():.1f} s")
    if not RATE_HZ[0] <= rate <= RATE_HZ[1] or (dt <= 0).any():
        chk["vibration rate"] = ("FAIL", f"median {rate:.1f} Hz, {int((dt <= 0).sum())} non-increasing")
    elif notes:
        chk["vibration rate"] = ("WARN", f"{rate:.1f} Hz; " + "; ".join(notes))
    else:
        chk["vibration rate"] = ("PASS", f"{rate:.1f} Hz")

    x = a[CHANNELS].to_numpy(float)
    std = x.std(0)
    clip = (np.abs(x) >= CLIP_G).mean(0)
    glitches = int((np.abs(x) > GLITCH_G).any(axis=1).sum())
    out["glitches"] = glitches
    chk["glitches"] = ("WARN", f"{glitches} sample(s) beyond ±{GLITCH_G} g (removed in Stage 1b)") if glitches else ("PASS", "")
    flat = [c for c, s in zip(CHANNELS, std) if s < FLAT_STD]
    clipped = {c: round(float(f) * 100, 2) for c, f in zip(CHANNELS, clip) if f > CLIP_WARN_FRACTION}
    if flat:
        chk["channels"] = ("FAIL", f"flat {flat}")
    elif clipped:
        chk["channels"] = ("WARN", f"clipping % {clipped}")
    else:
        chk["channels"] = ("PASS", "")
    out["clip_pct_max"] = round(float(clip.max()) * 100, 3)

    ph = phases(g)
    seconds = {p: int((ph == p).sum()) for p in PHASES}
    out["seconds"] = seconds
    short = {p: seconds[p] for p in ("fill", "spin") if seconds[p] < MIN_STATE_S}
    if all(seconds[p] == 0 for p in ("fill", "spin")):
        chk["states"] = ("FAIL", "neither fill nor spin")
    elif short:
        chk["states"] = ("WARN", f"under {MIN_STATE_S} s: {short}")
    else:
        chk["states"] = ("PASS", "")

    # vibration level per phase: per-second std of each channel (gravity removed), mean over channels
    sec = np.clip(np.searchsorted(tg, ta, side="right") - 1, 0, len(tg) - 1)
    n = np.bincount(sec, minlength=len(tg))
    ok = n >= 50
    var = np.zeros(len(tg))
    for j in range(x.shape[1]):
        m1 = np.bincount(sec, x[:, j], len(tg)) / np.maximum(n, 1)
        m2 = np.bincount(sec, x[:, j] ** 2, len(tg)) / np.maximum(n, 1)
        var += np.maximum(m2 - m1 ** 2, 0)
    level = np.sqrt(var / x.shape[1])

    cover = ok.mean()
    out["coverage"] = round(float(cover), 3)
    msg = f"{cover:.1%} of label seconds; offsets start {out['offset_s']['start']:+.1f} s, end {out['offset_s']['end']:+.1f} s"
    chk["coverage"] = ("FAIL" if cover < COVER_FAIL else "WARN" if cover < COVER_WARN else "PASS", msg)

    # does vibration line up with the labels in time? At each spin END the drum
    # stops, so the level drops sharply: find where, relative to the label edge.
    # (Correlating against power can't tell: tumbling repeats every ~26 s. Spin
    # STARTS can't either: a loud pre-spin phase precedes the label.)
    lv = np.log(level + 1e-6)
    s = g.centrifuge_label.to_numpy()
    offsets = []
    for i in np.flatnonzero(np.diff(s) == -1) + 1:
        lo, hi = i - 90, i + 90
        if lo < 0 or hi > len(lv) or not ok[lo:hi].all():
            continue
        before, after = np.median(lv[i - 90:i - 30]), np.median(lv[i + 30:i + 90])
        if before - after < 0.7:          # no clear drop: skip this edge
            continue
        w = np.convolve(lv[lo:hi], np.ones(5) / 5, "same")
        below = np.flatnonzero(w < (before + after) / 2)
        if len(below):
            offsets.append(int(lo + below[0] - i))
    out["alignment"] = {"spin_end_offsets_s": offsets}
    if not offsets:
        chk["alignment"] = ("PASS", "not measurable: no spin end with a clear drop")
    else:
        med = float(np.median(offsets))
        out["alignment"]["median_s"] = med
        chk["alignment"] = ("WARN" if abs(med) > ALIGN_WARN_S else "PASS",
                            f"vibration drops {med:+.0f} s from the label's spin end (median of {len(offsets)})")

    out["vib_level_g"] = {p: round(float(np.median(level[ok & (ph == p)])), 4) if (ok & (ph == p)).any() else None
                          for p in PHASES}
    return out


def main():
    split = json.load(open(os.path.join(DATA, "split.json")))
    jobs = [(role, split["library_unit"], f) for role in ("library", "validation", "test")
            for group in split[role].values() for f in group]
    jobs += [("delivery", split["delivery_unit"], f) for f in split["delivery"]]
    with ProcessPoolExecutor(min(8, os.cpu_count() or 1)) as pool:
        results = list(pool.map(check_cycle, *zip(*jobs)))

    manifest = {e["path"]: e["size"] for e in json.load(open(os.path.join(DATA, "manifest.json")))}
    for r in results:
        for rel in (f"raw/labels/{r['file']}", f"raw/vibration/{r['file'][:-4]}_acc.parquet"):
            path = os.path.join(DATA, rel)
            if os.path.exists(path) and manifest.get(rel) != os.path.getsize(path):
                r["checks"]["files"] = ("FAIL", f"{rel}: size differs from the manifest")

    title = (f"Stage 1a preflight: {len(results)} cycles "
             f"({sum(r['unit'] == split['library_unit'] for r in results)} {split['library_unit']}, "
             f"{sum(r['unit'] == split['delivery_unit'] for r in results)} {split['delivery_unit']})")
    lines = ["Median vibration level (g) per state, by unit:"]
    for unit in (split["library_unit"], split["delivery_unit"]):
        rows = [r for r in results if r["unit"] == unit and "vib_level_g" in r]
        med = {p: np.nanmedian([r["vib_level_g"][p] if r["vib_level_g"][p] is not None else np.nan for r in rows])
               for p in PHASES}
        secs = {p: int(np.sum([r["seconds"][p] for r in rows])) for p in PHASES}
        total = sum(secs.values())
        lines.append(f"  {unit:<22} " + "  ".join(f"{p} {med[p]:.4f} ({secs[p] / total:.0%})" for p in PHASES))
    for unit in (split["library_unit"], split["delivery_unit"]):
        d = sorted(r["date"] for r in results if r["unit"] == unit and "date" in r)
        lines.append(f"  {unit:<22} recorded {d[0]} .. {d[-1]} on {len(set(d))} days")

    n_fail = report(results, title, "Stage 1b", os.path.join(DATA, "preflight_raw.json"), notes="\n".join(lines))
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
