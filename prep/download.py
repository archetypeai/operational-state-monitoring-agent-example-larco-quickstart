#!/usr/bin/env python3
"""Stage 0b: download both units' cycles from Zenodo by HTTP range request.

Only the cycles in data/split.json (Stage 0a: the quickstart's 19). Labels first (the 1 Hz
general CSVs, ~1 MB a cycle); vibration with
--vibration (the 200 Hz parquets, ~2-9 MB for these short cycles, ~140 MB in all).
Also LICENCE.txt, aggregated_data.csv and metadata.xlsx. Files already present
at the right size are skipped, so an interrupted run resumes.

    python3 prep/download.py                 # labels only, both units
    python3 prep/download.py --vibration     # add the vibration
    python3 prep/download.py --vibration --background   # the same, detached (nohup + caffeinate), log data/download.log
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from larco import DATA, FILES, fetch_member, listing  # noqa: E402
from background import add_background_flag, maybe_detach  # noqa: E402

RAW = os.path.join(DATA, "raw")


def fetch(url, entry, path):
    if os.path.exists(path) and os.path.getsize(path) == entry[3]:
        return 0
    data = fetch_member(url, entry)
    tmp = path + ".part"
    with open(tmp, "wb") as f:
        f.write(data)
    os.replace(tmp, path)
    return len(data)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--vibration", action="store_true", help="also fetch the vibration parquets")
    ap.add_argument("--workers", type=int, default=2)
    add_background_flag(ap, default_log="data/download.log")
    args = ap.parse_args()
    maybe_detach(args)

    lst = listing()
    split = json.load(open(os.path.join(DATA, "split.json")))     # Stage 0a: only the quickstart's cycles
    wanted = {f for r in ("library", "validation", "test") for fs in split[r].values() for f in fs} | set(split["delivery"])
    os.makedirs(os.path.join(RAW, "labels"), exist_ok=True)
    os.makedirs(os.path.join(RAW, "vibration"), exist_ok=True)
    licence = os.path.join(DATA, "LICENCE.txt")
    if not os.path.exists(licence):
        urllib.request.urlretrieve(FILES["LICENCE.txt"], licence)

    jobs = [(FILES["general.zip"], e["entry"], os.path.join(DATA, os.path.basename(e["member"])))
            for e in lst["extras"].values()]
    for c in lst["cycles"]:
        if not c["vibration"] or c["file"] not in wanted:
            continue  # only the split's cycles (all have vibration)
        jobs.append((FILES["general.zip"], c["labels"]["entry"], os.path.join(RAW, "labels", c["file"])))
        if args.vibration:
            jobs.append((FILES["vibrations.zip"], c["vibration"]["entry"],
                         os.path.join(RAW, "vibration", os.path.basename(c["vibration"]["member"]))))

    total = sum(j[1][3] for j in jobs)
    print(f"{len(jobs)} files, {total / 1e6:,.0f} MB")
    t0, done, new = time.time(), 0, 0
    with ThreadPoolExecutor(args.workers) as pool:
        futures = {pool.submit(fetch, *j): j[2] for j in jobs}
        for i, fut in enumerate(as_completed(futures), 1):
            n = fut.result()
            done += n
            if n:  # one line per file actually downloaded; skipped files stay quiet
                new += 1
                print(f"  {i}/{len(jobs)}  {os.path.basename(futures[fut])}  {n / 1e6:.0f} MB"
                      f"  ({done / 1e6:,.0f} MB new, {time.time() - t0:.0f} s)", flush=True)
    print(f"done: {new} downloaded, {len(jobs) - new} already present")

    # merged with any earlier manifest, so a labels-only rerun keeps the vibration entries
    path = os.path.join(DATA, "manifest.json")
    old = {e["path"]: e for e in json.load(open(path))} if os.path.exists(path) else {}
    old.update({os.path.relpath(p, DATA): {"path": os.path.relpath(p, DATA),
                "archive": os.path.basename(u.split("/files/")[1].split("/")[0]), "size": e[3]} for u, e, p in jobs})
    manifest = sorted(old.values(), key=lambda e: e["path"])
    with open(os.path.join(DATA, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=1)
    print(f"wrote {os.path.join(DATA, 'manifest.json')}")


if __name__ == "__main__":
    main()
