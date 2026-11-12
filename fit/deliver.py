#!/usr/bin/env python3
"""Stage 6: run the tested model over becken-flt, as a customer would.

The delivery files (data/roles/delivery/, the 5 becken-flt cycles, whole) carry no
labels. This uploads them (ids cached), packages the Stage 5 blueprint
(fit/out/test_state.json, unchanged: a package carries no values), deploys the package as
`batch`, runs it over the files in sessions (one by default; --files-per-run N makes
batches), downloads every output and writes one predictions CSV per delivery file:

    fit/out/delivery/<file>.csv    the platform's output rows for that file
                                   (finish_timestamp, predicted_state, invalid, ...),
                                   time-sorted
    fit/out/delivery/runs.json     package, deployment, sessions and each file's time range, so a rerun
                                   (or --resume) collects without starting anything again

A rerun also replaces what can't finish: a failed package is rebuilt once, and a session
that failed, or ended `completed` with an output short of its file's end, is ended and
dropped, and its files get a new session.

A session's outputs don't name their inputs, so rows are matched to files by timestamp:
becken-flt's cycles never overlap in time. Stage 7 (fit/score_delivery.py) scores the
files against the labels held back in data/roles/delivery_labels/.

    python fit/deliver.py --background                   # detached, log fit/out/deliver.log
    python fit/deliver.py --resume                       # collect the sessions in runs.json
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
from atai import (TERMINAL, api_base, deploy, deployment_ready, end, lifecycle_url, load_dotenv,  # noqa: E402
                  open_session, ready_package, request, result_url, same_deployment)
from optimize import OUT, ROLES, log, upload_all  # noqa: E402

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prep"))
from background import add_background_flag, maybe_detach  # noqa: E402

DELIVERY = os.path.join(OUT, "delivery")
STATE = os.path.join(DELIVERY, "runs.json")
# The platform reports a run `completed` while an artifact is still being written.
# Each artifact carries its own status at
# data.file_attributes.job_output.status (`partial` -> `complete`), which is what
# the console renders; that is the primary gate. The timestamp check below stays
# as a second opinion: it also catches an output that is complete but does not
# cover its input, which a status cannot.
END_SLACK_MS = 60_000          # a file's last prediction lands within a window of its end
OUTPUT_WAIT_S = 600            # how long to wait for outputs after the platform says completed


def to_ms(value):
    """A timestamp from the platform's output (epoch s, epoch ms or ISO 8601) as epoch ms."""
    try:
        v = float(value)
        return round(v if v > 1e11 else v * 1000)
    except ValueError:
        return round(datetime.datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)


def time_range(path):
    """First and last timestamp of a role file, in epoch ms, without reading it all.

    The tail window grows until it holds a complete final record, and is
    clamped to the file: a fixed seek from the end raises OSError on a file
    shorter than the window, and a window narrower than one row reads a
    fragment of a field — which still parses as a number and is silently
    wrong.
    """
    size = os.path.getsize(path)
    with open(path, "rb") as f:
        f.readline()
        first = f.readline().split(b",", 1)[0]
        window = 4096
        while True:
            f.seek(max(0, size - window))
            lines = f.read().strip().split(b"\n")
            # A leading partial line guarantees the last one is whole.
            if len(lines) > 1 or window >= size:
                last = lines[-1].split(b",", 1)[0]
                break
            window *= 4
    return to_ms(first.decode()), to_ms(last.decode())


def fetch(ref):
    """A result file, by its `ref`: a root path (`/api/files/<fil_…>/content`) or a URL."""
    req = urllib.request.Request(result_url(ref))
    req.add_header("Authorization", f"Bearer {os.environ['ATAI_API_KEY']}")
    with urllib.request.urlopen(req, timeout=600) as resp:
        return resp.read().decode("utf-8", errors="replace")


def stage5_blueprint():
    test_state = os.path.join(OUT, "test_state.json")
    if not os.path.exists(test_state):
        sys.exit("no Stage 5 blueprint yet: run fit/test.py first")
    ts = json.load(open(test_state))
    if not same_deployment(ts):
        sys.exit(f"{test_state} is from {ts.get('endpoint', 'an earlier run')}, a different deployment from {api_base()}: run fit/test.py here first")
    return ts["blueprint"]


