"""The PASS / WARN / FAIL report every preflight stage prints and writes.

Each cycle's result is a dict with "file", "role" and "checks":
{check name: (level, message)}. Acknowledged FAILs (acknowledged.py) are
printed with their reason but don't count as blocking.
"""
import json

from acknowledged import ACKNOWLEDGED


def report(results, title, next_stage, path, extra=None, notes=""):
    """Print one line per check, list every non-PASS cycle, then `notes`; write `path`; return the blocking FAIL count."""
    names = []
    for r in results:
        names += [c for c in r["checks"] if c not in names]
    print(f"{title}\n")
    n_fail = 0
    for c in names:
        # A missing check is "not reached" (a FAIL) only for a result that already failed
        # something, since checks stop early after a FAIL. Otherwise it doesn't apply to
        # that result: role-specific file checks, or whole-role checks.
        scoped = [r for r in results if c in r["checks"] or any(lv == "FAIL" for lv, _ in r["checks"].values())]
        levels = [r["checks"].get(c, ("FAIL", "not reached"))[0] for r in scoped]
        acked = sum(1 for r in scoped if r["checks"].get(c, ("",))[0] == "FAIL" and (r["file"], c) in ACKNOWLEDGED)
        worst = "FAIL" if levels.count("FAIL") > acked else "WARN" if "WARN" in levels or acked else "PASS"
        n_fail += levels.count("FAIL") - acked
        print(f"  {worst:<4}  {c:<15} {levels.count('PASS')} pass, {levels.count('WARN')} warn, {levels.count('FAIL')} fail")
        for r in scoped:
            lv, msg = r["checks"].get(c, ("FAIL", "not reached"))
            if lv != "PASS":
                note = f"  [acknowledged: {ACKNOWLEDGED[(r['file'], c)]}]" if lv == "FAIL" and (r["file"], c) in ACKNOWLEDGED else ""
                print(f"          {lv}  {r['role']:<10} {r['file']}: {msg}{note}")

    if notes:
        print("\n" + notes.rstrip())
    out = {"cycles": results, "failures": n_fail,
           "acknowledged": [{"file": f, "check": c, "reason": why} for (f, c), why in ACKNOWLEDGED.items()],
           **(extra or {})}
    with open(path, "w") as f:
        json.dump(out, f, indent=1, default=str)
    print(f"\nreport written to {path}")
    if n_fail:
        print(f"RESULT: FAIL, {n_fail} failed check(s) above. {next_stage} is blocked until they are fixed or acknowledged.")
    else:
        acked = sum(1 for r in results for c, (lv, _) in r["checks"].items() if lv == "FAIL" and (r["file"], c) in ACKNOWLEDGED)
        print(f"RESULT: PASS, no blocking failures{f' ({acked} acknowledged, listed above)' if acked else ''}. {next_stage} may run.")
    return n_fail


def roles_of(split):
    """{cycle file: role} from data/split.json."""
    roles = {f: r for r in ("library", "validation", "test") for grp in split[r].values() for f in grp}
    roles.update({f: "delivery" for f in split["delivery"]})
    return roles
