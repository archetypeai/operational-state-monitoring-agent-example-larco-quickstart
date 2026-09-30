#!/usr/bin/env python3
"""Stage 6: run the tested model over becken-flt, as a customer would.

The delivery files (data/roles/delivery/, the 5 becken-flt cycles, whole) carry no
labels. This uploads them (ids cached), creates one bundle from the Stage 5 blueprint
(fit/out/test_state.json, unchanged), runs it over the files (one run by default;
--files-per-run N makes batches), downloads every output and writes one predictions
CSV per delivery file:

    fit/out/delivery/<file>.csv    the platform's output rows for that file
                                   (finish_timestamp, predicted_state, invalid, ...),
                                   time-sorted
    fit/out/delivery/runs.json     bundle, runs and each file's time range, so a rerun
                                   (or --resume) collects without starting anything again

A run's outputs don't name their inputs, so rows are matched to files by timestamp:
becken-flt's cycles never overlap in time. Stage 7 (fit/score_delivery.py) scores the
files against the labels held back in data/roles/delivery_labels/.

    python fit/deliver.py --background                   # detached, log fit/out/deliver.log
    python fit/deliver.py --resume                       # collect the runs in runs.json
"""
import argparse
import csv
import datetime
import io
import json
import os
import sys
import time
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atai import TERMINAL, agents, api_base, load_dotenv, request  # noqa: E402
from optimize import OUT, ROLES, log, upload_all  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prep"))
from background import add_background_flag, maybe_detach  # noqa: E402

DELIVERY = os.path.join(OUT, "delivery")
STATE = os.path.join(DELIVERY, "runs.json")