def start(files, per_run, jobs, bp, state=None):
    """The package and batch deployment (once), then a session for every delivery file no run
    holds yet: all of them on a first run, those of dropped sessions on a rerun."""
    paths = [os.path.join(ROLES, f["file"]) for f in files]
    if state is None:
        state = {"endpoint": api_base(), "blueprint": bp["key"], "runs": [],
                 "files": {f["file"]: dict(zip(("first_ms", "last_ms"), time_range(p))) for f, p in zip(files, paths)}}
    os.makedirs(DELIVERY, exist_ok=True)

    def save():
        json.dump(state, open(STATE, "w"), indent=1)

    save()
    if "deployment" not in state:
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        ready_package(state, bp["id"], f"LARCO quickstart delivery to becken-flt {stamp}", save, log)
        state["deployment"] = deploy(state["package"], "batch")["id"]
        save()
        log(f"package {state['package']} of {bp['key']}, batch deployment {state['deployment']}")
        deployment_ready(state["deployment"])
    held = {n for r in state["runs"] for n in r["files"]}
    todo = [f["file"] for f in files if f["file"] not in held]
    if not todo:
        return state
    ids = upload_all([os.path.join(ROLES, n) for n in todo], jobs)
    per = per_run or len(todo)
    for i in range(0, len(todo), per):
        batch = todo[i:i + per]
        ses = open_session(state["deployment"], [ids[os.path.join(ROLES, n)] for n in batch])
        state["runs"].append({"session": ses["id"], "files": batch})
        save()                                            # saved per session, so --resume sees every one
        log(f"  session {ses['id']} over {len(batch)} file(s) ({i // per + 1} of {-(-len(todo) // per)})")
    return state


def drop_unfinished(state):
    """End and forget every session that failed or ended `completed` with an output short of
    a file's end, so the next run opens new sessions for its files."""
    keep = []
    for r in state["runs"]:
        failed = r.get("status") in TERMINAL and r.get("status") != "completed"
        short = (r.get("error") or "").startswith("outputs incomplete")
        if not (failed or short):
            keep.append(r)
            continue
        try:
            end("sessions", r["session"])
        except (RuntimeError, OSError) as e:
            log(f"  could not end {r['session']}: {e}; fit/cleanup.py retries it")
        log(f"  dropped {r['session']} ({r.get('status')}{', ' + r['error'] if r.get('error') else ''}): "
            f"a rerun opens a new session for its {len(r['files'])} file(s)")
    if len(keep) != len(state["runs"]):
        state["runs"] = keep
        json.dump(state, open(STATE, "w"), indent=1)


def artifact_status(item):
    """An output's own completion status, or None if the platform does not report one.

    A run reports `completed` while an artifact is still being written -- the
    console shows the run COMPLETED beside an artifact PARTIAL, and measured:
    an artifact read 16,018 of an eventual 50,547 bytes at the moment its run
    reported completed. Reading a partial artifact yields a well-formed CSV
    that is simply short, so this is the one signal that catches it without
    needing the input to compare against.
    """
    return (item.get("data", {}).get("file_attributes", {})
                .get("job_output", {}).get("status"))


def download(runs, ranges):
    """The runs' output rows, matched to delivery files by timestamp: ({file: [rows]}, header, unmatched).

    Artifacts the platform has not finished writing are skipped, not parsed:
    their rows would be a prefix, and a prefix is indistinguishable from a
    complete file once parsed.
    """
    per_file, header, unmatched = {}, None, 0
    for r in runs:
        for item in request("GET", lifecycle_url("sessions", r["session"]) + "/results").get("data") or []:
            status = artifact_status(item)
            if status is not None and status != "complete":
                continue
            body = list(csv.reader(io.StringIO(fetch(item["data"]["ref"]))))
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
    return per_file, header, unmatched


def incomplete(names, state, per_file, header):
    """The files among `names` whose predictions stop more than END_SLACK_MS before the file's end."""
    if not header:
        return list(names)
    fin = header.index("finish_timestamp")
    return [n for n in names if not per_file.get(n)
            or state["files"][n]["last_ms"] - max(to_ms(row[fin]) for row in per_file[n]) > END_SLACK_MS]


