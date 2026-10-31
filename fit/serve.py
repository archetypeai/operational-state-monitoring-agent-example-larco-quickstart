#!/usr/bin/env python3
"""Stage 8: serve the Stage 5 model over /query, checked against Stage 6's delivery.

    python fit/serve.py                          # the 5 becken-flt cycles, every window (25-35 min)
    python fit/serve.py --files warm_fast-15_0   # one cycle (406 windows, ~4 min)
    python fit/serve.py --max-windows 20         # the first 20 windows of each file
    python fit/serve.py --windows-per-query 8    # 8 consecutive windows a query

Stage 6 runs the model in batch: upload the files, run a session, download the outputs.
Agent serving keeps an agent loaded and answers a window in one round trip, as a
washing machine's controller would ask it every 2.56 s about the latest 512 readings.
This replays Stage 6's delivery files that way and checks every answer against the
batch output for the same window:

  1. a package of Stage 5's blueprint as it is (a package carries no values; the
     columns its serving source needs, since a served query has no CSV header, were
     pinned at Stage 4): the platform supplies the `atai-records` serving connectors
     for a serving deployment, so no served copy is made;
  2. that package deployed as `serving` (the agent placed on a worker and loaded), and a
     session opened on it, queried at POST /api/agents/sessions/{id}/query;
  3. each file replayed a window at a time: every query holds one window's 512 rows
     of the 9 channels (--windows-per-query N sends N consecutive windows);
  4. every answer against batch: `predicted_state`, the four probabilities to within
     1e-4, and the macro-F1 against the held-back labels, served and batch (exit 1 if more than
     0.1% of the states differ: a window near a tie can swap one neighbour);
  5. the session cancelled, the deployment stopped, and all three deleted, however it ends (Ctrl-C,
     SIGTERM and SIGHUP included). Their ids stay in fit/out/serve_state.json until
     then, so fit/cleanup.py finds them after a kill -9.

A served agent keeps
nothing between queries, so a query carries the whole window, and the timestamps in its
answer are row indices within the query. Writes fit/out/serving/.
"""
import argparse
import json
import os
import signal
import statistics
import sys
import time

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atai import (agents, api_base, create_package, deploy, end as end_one, lifecycle_url, load_dotenv,  # noqa: E402
                  open_session, request, same_deployment, wait_running)
from optimize import OUT, ROLES, log  # noqa: E402
from score_delivery import scores  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prep"))
from states import STATES  # noqa: E402

MODEL = "larco-osm"            # the name a query gives in `model` (a served agent does not check it)
MAX_WPQ = 16


def query_body(chunk, model=MODEL):
    """One query: a list per channel of the chunk's rows (windows x 512 of them)."""
    return {"model": model, "query": "", "events": [{"type": "data.numeric_array",
            "event_data": {"contents": [chunk[:, c].tolist() for c in range(chunk.shape[1])]}}]}


def windows_of(batch, ts_ms, window):
    """The first row of each batch window, from its finish_timestamp; exits if they don't tile."""
    row = {t: i for i, t in enumerate(ts_ms)}
    ends = [row.get(round(float(v) * 1000)) for v in batch.finish_timestamp]
    if None in ends:
        sys.exit("a batch window's finish_timestamp matches no row of its delivery file")
    firsts = np.array(ends) - window + 1
    starts = np.round(batch.start_timestamp.astype(float).to_numpy() * 1000).astype(np.int64)
    if (firsts < 0).any() or (ts_ms[firsts] != starts).any():
        sys.exit("batch windows don't span window_size rows")
    return firsts


def end(ex):
    """End the session, then the deployment, then delete the package; an id already gone
    counts as done. Each holds the next."""
    for kind in ("sessions", "deployments", "packages"):
        if ex.get(kind[:-1]):
            try:
                end_one(kind, ex[kind[:-1]])
            except RuntimeError as e:
                log(f"cleanup: {e}; fit/cleanup.py retries it")
                return False
    return True


