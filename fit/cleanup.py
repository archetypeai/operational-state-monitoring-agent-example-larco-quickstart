#!/usr/bin/env python3
"""Clean up: delete the agents and bundles Stages 6, 8 and 9 left on the platform.

    python fit/cleanup.py            # list what would be deleted, and what stays
    python fit/cleanup.py --yes      # delete it

From the ids in fit/out/ (--out for another folder):

  Stage 9   app_serving_state.json: app-serving/'s serving agents and bundle, if its
            bridge was killed before it could delete them; library_state.json: the
            4 batch runs of fit/library_embeddings.py and their bundle.
  Stage 8   serve_state.json: a serving agent and its bundle, if fit/serve.py was
            killed before it could delete them. The bundle's own listing also finds
            an agent a rejected run created but never recorded. A running serving
            agent holds one of Dev's 3 serving workers until it is cancelled, or
            idle 20 minutes.
  Stage 6   delivery/runs.json (and delivery_check/runs.json): the delivery agents
            and their bundle. The bundle's own listing also finds runs a rerun
            replaced. The outputs are already in fit/out/delivery/.

Agents go first (a bundle that still has an agent answers 409), then bundles, then
the served copy of the Stage 5 blueprint (fit/serve.py and the bridge remake it). A
running agent is cancelled before it is deleted, an id already gone counts as done,
and one object that won't delete doesn't stop the rest: they are listed at the end.
Progress goes to fit/out/cleanup_state.json, so a rerun picks up where it stopped.

What stays: the promoted blueprint, and the optimizations and eval the README cites.
After a cleanup, fit/deliver.py --resume can't collect again: its bundle is gone.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atai import TERMINAL, agents, api_base, load_dotenv, request, same_deployment  # noqa: E402
from optimize import OUT, log  # noqa: E402


def call(method, path, body=None):
    """The response, or None when the object is already gone."""
    try:
        return request(method, f"{agents()}{path}", body=body)
    except RuntimeError as e:
        if "(404)" in str(e):
            return None
        raise


def load(path):
    return json.load(open(path)) if os.path.exists(path) else {}


def bundle_runs(bundle):
    """Every agent the bundle has run."""
    ids, cursor = set(), ""
    while True:
        page = call("GET", f"/instances?bundle_id={bundle}&limit=1000{cursor}") or {}
        # a deployment that ignores the filter returns other bundles' runs too
        ids |= {r["id"] for r in page.get("data", []) if r.get("bundle_id", bundle) == bundle}
        if not page.get("has_more"):
            return ids
        cursor = f"&after={page['next_cursor']}"


def targets(serve, deliveries, runs_of=bundle_runs, app=None, library=None, served=None):
    """(stage, kind, id) to delete, each agent before its bundle, and the served blueprint last."""
    out = []
    if (app or {}).get("bundle"):
        out += [("Stage 9", "instances", a) for a in sorted(set(app.get("agents") or []) | runs_of(app["bundle"]))]
        out += [("Stage 9", "bundles", app["bundle"])]
    if (library or {}).get("bundle"):
        out += [("Stage 9", "instances", a) for a in sorted(set((library.get("runs") or {}).values()) | runs_of(library["bundle"]))]
        out += [("Stage 9", "bundles", library["bundle"])]
    if serve.get("bundle"):
        ids = ({serve["agent"]} if serve.get("agent") else set()) | runs_of(serve["bundle"])
        out += [("Stage 8", "instances", a) for a in sorted(ids)]
        out += [("Stage 8", "bundles", serve["bundle"])]
    for d in deliveries:
        if d.get("bundle"):
            ids = {r["agent"] for r in d.get("runs", [])} | runs_of(d["bundle"])
            out += [("Stage 6", "instances", a) for a in sorted(ids)] + [("Stage 6", "bundles", d["bundle"])]
    if served:
        out += [("served", "blueprints", served)]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=OUT, help="the stage outputs whose ids to delete (default fit/out)")
    ap.add_argument("--yes", action="store_true", help="delete; without it, only list")
    args = ap.parse_args()
    load_dotenv()
    serve_path = os.path.join(args.out, "serve_state.json")
    serve = load(serve_path)
    deliveries = [load(os.path.join(args.out, d, "runs.json")) for d in ("delivery", "delivery_check")]
    app_path, library_path = (os.path.join(args.out, f) for f in ("app_serving_state.json", "library_state.json"))
    app, library = load(app_path), load(library_path)
    for st in [serve, app, library] + deliveries:
        if st.get("endpoint") and not same_deployment(st):
            sys.exit(f"ids in {args.out} are from {st['endpoint']}, a different deployment from {api_base()}: "
                     f"set .env to that deployment")
    test = load(os.path.join(args.out, "test_state.json"))
    key = (test.get("blueprint") or {}).get("key")
    todo_all = targets(serve, deliveries, app=app, library=library, served=key and f"{key}-served-records")
    done_path = os.path.join(args.out, "cleanup_state.json")
    gone = set(load(done_path).get("deleted", []))
    todo = [t for t in todo_all if t[2] not in gone]
    for stage in ("Stage 9", "Stage 8", "Stage 6"):
        mine = [t for t in todo if t[0] == stage]
        print(f"{stage}: {sum(t[1] == 'instances' for t in mine)} agent(s), "
              f"{sum(t[1] == 'bundles' for t in mine)} bundle(s) to delete")
    print(f"already deleted: {len(gone & {t[2] for t in todo_all})}")
    if key:
        if f"{key}-served-records" not in gone:
            print(f"also deletes: blueprint {key}-served-records (fit/serve.py remakes it)")
        print(f"stays: blueprint {key}, and the optimizations and eval")
    if not args.yes:
        print("\nnothing deleted: rerun with --yes")
        return 0

    failed = []
    for k, (_, kind, obj) in enumerate(todo, 1):
        try:
            if kind == "instances":
                info = call("GET", f"/instances/{obj}")
                if info and info.get("status") not in TERMINAL:
                    call("POST", f"/instances/{obj}/cancel", {})
            call("DELETE", f"/{kind}/{obj}")
            gone.add(obj)
        except RuntimeError as e:
            failed.append(f"{kind}/{obj}: {e}")
        if k % 25 == 0 or k == len(todo):
            json.dump({"deleted": sorted(gone)}, open(done_path, "w"), indent=1)
            log(f"deleted {k - len(failed)}/{len(todo)}")
    if failed:
        print(f"\n{len(failed)} object(s) would not delete:")
        for f in failed:
            print(f"  {f}")
        print("an agent holds its bundle, and a bundle holds its blueprint: delete what holds them, then rerun")
        return 1
    for path, st in ((serve_path, serve), (app_path, app), (library_path, library)):
        if st.get("bundle"):
            os.remove(path)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
