#!/usr/bin/env python3
"""Stage 5: test the chosen setting once, on the 3 test cycles, with the Evals API.

The model is the best Stage 4b trial by validation macro-F1 (fit/out/optimize_<id>.json),
unless --trial names one. Steps, each recorded in fit/out/test_state.json so a rerun
resumes where it stopped instead of promoting or testing again:

  1. promote the trial to a blueprint (its settings and fitted classifier attached),
     or reuse the blueprint if it was already promoted;
  2. upload the test files (cached like the other role files);
  3. one eval over all of them; it takes ~40 min on the platform even for small files.

This is the one-shot test: once its number is seen, the setting and setup are frozen.
Writes fit/out/test_<eval id>.json and fit/out/test.json (the summary).

    python fit/test.py --background              # detached, log fit/out/test.log
    python fit/test.py --trial otr_... --background
"""
import argparse
import datetime
import glob
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atai import TERMINAL, agents, load_dotenv, request, trial_setting  # noqa: E402
from optimize import OUT, ROLES, log, upload_all  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prep"))
from background import add_background_flag, maybe_detach  # noqa: E402
from states import STATES  # noqa: E402

STATE = os.path.join(OUT, "test_state.json")


def blueprint_key(trial):
    """Unique per trial: a rerun of Stage 4 with the same setting gets its own blueprint."""
    w, st, k, metric, weights = trial_setting(trial)
    return f"osm-larco-quickstart-w{w}-s{st}-{metric}-k{k}-{weights}-{trial['id'][-6:]}"


def chosen_trial(trial_id=None):
    """(optimization id, trial) of the best completed Stage 4b trial, or of --trial."""
    runs = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(OUT, "optimize_opt_*.json")))]
    trials = [(r["optimization"]["id"], t) for r in runs for t in r["trials"]
              if t["status"] == "completed" and t.get("objective_value") is not None]
    if trial_id:
        trials = [(o, t) for o, t in trials if t["id"] == trial_id]
    if not trials:
        sys.exit("no completed Stage 4b trial in fit/out/: run fit/optimize.py first"
                 + (f" (or check --trial {trial_id})" if trial_id else ""))
    return max(trials, key=lambda ot: ot[1]["objective_value"])


def blueprint(opt, trial):
    key = blueprint_key(trial)
    try:
        return request("GET", f"{agents()}/blueprints/{key}")
    except RuntimeError:
        pass  # not promoted yet
    w, st, k, metric, weights = trial_setting(trial)
    bp = request("POST", f"{agents()}/optimizations/{opt}/trials/{trial['id']}/promote", body={
        "blueprint_key": key, "name": f"OSM LARCO quickstart (w{w}, s{st}, {metric}, k{k}, {weights})",
        "description": f"Stage 5 model: window {w}, step {st}, {metric}, k {k}, {weights}; "
                       f"library = 4 files, 100 windows per state, 8 short becken cycles."})
    log(f"promoted {trial['id']} -> {bp['blueprint_key']} ({bp['id']})")
    return bp


def matrix(ev):
    """Confusion matrix in STATES order (rows = true), from an eval's report."""
    s = ev["metrics_report"]["targets"]["state"]
    idx = [s["class_names"].index(x) for x in STATES]
    cm = s["confusion_matrix"]
    return [[cm[i][j] for j in idx] for i in idx]


def scores(cm):
    f1 = {}
    for i, s in enumerate(STATES):
        tp, row, col = cm[i][i], sum(cm[i]), sum(r[i] for r in cm)
        f1[s] = round(2 * tp / (row + col), 4) if row + col else 0.0
    return {"macro_f1": round(sum(f1.values()) / len(f1), 4), "f1": f1, "windows": sum(map(sum, cm)), "confusion": cm}


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--trial", metavar="OTR_ID", help="test this Stage 4b trial instead of the best one")
    ap.add_argument("--upload-jobs", type=int, default=3)
    add_background_flag(ap, default_log="fit/out/test.log")
    args = ap.parse_args()
    maybe_detach(args)
    load_dotenv()
    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    save = lambda: json.dump(state, open(STATE, "w"), indent=1)  # noqa: E731

    if "model" in state:
        opt, tid = state["model"]["optimization"], state["model"]["trial"]
        trial = next(t for t in request("GET", f"{agents()}/optimizations/{opt}/trials")["data"] if t["id"] == tid)
    else:
        opt, trial = chosen_trial(args.trial)
        state["model"] = {"optimization": opt, "trial": trial["id"]}
        save()
    w, st, k, metric, weights = trial_setting(trial)
    log(f"model: {opt} trial {trial['id']} (w {w}, step {st}, k {k}, {metric}, {weights}; "
        f"validation macro-F1 {trial['objective_value']:.4f})")
    if "blueprint" not in state:
        bp = blueprint(opt, trial)
        state["blueprint"] = {"id": bp["id"], "key": bp["blueprint_key"]}
        save()

    files = json.load(open(os.path.join(ROLES, "manifest.json")))["test"]["files"]
    paths = [os.path.join(ROLES, f["file"]) for f in files]
    ids = upload_all(paths, args.upload_jobs)
    if "eval" not in state:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ev = request("POST", f"{agents()}/evals", body={
            "blueprint_id": state["blueprint"]["id"], "name": f"LARCO quickstart Stage 5 test {stamp}",
            "emit_predictions": False,
            "examples": [{"name": os.path.basename(p)[:-4], "inputs": [{"type": "file", "id": ids[p], "format": "csv"}],
                          "ground_truth": {"state": {"from": {"column": "label"}, "downsampling": "last_record"}}}
                         for p in paths]})
        state["eval"] = ev["id"]
        save()
        log(f"eval {ev['id']}: {len(paths)} test file(s)")

    while True:
        ev = request("GET", f"{agents()}/evals/{state['eval']}")
        if ev["status"] in TERMINAL:
            break
        log(f"  eval {ev['status']}")
        time.sleep(60)
    json.dump(ev, open(os.path.join(OUT, f"test_{state['eval']}.json"), "w"), indent=1)
    if ev["status"] != "completed" or not ev.get("metrics_report"):
        sys.exit(f"eval {state['eval']} ended {ev['status']}: {ev.get('error')}")

    cm = matrix(ev)
    res = {"model": {"optimization": opt, "trial": trial["id"], "blueprint": state["blueprint"],
                     "window": w, "step": st, "k": k, "metric": metric, "weights": weights},
           "eval": state["eval"], "all": scores(cm)}
    json.dump(res, open(os.path.join(OUT, "test.json"), "w"), indent=1)
    r = res["all"]
    print(f"\nStage 5 test, w {w} / step {st}, k {k}, {metric}, {weights}:")
    print(f"  all {len({f['cycle'] for f in files})} test cycles  macro-F1 {r['macro_f1']:.4f}  "
          + "  ".join(f"{s} {r['f1'][s]:.2f}" for s in STATES) + f"  windows {r['windows']:,}")
    print("confusion (rows = true, cols = predicted):")
    print("  " + "".join(f"{s:>9}" for s in STATES))
    for s, row in zip(STATES, cm):
        print(f"  {s:<7}" + "".join(f"{v:>9,}" for v in row))
    log(f"wrote {os.path.join(OUT, 'test.json')}")


if __name__ == "__main__":
    main()
