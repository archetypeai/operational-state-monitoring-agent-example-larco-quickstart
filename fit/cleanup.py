#!/usr/bin/env python3
"""Clean up: delete the sessions, deployments and packages Stages 6, 8 and 9 left on the platform.

    python fit/cleanup.py            # list what would be deleted, and what stays
    python fit/cleanup.py --yes      # delete it

From the packages recorded in fit/out/ (--out for another folder):

  Stage 9   app_serving_state.json: app-serving/'s, if its bridge was killed before it
            could delete them; library_state.json: fit/library_embeddings.py's.
  Stage 8   serve_state.json: fit/serve.py's, if it was killed before it could delete
            them. A serving deployment holds a serving worker until it is stopped.
  Stage 6   delivery/runs.json (and delivery_check/runs.json): the delivery's. The
            outputs are already in fit/out/delivery/.

For each package, the platform's own listings (GET /api/agents/deployments?package_id=,
then GET /api/agents/sessions?deployment_id=) give its deployments and their sessions, so
ones a script never recorded, or a rerun replaced, are found too.

Sessions go first, then deployments, then the package: each holds the next. A session
still going is cancelled, and a deployment still up stopped, before it is deleted; an id
already gone counts as done, and one object that won't delete doesn't stop the rest: they
are listed at the end. Progress goes to fit/out/cleanup_state.json, so a rerun picks up
where it stopped.

What stays: the project, its copy of `osm`, the promoted blueprints (Stage 5's and the
embeddings one), and the optimizations and eval the README cites. After a cleanup,
fit/deliver.py --resume can't collect again: its sessions are gone.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from atai import api_base, end, lifecycle_url, load_dotenv, request, same_deployment  # noqa: E402
from optimize import OUT, log  # noqa: E402

KINDS = ("sessions", "deployments", "packages")


def load(path):
    return json.load(open(path)) if os.path.exists(path) else {}


def listing(kind, **query):
    """Every id of a lifecycle list, paged."""
    out, cursor = [], ""
    q = "&".join(f"{k}={v}" for k, v in query.items())
    while True:
        page = request("GET", f"{lifecycle_url(kind)}?{q}&limit=1000{cursor}")
        out += [r["id"] for r in page["data"]]
        if not page.get("has_more"):
            return out
        cursor = f"&after={page['next_cursor']}"


def targets(packages, deployments_of, sessions_of):
    """(stage, kind, id) to delete, each session before its deployment and each deployment
    before its package. `packages` is [(stage, package id)]."""
    out = []
    for stage, pkg in packages:
        dpls = deployments_of(pkg)
        out += [(stage, "sessions", s) for d in dpls for s in sessions_of(d)]
        out += [(stage, "deployments", d) for d in dpls] + [(stage, "packages", pkg)]
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=OUT, help="the stage outputs whose ids to delete (default fit/out)")
    ap.add_argument("--yes", action="store_true", help="delete; without it, only list")
    args = ap.parse_args()
    load_dotenv()
    paths = {"app": os.path.join(args.out, "app_serving_state.json"),
             "library": os.path.join(args.out, "library_state.json"),
             "serve": os.path.join(args.out, "serve_state.json"),
             "delivery": os.path.join(args.out, "delivery", "runs.json"),
             "delivery_check": os.path.join(args.out, "delivery_check", "runs.json")}
    stage = {"app": "Stage 9", "library": "Stage 9", "serve": "Stage 8", "delivery": "Stage 6", "delivery_check": "Stage 6"}
    found = {k: load(p) for k, p in paths.items()}
    for k, st in found.items():
        if st.get("endpoint") and not same_deployment(st):
            sys.exit(f"ids in {args.out} are from {st['endpoint']}, a different deployment from {api_base()}: "
                     f"set .env to that deployment")
        if st.get("bundle"):
            sys.exit(f"{paths[k]} holds bundle ids from before packages: delete them with the bundle-era fit/cleanup.py")

    def exists(pkg):
        try:
            return bool(request("GET", lifecycle_url("packages", pkg)))
        except RuntimeError as e:
            if "(404)" in str(e):
                return False
            raise
    packages = [(stage[k], st["package"]) for k, st in found.items() if st.get("package") and exists(st["package"])]
    todo_all = targets(packages, lambda p: listing("deployments", package_id=p), lambda d: listing("sessions", deployment_id=d))
    done_path = os.path.join(args.out, "cleanup_state.json")
    gone = set(load(done_path).get("deleted", []))
    todo = [t for t in todo_all if t[2] not in gone]
    for s in ("Stage 9", "Stage 8", "Stage 6"):
        mine = [t for t in todo if t[0] == s]
        print(f"{s}: " + ", ".join(f"{sum(t[1] == k for t in mine)} {k}" for k in KINDS) + " to delete")
    print(f"already deleted: {len(gone & {t[2] for t in todo_all})}")
    key = (load(os.path.join(args.out, "test_state.json")).get("blueprint") or {}).get("key")
    if key:
        print(f"stays: blueprint {key}, the embeddings blueprint, the optimizations and eval, "
              f"and the project with its copy of osm")
    if not args.yes:
        print("\nnothing deleted: rerun with --yes")
        return 0

    failed = []
    for k, (_, kind, obj) in enumerate(todo, 1):
        try:
            end(kind, obj)
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
        print("a session holds its deployment, and a deployment its package: delete what holds them, then rerun")
        return 1
    for k in ("serve", "app"):
        if found[k].get("package"):
            os.remove(paths[k])
    lib = found["library"]
    if lib.get("package"):                # keep the embeddings blueprint for a rerun and the bridge
        json.dump({k: v for k, v in lib.items() if k not in ("package", "deployment", "runs")},
                  open(paths["library"], "w"), indent=1)
    log("done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