def collect(state):
    ranges = sorted((v["first_ms"], v["last_ms"], n) for n, v in state["files"].items())
    pending = {r["session"] for r in state["runs"] if r.get("status") not in TERMINAL}
    finishing = {}                 # run -> deadline, once the platform says completed
    while pending:
        for aid in sorted(pending):
            r = next(r for r in state["runs"] if r["session"] == aid)
            if aid not in finishing:
                a = request("GET", lifecycle_url("sessions", aid))
                if a["status"] not in TERMINAL:
                    continue
                if a["status"] != "completed":
                    pending.discard(aid)
                    r["status"], r["error"] = a["status"], a.get("error")
                    log(f"  {aid} {a['status']}" + (f" ({a.get('error')})" if a.get("error") else ""))
                    continue
                finishing[aid] = time.time() + OUTPUT_WAIT_S
            per_file, header, _ = download([r], ranges)
            short = incomplete(r["files"], state, per_file, header)
            n = len(r["files"])
            if not short:
                pending.discard(aid)
                r["status"], r["error"] = "completed", None
                log(f"  {aid} completed: all {n} outputs complete")
            elif time.time() > finishing[aid]:
                pending.discard(aid)
                r["status"], r["error"] = "completed", f"outputs incomplete: {', '.join(short)}"
                log(f"  {aid} WARNING platform completed {OUTPUT_WAIT_S // 60} min ago but {len(short)} of {n} "
                    f"outputs still end early ({', '.join(os.path.basename(x) for x in short)}); "
                    f"a rerun downloads them again, then replaces the session")
            else:
                log(f"  {aid} run reports completed; outputs {n - len(short)} of {n} ready "
                    f"({', '.join(os.path.basename(x) for x in short)} still being written)")
        json.dump(state, open(STATE, "w"), indent=1)
        if pending:
            if pending - set(finishing):
                log(f"  {len(pending - set(finishing))} of {len(state['runs'])} sessions still going")
            time.sleep(15 if pending <= set(finishing) else 60)

    done = [r for r in state["runs"] if r.get("status") == "completed"]
    per_file, header, unmatched = download(done, ranges)
    short = incomplete([n for r in done for n in r["files"]], state, per_file, header)
    for r in done:
        if not set(r["files"]) & set(short) and (r.get("error") or "").startswith("outputs incomplete"):
            r["error"] = None      # a --resume found them complete
    json.dump(state, open(STATE, "w"), indent=1)
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
    failed = [r["session"] for r in state["runs"] if r.get("status") != "completed"]
    log(f"{len(per_file)} of {len(state['files'])} files have predictions: {n:,} windows ({bad:,} invalid); "
        f"{unmatched:,} rows matched no file; failed sessions: {failed or 'none'}; "
        f"incomplete outputs: {[os.path.basename(x) for x in short] or 'none'} -> {DELIVERY}/")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", nargs="+", metavar="CYCLE", help="deliver just these becken-flt cycles (wm_... .csv)")
    ap.add_argument("--files-per-run", type=int, default=0, help="files per session (default 0: all in one session)")
    ap.add_argument("--upload-jobs", type=int, default=3)
    ap.add_argument("--resume", action="store_true", help="collect the sessions recorded in runs.json")
    add_background_flag(ap, default_log="fit/out/deliver.log")
    args = ap.parse_args()
    maybe_detach(args)
    load_dotenv()
    global DELIVERY, STATE
    if args.only:                  # a check goes to its own folder, never mixed with the delivery
        DELIVERY = os.path.join(OUT, "delivery_check")
        STATE = os.path.join(DELIVERY, "runs.json")
    bp = stage5_blueprint()
    files = json.load(open(os.path.join(ROLES, "manifest.json")))["delivery"]["files"]
    if args.only:
        files = [f for f in files if f["cycle"] in set(args.only)]
    state = None
    if args.resume or os.path.exists(STATE):
        if not args.resume:
            log(f"{STATE} exists: collecting those sessions (delete it to deliver again)")
        state = json.load(open(STATE))
        if state.get("bundle"):
            sys.exit(f"{STATE} is from a bundle delivery: move fit/out/delivery/ aside to deliver through a package")
        if not same_deployment(state):
            sys.exit(f"{STATE} is from {state.get('endpoint', 'an earlier run')}, a different deployment from {api_base()}: "
                     f"move fit/out/delivery/ aside to deliver on this deployment")
        if state.get("blueprint") != bp["key"]:
            sys.exit(f"{STATE} is for {state.get('blueprint')}: move fit/out/delivery/ aside to deliver {bp['key']}")
        collect(state)                           # download what the saved sessions have, first
        drop_unfinished(state)
    before = [r["session"] for r in (state or {}).get("runs", [])]
    state = start(files, args.files_per_run, args.upload_jobs, bp, state)
    if [r["session"] for r in state["runs"]] != before:
        collect(state)


if __name__ == "__main__":
    main()