def to_ms(value):
    """A timestamp from the platform's output (epoch s, epoch ms or ISO 8601) as epoch ms."""
    try:
        v = float(value)
        return round(v if v > 1e11 else v * 1000)
    except ValueError:
        return round(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def time_range(path):
    """First and last timestamp of a role file, in epoch ms, without reading it all."""
    with open(path, "rb") as f:
        f.readline()
        first = f.readline().split(b",", 1)[0]
        f.seek(-4096, os.SEEK_END)
        last = f.read().strip().split(b"\n")[-1].split(b",", 1)[0]
    return to_ms(first.decode()), to_ms(last.decode())


def fetch(filename):
    req = urllib.request.Request(f"{api_base()}/v0.5/files/download/{filename}")
    req.add_header("Authorization", f"Bearer {os.environ['ATAI_API_KEY']}")
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.read().decode("utf-8", errors="replace")


def start(files, per_run, jobs):
    paths = [os.path.join(ROLES, f["file"]) for f in files]
    ids = upload_all(paths, jobs)
    test_state = os.path.join(OUT, "test_state.json")
    if not os.path.exists(test_state):
        sys.exit("no Stage 5 blueprint yet: run fit/test.py first")
    KEY = json.load(open(test_state))["blueprint"]["key"]
    bp = request("GET", f"{agents()}/blueprints/{KEY}")
    stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    bundle = request("POST", f"{agents()}/bundles", body={
        "blueprint": KEY, "name": f"LARCO quickstart delivery to becken-flt {stamp}",
        "description": "Stage 6: the Stage 5 model run over the unlabelled becken-flt cycles"})
    log(f"bundle {bundle['id']} from {KEY} ({bp['id']})")
    state = {"bundle": bundle["id"], "blueprint": KEY, "runs": [],
             "files": {f["file"]: dict(zip(("first_ms", "last_ms"), time_range(p))) for f, p in zip(files, paths)}}
    os.makedirs(DELIVERY, exist_ok=True)
    for i in range(0, len(paths), per_run):
        batch = [f["file"] for f in files[i:i + per_run]]
        run = request("POST", f"{agents()}/bundles/{bundle['id']}/run", body={
            "connectors": {"source": [{"type": "file", "id": ids[os.path.join(ROLES, n)]} for n in batch]}})
        state["runs"].append({"agent": run["id"], "files": batch})
        json.dump(state, open(STATE, "w"), indent=1)      # saved per run, so --resume sees every one
        log(f"  run {run['id']} over {len(batch)} file(s) ({len(state['runs'])} of {-(-len(paths) // per_run)})")
    return state


def collect(state):
    pending = {r["agent"] for r in state["runs"] if r.get("status") not in TERMINAL}
    while pending:
        for aid in sorted(pending):
            a = request("GET", f"{agents()}/instances/{aid}")
            if a["status"] in TERMINAL:
                pending.discard(aid)
                r = next(r for r in state["runs"] if r["agent"] == aid)
                r["status"], r["error"] = a["status"], a.get("error")
                log(f"  {aid} {a['status']}" + (f" ({a.get('error')})" if a.get("error") else ""))
        json.dump(state, open(STATE, "w"), indent=1)
        if pending:
            log(f"  {len(pending)} of {len(state['runs'])} runs still going")
            time.sleep(60)

    ranges = sorted((v["first_ms"], v["last_ms"], n) for n, v in state["files"].items())
    per_file, header, unmatched = {}, None, 0
    for r in state["runs"]:
        if r.get("status") != "completed":
            continue
        for item in request("GET", f"{agents()}/instances/{r['agent']}/results").get("data") or []:
            body = list(csv.reader(io.StringIO(fetch(item["data"]["filename"]))))
            if not body:
                continue
            if header is None:
                header = body[0]
            elif body[0] != header:
                sys.exit(f"outputs disagree on columns: {body[0]} vs {header}")
            fin = header.index("finish_timestamp")
            for row in body[1:]:
                t = to_ms(row[fin])
                name = next((n for a, b, n in ranges if a <= t <= b), None)
                if name is None:
                    unmatched += 1
                else:
                    per_file.setdefault(name, []).append(row)
    fin = header.index("finish_timestamp") if header else None
    for name, rows in sorted(per_file.items()):
        rows.sort(key=lambda row: to_ms(row[fin]))
        with open(os.path.join(DELIVERY, os.path.basename(name)), "w", newline="") as f:
            out = csv.writer(f)
            out.writerow(header)
            out.writerows(rows)
    inv = header.index("invalid") if header and "invalid" in header else None
    n = sum(len(v) for v in per_file.values())
    bad = sum(1 for v in per_file.values() for row in v if inv is not None and row[inv].lower() == "true")
    failed = [r["agent"] for r in state["runs"] if r.get("status") != "completed"]
    log(f"{len(per_file)} of {len(state['files'])} files have predictions: {n:,} windows ({bad:,} invalid); "
        f"{unmatched:,} rows matched no file; failed runs: {failed or 'none'} -> {DELIVERY}/")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="+", metavar="CYCLE", help="deliver just these becken-flt cycles (wm_... .csv)")
    ap.add_argument("--files-per-run", type=int, default=0, help="files per run (default 0: all in one run)")
    ap.add_argument("--upload-jobs", type=int, default=3)
    ap.add_argument("--resume", action="store_true", help="collect the runs recorded in runs.json")
    add_background_flag(ap, default_log="fit/out/deliver.log")
    args = ap.parse_args()
    maybe_detach(args)
    load_dotenv()
    global DELIVERY, STATE
    if args.only:                  # a check goes to its own folder, never mixed with the delivery
        DELIVERY = os.path.join(OUT, "delivery_check")
        STATE = os.path.join(DELIVERY, "runs.json")
    if args.resume or os.path.exists(STATE):
        if not args.resume:
            log(f"{STATE} exists: collecting those runs (delete it to deliver again)")
        state = json.load(open(STATE))
    else:
        files = json.load(open(os.path.join(ROLES, "manifest.json")))["delivery"]["files"]
        if args.only:
            files = [f for f in files if f["cycle"] in set(args.only)]
        state = start(files, args.files_per_run or len(files), args.upload_jobs)
    collect(state)


if __name__ == "__main__":
    main()