def stop(signum, _frame):
    sys.exit(f"stopped by signal {signum}")       # so `finally` ends the session and deployment


def main():
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, stop)
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=OUT, help="the stage outputs to serve and compare against (default fit/out)")
    ap.add_argument("--files", nargs="+", metavar="NAME", help="delivery files whose name contains NAME (default: all)")
    ap.add_argument("--max-windows", type=int, help="replay only the first N windows of each file")
    ap.add_argument("--windows-per-query", type=int, default=1, help=f"1 = live, one window a query; up to {MAX_WPQ}")
    args = ap.parse_args()
    load_dotenv()
    ts_path = os.path.join(args.out, "test_state.json")
    if not os.path.exists(ts_path):
        sys.exit(f"no {ts_path}: run fit/test.py first")
    test = json.load(open(ts_path))
    if not same_deployment(test):
        sys.exit(f"{ts_path} is from {test.get('endpoint', 'an earlier run')}, a different deployment from {api_base()}")
    promoted = test["blueprint"]["key"]
    # by id: a key registered in a project resolves only with ?project_id=
    bp = request("GET", f"{agents()}/blueprints/{test['blueprint']['id']}")["document"]["values"]
    if "data_columns" not in bp:
        sys.exit(f"{promoted} doesn't pin data_columns, which serving reads from values: rerun Stages 4-5")
    window = bp["window_size"]
    names = [os.path.basename(f["file"]) for f in json.load(open(os.path.join(ROLES, "manifest.json")))["delivery"]["files"]]
    if args.files:
        names = [n for n in names if any(s in n for s in args.files)] or sys.exit(f"no delivery file matches {args.files}")

    plan = []                                    # (name, X, labels per row, batch, first rows)
    channels = None
    for name in names:
        out = os.path.join(args.out, "delivery", name)
        if not os.path.exists(out):
            sys.exit(f"no {out}: run fit/deliver.py first")
        data = pd.read_csv(os.path.join(ROLES, "delivery", name), engine="pyarrow")
        channels = channels or [c for c in data.columns if c != "timestamp"]
        ts_ms = np.round(data.timestamp.to_numpy() * 1000).astype(np.int64)
        labels = pd.read_csv(os.path.join(ROLES, "delivery_labels", name), engine="pyarrow").label.to_numpy(dtype=object)
        batch = pd.read_csv(out, dtype={"predicted_state": str, "invalid": str})
        batch = batch[batch.invalid.str.lower() != "true"].reset_index(drop=True)
        if args.max_windows:
            batch = batch.head(args.max_windows)
        plan.append((name, data[channels].to_numpy(dtype=np.float32), labels, batch, windows_of(batch, ts_ms, window)))
    wpq = max(1, min(args.windows_per_query, MAX_WPQ))
    log(f"{len(plan)} file(s), {sum(len(p[3]) for p in plan):,} windows of {window} rows, {wpq} per query; "
        f"model {promoted}")

    sf = os.path.join(args.out, "serve_state.json")
    if os.path.exists(sf):
        sys.exit(f"{sf} holds an earlier replay's session, deployment and package: run fit/cleanup.py --yes first")
    ex = {"endpoint": api_base(), "package": None, "deployment": None, "session": None}
    served, latency = {}, []
    t0 = time.time()
    try:
        for kind, make in (("package", lambda: create_package(test["blueprint"]["id"], "LARCO serve")),
                           ("deployment", lambda: deploy(ex["package"], "serving")),
                           ("session", lambda: open_session(ex["deployment"]))):
            ex[kind] = make()["id"]
            json.dump(ex, open(sf, "w"), indent=1)
        log(f"package {ex['package']} · serving deployment {ex['deployment']} · session {ex['session']}")
        wait_running(ex["session"])
        for name, X, _, batch, firsts in plan:
            got = served[name] = []
            k = 0
            while k < len(firsts):
                # consecutive windows only: a gap in the tiling (an invalid window left out) ends a query
                count = 1
                while count < wpq and k + count < len(firsts) and firsts[k + count] == firsts[k] + count * window:
                    count += 1
                t = time.time()
                answer = request("POST", lifecycle_url("sessions", ex["session"]) + "/query",
                                 body=query_body(X[firsts[k]:firsts[k] + count * window]))
                latency.append(time.time() - t)
                records = (answer.get("response") or {}).get("data")
                if not isinstance(records, list) or len(records) != count:
                    sys.exit(f"{name} window {k}: expected {count} records, got {str(answer)[:300]}")
                got.extend(records)
                k += count
            log(f"  {name}: {len(firsts)} windows, {time.time() - t0:.0f} s so far")
    finally:
        if end(ex):
            os.remove(sf)
            log(f"ended and deleted {', '.join(v for k, v in ex.items() if k != 'endpoint' and v)}")
    elapsed = time.time() - t0

    rows = []
    for name, _, labels, batch, firsts in plan:
        for k, (s, b) in enumerate(zip(served[name], batch.itertuples())):
            diff = max(abs(float(s[f"p_{c}"]) - float(getattr(b, f"p_{c}"))) for c in STATES)
            rows.append({"file": name, "window": k, "finish_timestamp": b.finish_timestamp,
                         "label": labels[firsts[k] + window - 1],
                         "batch_state": b.predicted_state, "served_state": s["predicted_state"],
                         **{f"served_p_{c}": float(s[f"p_{c}"]) for c in STATES},
                         "max_p_diff": diff, "served_invalid": str(s["invalid"]).lower() == "true",
                         "same_state": s["predicted_state"] == b.predicted_state, "same_p": diff < 1e-4})
    df = pd.DataFrame(rows)
    y = df.label.to_numpy(dtype=object)
    sdir = os.path.join(args.out, "serving")
    os.makedirs(sdir, exist_ok=True)
    stem = os.path.join(sdir, f"serve_{len(plan)}files{f'_first{args.max_windows}' if args.max_windows else ''}_wpq{wpq}")
    df.to_csv(stem + ".csv", index=False)
    ms = sorted(x * 1000 for x in latency)
    summary = {"blueprint": promoted, "files": names, "windows": len(df),
               "windows_per_query": wpq, "queries": len(latency),
               "same_state": int(df.same_state.sum()), "same_p": int(df.same_p.sum()),
               "max_p_diff": float(df.max_p_diff.max()), "invalid": int(df.served_invalid.sum()),
               "served": scores(y, df.served_state.to_numpy(dtype=object)),
               "batch": scores(y, df.batch_state.to_numpy(dtype=object)),
               "latency_ms": {"median": round(statistics.median(ms)), "p95": round(ms[int(0.95 * (len(ms) - 1))]),
                              "max": round(ms[-1])},
               "replay_s": round(elapsed)}
    json.dump(summary, open(stem + ".json", "w"), indent=1)

    print(f"\nserved {len(df):,} windows of {len(plan)} file(s) in {len(latency):,} queries, {elapsed:.0f} s")
    print(f"  against the delivery (batch): predicted_state identical in {summary['same_state']:,}/{len(df):,}, "
          f"probabilities within 1e-4 in {summary['same_p']:,}/{len(df):,} "
          f"(max difference {summary['max_p_diff']:.1e}); invalid {summary['invalid']}")
    print(f"  latency per query: median {summary['latency_ms']['median']} ms, "
          f"p95 {summary['latency_ms']['p95']} ms, max {summary['latency_ms']['max']} ms")
    for who in ("served", "batch"):
        r = summary[who]
        print(f"  {who:<7} macro-F1 {r['macro_f1']:.4f}  " + "  ".join(f"{s} {r['f1'][s]:.2f}" for s in STATES))
    print(f"  -> {os.path.relpath(stem)}.csv / .json")
    # a kNN near a tie can swap one neighbour between served and batch (see the README):
    # fail on more than that
    return 0 if summary["same_state"] >= 0.999 * len(df) else 1


if __name__ == "__main__":
    sys.exit(main())
