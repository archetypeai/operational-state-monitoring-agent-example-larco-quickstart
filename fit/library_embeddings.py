#!/usr/bin/env python3
"""Stage 9 prep, once: the library's embeddings, for app-serving/'s embedding space.

    python fit/library_embeddings.py          # 4 batch runs, one per library file, then the view

The Stage 5 model is a kNN over the library's windows (window 512, step 512), but it
is a registry artifact with no download route, so its reference windows can't be
read back. They can be recomputed: a bundle on the promoted blueprint with
`output_embeddings: true` runs each library file (one per state) through the same
encoder, on the same windows, and returns each window's 9 x 768 Omega embedding
(800 windows in all, 200 a state).

The view is PCA 1-2 of those embeddings, whitened, in the layout app-serving/ reads:
app-serving/static/library-view.{json,bin} holds the 800 points with their state,
the mean and the two projection rows, so a served window's embedding lands in the
same coordinates. It is a drawing, not the model: the kNN decides in all 6,912
dimensions (cosine, k 5), and two of them are drawn.

Outputs: fit/out/library_embeddings/ (the 4 batch outputs, ~50 MB, not committed),
fit/out/library_embeddings.json (summary), the view files. Ids go to
fit/out/library_state.json, so a rerun resumes and fit/cleanup.py deletes them.
"""
import csv
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atai import TERMINAL, agents, api_base, load_dotenv, request  # noqa: E402
from deliver import END_SLACK_MS, OUTPUT_WAIT_S, fetch, time_range, to_ms  # noqa: E402
from optimize import OUT, ROLES, log, upload_all  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prep"))
from states import STATES  # noqa: E402

DIR = os.path.join(OUT, "library_embeddings")
STATE = os.path.join(OUT, "library_state.json")
VIEW = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app-serving", "static")


def outputs(agent):
    """The run's output rows, header first, from every result file it wrote."""
    rows, header = [], None
    for item in request("GET", f"{agents()}/instances/{agent}/results").get("data") or []:
        body = list(csv.reader(fetch(item["data"]["filename"]).splitlines()))
        if body:
            header = header or body[0]
            rows += body[1:]
    return header, rows


def run_all(files, state, save, ensure_bundle):
    """One batch run per library file; each output saved once it reaches its file's end."""
    paths = [os.path.join(ROLES, f["file"]) for f in files]
    done = lambda f: os.path.exists(os.path.join(DIR, f"{f['state']}.csv"))  # noqa: E731
    if all(done(f) for f in files):
        return
    ensure_bundle()
    ids = upload_all(paths, 3)
    for f, p in zip(files, paths):
        if not done(f) and f["state"] not in state["runs"]:
            run = request("POST", f"{agents()}/bundles/{state['bundle']}/run",
                          body={"connectors": {"source": [{"type": "file", "id": ids[p]}]}})
            state["runs"][f["state"]] = run["id"]
            save()
            log(f"  {f['state']}: run {run['id']}")
    os.makedirs(DIR, exist_ok=True)
    pending = {f["state"]: p for f, p in zip(files, paths) if not os.path.exists(os.path.join(DIR, f"{f['state']}.csv"))}
    deadline = {}
    while pending:
        for s, p in list(pending.items()):
            aid = state["runs"][s]
            if s not in deadline:
                status = request("GET", f"{agents()}/instances/{aid}")["status"]
                if status not in TERMINAL:
                    continue
                if status != "completed":
                    state["runs"].pop(s)
                    save()
                    sys.exit(f"{s}: run {aid} {status}; rerun to start it again")
                deadline[s] = time.time() + OUTPUT_WAIT_S
            header, rows = outputs(aid)
            last = max((to_ms(r[header.index("finish_timestamp")]) for r in rows), default=0) if header else 0
            # A run can say completed before its output is fully written (README, Platform behaviour)
            if time_range(p)[1] - last <= END_SLACK_MS:
                with open(os.path.join(DIR, f"{s}.csv"), "w", newline="") as fh:
                    csv.writer(fh).writerows([header] + rows)
                log(f"  {s}: {len(rows):,} windows")
                pending.pop(s)
            elif time.time() > deadline[s]:
                state["runs"].pop(s)
                save()
                sys.exit(f"{s}: run {aid} completed {OUTPUT_WAIT_S // 60} min ago but its output stops early; "
                         f"rerun to start it again")
        if pending:
            time.sleep(15 if set(pending) <= set(deadline) else 30)


def read_embeddings(path, channels):
    """[windows, 9 x 768] in the channels' order, invalid windows left out."""
    csv.field_size_limit(1 << 30)
    vecs = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            if str(r.get("invalid")).lower() == "true":
                continue
            missing = [c for c in channels if f"embedding_{c}" not in r]
            if missing:
                sys.exit(f"{path}: no embedding_{missing[0]} column (columns: {', '.join(r)[:300]})")
            vecs.append(np.concatenate([np.asarray(json.loads(r[f"embedding_{c}"]), dtype=np.float32) for c in channels]))
    return np.stack(vecs)


