#!/usr/bin/env python3
"""Pack data/roles/ into a Git LFS archive, or unpack it, so Stages 4-7 can run without 0-3.

data/roles/ (Stage 2) is packed into one tar.xz archive, split into 900 MB parts if
needed, under data/archives/ (tracked by Git LFS; about 5x smaller than the CSVs).
data/archives/SHA256SUMS holds a checksum per part. Unpacking checks them first, then
rebuilds data/roles/, and Stage 3 (prep/preflight_roles.py) checks the result.

    python prep/archive_roles.py --pack        # ~1 min
    python prep/archive_roles.py --unpack      # after git lfs pull
"""
import argparse
import glob
import hashlib
import os
import shutil
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from background import add_background_flag, maybe_detach  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.path.join(ROOT, "data")
ARCHIVES = os.path.join(DATA, "archives")
SUMS = os.path.join(ARCHIVES, "SHA256SUMS")
PART = "900m"
SETS = {"all": ["roles"]}


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 22), b""):
            h.update(block)
    return h.hexdigest()


def pack(names):
    for tool in ("tar", "xz", "split"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found")
    os.makedirs(ARCHIVES, exist_ok=True)
    for name in names:
        missing = [p for p in SETS[name] if not os.path.exists(os.path.join(DATA, p))]
        if missing:
            sys.exit(f"missing {missing}: run Stage 2 (prep/build_roles.py) first")
        for old in glob.glob(os.path.join(ARCHIVES, f"roles_{name}.tar.xz.part-*")):
            os.remove(old)
        prefix = os.path.join(ARCHIVES, f"roles_{name}.tar.xz.part-")
        print(f"packing {name}: {' '.join(SETS[name])}", flush=True)
        tar = subprocess.Popen(["tar", "-C", DATA, "-cf", "-", *SETS[name]], stdout=subprocess.PIPE)
        xz = subprocess.Popen(["xz", "-T0", "-6", "-c"], stdin=tar.stdout, stdout=subprocess.PIPE)
        tar.stdout.close()
        split = subprocess.Popen(["split", "-b", PART, "-", prefix], stdin=xz.stdout)
        xz.stdout.close()
        if split.wait() or xz.wait() or tar.wait():
            sys.exit(f"packing {name} failed")
        parts = sorted(glob.glob(prefix + "*"))
        print(f"  {len(parts)} part(s), {sum(map(os.path.getsize, parts)) / 1e9:.2f} GB", flush=True)
    sums = {}
    if os.path.exists(SUMS):
        for line in open(SUMS):
            digest, part = line.split()
            sums[part] = digest
    for part in sorted(glob.glob(os.path.join(ARCHIVES, "roles_*.tar.xz.part-*"))):
        if any(os.path.basename(part).startswith(f"roles_{n}.") for n in names):
            sums[os.path.basename(part)] = sha256(part)
    sums = {p: d for p, d in sums.items() if os.path.exists(os.path.join(ARCHIVES, p))}
    with open(SUMS, "w") as f:
        f.writelines(f"{d}  {p}\n" for p, d in sorted(sums.items()))
    print(f"wrote {SUMS} ({len(sums)} parts)")


def unpack(names):
    sums = dict(reversed(line.split()) for line in open(SUMS))
    for name in names:
        parts = sorted(p for p in sums if p.startswith(f"roles_{name}."))
        if not parts:
            sys.exit(f"no parts for {name} in {SUMS}")
        for p in parts:
            path = os.path.join(ARCHIVES, p)
            if not os.path.exists(path) or os.path.getsize(path) < 1024 and open(path, "rb").read(7) == b"version":
                sys.exit(f"{p} is missing or still an LFS pointer: run `git lfs pull` first")
            if sha256(path) != sums[p]:
                sys.exit(f"{p}: checksum differs from SHA256SUMS")
        print(f"unpacking {name}: {len(parts)} part(s), checksums OK", flush=True)
        cat = subprocess.Popen(["cat", *[os.path.join(ARCHIVES, p) for p in parts]], stdout=subprocess.PIPE)
        tar = subprocess.Popen(["tar", "-C", DATA, "-xJf", "-"], stdin=cat.stdout)
        cat.stdout.close()
        if tar.wait() or cat.wait():
            sys.exit(f"unpacking {name} failed")
    print("done; check the result with: python prep/preflight_roles.py")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--pack", action="store_true")
    mode.add_argument("--unpack", action="store_true")
    ap.add_argument("--only", choices=list(SETS), help="one archive instead of both")
    add_background_flag(ap, default_log="data/archive.log")
    args = ap.parse_args()
    maybe_detach(args)
    names = [args.only] if args.only else list(SETS)
    pack(names) if args.pack else unpack(names)


if __name__ == "__main__":
    main()
