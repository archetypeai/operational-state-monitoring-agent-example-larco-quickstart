#!/usr/bin/env python3
"""Stage 0a: the quickstart's cycles, a subset of the full example's split, before any download.

The full example (osm-agent-example-larco) splits becken's 93 cycles by setting group
(program × wash temperature × load): whole groups go to library, validation or test at
random, stratified by family (cotton / eco / other), so no near-twin straddles two
roles, with seed 20260928. becken-flt is delivery only.

The quickstart draws that same split, then keeps only the 19 short cycles in QUICKSTART:
the shortest `warm_*` programs of each role, which still hold all four states with as
much fill, spin and drain as a 3-hour cotton cycle. They were chosen by that rule from
the full example's Stage 1b report; Stage 3 checks that every role
still has all four states. So no group crosses roles here either. Writes data/split.json.

    python3 prep/split.py
"""
import collections
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from larco import DATA, DELIVERY_UNIT, LIBRARY_UNIT, listing  # noqa: E402

SEED = 20260928
# groups per role and family: library / validation / test
ALLOCATION = {"cotton": (16, 6, 6), "eco": (4, 2, 1), "other": (14, 5, 4)}
ROLES = ("library", "validation", "test")
QUICKSTART = {
    "library": ["warm_15-min_40_2", "warm_sport_40_2", "warm_delicate_30_0", "warm_sport_40_6",
                "warm_20-deg_20_0", "warm_mix_40_2", "warm_delicate_30_6", "warm_wool_40_0"],
    "validation": ["warm_15-min_40_0", "warm_fast-45_40_2", "warm_sport_40_0"],
    "test": ["warm_delicate_30_2", "warm_fast-45_40_0", "warm_20-deg_20_2"],
    "delivery": ["warm_fast-15_2", "warm_fast-15_0", "warm_fast-15_6", "warm_fast-45_40_2", "warm_sport_40_2"],
}


def family(program):
    return program if program in ("cotton", "eco") else "other"


def main():
    cycles = listing()["cycles"]
    becken = [c for c in cycles if c["unit"] == LIBRARY_UNIT and c["vibration"]]
    groups = collections.defaultdict(list)
    for c in becken:
        groups[c["setting"]].append(c["file"])

    rng = random.Random(SEED)
    role_of = {}
    for fam, counts in ALLOCATION.items():
        names = sorted(g for g in groups if family(g.split("_")[0]) == fam)
        if len(names) != sum(counts):
            sys.exit(f"{fam}: {len(names)} groups in the listing, allocation expects {sum(counts)}")
        rng.shuffle(names)
        start = 0
        for role, n in zip(ROLES, counts):
            for g in names[start:start + n]:
                role_of[g] = role
            start += n

    split = {role: {} for role in ROLES}
    for g in sorted(groups):
        split[role_of[g]][g] = sorted(groups[g])
    split["delivery"] = sorted(c["file"] for c in cycles if c["unit"] == DELIVERY_UNIT and c["vibration"])

    # keep the quickstart's cycles, each in the role the full split gave it
    unit_of = {"library": LIBRARY_UNIT, "validation": LIBRARY_UNIT, "test": LIBRARY_UNIT, "delivery": DELIVERY_UNIT}
    want = {role: {f"wm_{unit_of[role]}_{name}.csv" for name in names} for role, names in QUICKSTART.items()}
    for role in ROLES:
        split[role] = {g: [f for f in fs if f in want[role]] for g, fs in split[role].items()}
        split[role] = {g: fs for g, fs in split[role].items() if fs}
    split["delivery"] = [f for f in split["delivery"] if f in want["delivery"]]
    kept = {f for r in ROLES for fs in split[r].values() for f in fs} | set(split["delivery"])
    lost = sorted(f for fs in want.values() for f in fs if f not in kept)
    if lost:
        sys.exit(f"not in their role in the full split (or no vibration): {lost}")
    out = {"seed": SEED, "allocation": ALLOCATION, "library_unit": LIBRARY_UNIT, "delivery_unit": DELIVERY_UNIT,
           "subset": "quickstart: the 19 cycles in prep/split.py QUICKSTART, drawn from the full split", **split}
    with open(os.path.join(DATA, "split.json"), "w") as f:
        json.dump(out, f, indent=1)

    for role in ROLES:
        fams = collections.Counter(family(g.split("_")[0]) for g in split[role])
        n = sum(len(v) for v in split[role].values())
        print(f"{role:<10} {len(split[role]):>2} groups, {n:>2} cycles  {dict(fams)}")
    print(f"{'delivery':<10} {len(split['delivery']):>3} cycles of {DELIVERY_UNIT}")
    print(f"wrote {os.path.join(DATA, 'split.json')} (seed {SEED})")


if __name__ == "__main__":
    main()
