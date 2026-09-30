#!/usr/bin/env python3
"""Stage 7: score the delivered predictions against the labels held back.

The delivery files went out without labels (Stage 6); their labels were kept in
data/roles/delivery_labels/, row for row. Each prediction is paired with the label of its
window's last row (its finish_timestamp: the `last_record` rule used throughout). Windows
the platform marked invalid, or whose finish time matches no labelled row, are left out
and counted. Scored as the platform scores: macro-F1 over all four states, a state with
no windows and no predictions counting 0.

Reported pooled over the delivered becken-flt cycles and per cycle, next to Stage 5's
test number. Local only. Reads fit/out/delivery/<file>.csv; writes
fit/out/delivery/scores.json.

    python fit/score_delivery.py
"""
import glob
import json
import os
import sys
from collections import Counter

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from deliver import DELIVERY, to_ms  # noqa: E402
from optimize import OUT, ROLES  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prep"))
from states import STATES  # noqa: E402


def scores(y, p):
    """Platform-style: F1 per state over all four (0 when a state has no windows and no predictions)."""
    f1 = {}
    for s in STATES:
        tp = int(((y == s) & (p == s)).sum()); fp = int(((y != s) & (p == s)).sum()); fn = int(((y == s) & (p != s)).sum())
        f1[s] = 2 * tp / (2 * tp + fp + fn) if tp + fp + fn else 0.0
    cm = [[int(((y == a) & (p == b)).sum()) for b in STATES] for a in STATES]
    return {"macro_f1": round(float(np.mean(list(f1.values()))), 4), "f1": {s: round(v, 4) for s, v in f1.items()},
            "confusion": cm, "windows": int(len(y))}


def pairs_of(pred_path):
    """(true, predicted) per scored window of one delivery file, and what was left out."""
    name = os.path.basename(pred_path)
    pred = pd.read_csv(pred_path, dtype=str)
    side = pd.read_csv(os.path.join(ROLES, "delivery_labels", name), dtype=str, engine="pyarrow")
    label = dict(zip((side.timestamp.astype(float) * 1000).round().astype(np.int64), side.label))
    skipped = Counter()
    if "invalid" in pred:
        bad = pred.invalid.str.lower() == "true"
        skipped["invalid"] = int(bad.sum())
        pred = pred[~bad]
    t = [label.get(to_ms(v)) for v in pred.finish_timestamp]
    keep = np.array([x is not None for x in t])
    skipped["no label at finish time"] = int((~keep).sum())
    return np.array(t, dtype=object)[keep], pred.predicted_state.to_numpy(dtype=object)[keep], skipped


def cycle_of(name):
    return "wm_" + name.split("__seg")[0] + ".csv"


def main():
    paths = sorted(glob.glob(os.path.join(DELIVERY, "*.csv")))
    if not paths:
        sys.exit(f"no predictions in {DELIVERY}/: run fit/deliver.py first")
    manifest = json.load(open(os.path.join(ROLES, "manifest.json")))
    expected = {os.path.basename(f["file"]) for f in manifest["delivery"]["files"]}
    missing = sorted(expected - {os.path.basename(p) for p in paths})

    ys, ps, cyc, skipped = [], [], [], Counter()
    for p in paths:
        y, pr, sk = pairs_of(p)
        ys.append(y)
        ps.append(pr)
        cyc.append(np.array([cycle_of(os.path.basename(p))] * len(y)))
        skipped.update(sk)
    y, p, cyc = np.concatenate(ys), np.concatenate(ps), np.concatenate(cyc)
    unknown = sorted(set(p) - set(STATES))
    out = {"files": len(paths), "files_missing": missing, "cycles": len(set(cyc)), "skipped": dict(skipped),
           "unknown_predictions": unknown, "all": scores(y, p),
           "per_cycle": {c: scores(y[cyc == c], p[cyc == c]) for c in sorted(set(cyc))}}

    print(f"Stage 7: {out['cycles']} becken-flt cycles, {len(paths)} files, {len(y):,} windows scored; "
          f"left out {dict(skipped)}")
    if missing:
        print(f"  WARNING: {len(missing)} delivery file(s) have no predictions: {missing}")
    if unknown:
        print(f"  WARNING: predictions outside the four states: {unknown}")
    rows = [(f"all {out['cycles']} cycles", out["all"])] + [(c[3:-4], r) for c, r in out["per_cycle"].items()]
    for name, r in rows:
        print(f"  {name:<40} macro-F1 {r['macro_f1']:.4f}  " + "  ".join(f"{s} {r['f1'][s]:.2f}" for s in STATES)
              + f"  windows {r['windows']:,}")
    test_path = os.path.join(OUT, "test.json")
    if os.path.exists(test_path):
        t = json.load(open(test_path))["all"]
        print(f"\nnext to Stage 5's test (becken, new settings): macro-F1 {t['macro_f1']:.4f}  "
              + "  ".join(f"{s} {t['f1'][s]:.2f}" for s in STATES))
        out["test"] = t["macro_f1"]
    path = os.path.join(DELIVERY, "scores.json")
    with open(path, "w") as f:
        json.dump(out, f, indent=1)
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