def main():
    load_dotenv()
    test = json.load(open(os.path.join(OUT, "test_state.json"))) if os.path.exists(os.path.join(OUT, "test_state.json")) else {}
    if not test.get("blueprint"):
        sys.exit("no fit/out/test_state.json: run fit/test.py (Stage 5) first")
    if test.get("endpoint", api_base()) != api_base():
        sys.exit(f"fit/out/test_state.json is from {test['endpoint']}, not {api_base()}")
    key = test["blueprint"]["key"]
    values = request("GET", f"{agents()}/blueprints/{key}")["document"]["values"]
    window, step = values["window_size"], values["step_size"]
    files = json.load(open(os.path.join(ROLES, "manifest.json")))["library"]["files"]
    channels = json.load(open(os.path.join(ROLES, "zscore_stats.json")))["channels"]

    state = json.load(open(STATE)) if os.path.exists(STATE) else {}
    if state and state.get("endpoint") != api_base():
        sys.exit(f"{STATE} is from {state.get('endpoint')}: move it aside, or set .env to that deployment")
    save = lambda: json.dump(state, open(STATE, "w"), indent=1)  # noqa: E731

    def ensure_bundle():
        if not state.get("bundle"):
            b = request("POST", f"{agents()}/bundles", body={
                "blueprint": key, "name": "LARCO library embeddings", "values": {"output_embeddings": True}})
            state.update({"endpoint": api_base(), "bundle": b["id"], "blueprint": key, "runs": {}})
            save()
            log(f"bundle {b['id']} on {key} (window {window}, step {step}, embeddings on)")
    t0 = time.time()
    run_all(files, state, save, ensure_bundle)

    vecs, codes = [], []
    for f in files:
        v = read_embeddings(os.path.join(DIR, f"{f['state']}.csv"), channels)
        vecs.append(v)
        codes += [STATES.index(f["state"])] * len(v)
    X = np.concatenate(vecs).astype(np.float64)
    n, dim = X.shape
    log(f"{n:,} windows x {dim:,} values")

    mean = X.mean(axis=0)
    Xc = X - mean
    # PCA through the n x n Gram matrix: n (800) is smaller than dim (6,912)
    eigval, eigvec = np.linalg.eigh(Xc @ Xc.T)
    top = np.argsort(eigval)[::-1][:2]
    var = eigval[top] / (n - 1)
    axes = (Xc.T @ eigvec[:, top]) / np.sqrt(eigval[top])       # unit principal axes, [dim, 2]
    rows = (axes / np.sqrt(var)).T.astype(np.float32)           # whitened projection rows
    ref = (Xc @ rows.T).astype(np.float32)
    explained = var / Xc.var(axis=0, ddof=1).sum()

    os.makedirs(VIEW, exist_ok=True)
    pieces = {"ref": ref, "state": np.array(codes, dtype=np.float32), "mean": mean.astype(np.float32),
              "pc0": rows[0], "pc1": rows[1]}
    layout, offset = {}, 0
    with open(os.path.join(VIEW, "library-view.bin"), "wb") as fh:
        for k, a in pieces.items():
            a = np.ascontiguousarray(a, dtype="<f4")
            fh.write(a.tobytes())
            layout[k] = {"offset": offset, "shape": list(a.shape)}
            offset += a.nbytes
    per_state = {s: codes.count(i) for i, s in enumerate(STATES)}
    with open(os.path.join(VIEW, "library-view.json"), "w") as fh:
        json.dump({
            "note": ("PCA 1-2 of the Stage 5 model's library, recomputed: the 4 library files through the promoted "
                     "blueprint in batch with output_embeddings on (fit/library_embeddings.py). For drawing only."),
            "blueprint": key, "channels": channels, "states": STATES, "embeddingDim": dim // len(channels),
            "totalDim": dim, "nReference": n, "perState": per_state, "windowSize": window, "stepSize": step,
            "whiten": True, "explainedVariance": [round(float(e), 4) for e in explained],
            "layout": layout, "dtype": "float32-le"}, fh, indent=1)
    summary = {"blueprint": key, "window_size": window, "step_size": step, "windows": n, "per_state": per_state,
               "dim": dim, "explained_variance_pc1_pc2": [round(float(e), 4) for e in explained],
               "view_bytes": offset, "seconds": round(time.time() - t0)}
    json.dump(summary, open(os.path.join(OUT, "library_embeddings.json"), "w"), indent=1)
    print(f"\nlibrary: {n:,} windows ({', '.join(f'{s} {c}' for s, c in per_state.items())}), window {window}, step {step}")
    print(f"  PCA 1-2 explain {explained[0]:.1%} and {explained[1]:.1%} of the variance")
    print(f"  -> app-serving/static/library-view.bin ({offset / 1e3:.0f} KB), library-view.json")
    print("  python fit/cleanup.py --yes deletes the 4 runs and their bundle")
    return 0


if __name__ == "__main__":
    sys.exit(main())
