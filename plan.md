# Plan: LARCO quickstart, Stages 0–7 in about 1.5 hours

Status (2026-09-30): **built. Stages 0–3 verified; Stages 4–7 not yet run.**

**Decisions since this plan was written (user, 2026-09-30):**
- **A separate project,** named `osm-agent-example-larco-quickstart`, so a clone
  doesn't fetch the full example's ~5 GB of LFS.
- **No bar (Stage 4a):** the quickstart shows the lifecycle, not a benchmark.
  `baseline_bar.py` and `compare_bar.py` were removed, and Stage 7 reports Omega
  alone, next to the test.
- **No Stage 4c, no probes, no five-state diagnostic:** they're in the full example.
  Stage 5 promotes the best Stage 4b trial directly.

**Verified (Stages 0–3, 2026-09-30):**
- **Stage 0:** 19 cycles, 40 files, 142 MB.
- **Stage 1a:** PASS, with 3 warnings (1 clipping, 2 spin-end offsets).
- **Stage 1b:** 9,826,559 rows, 0.13 GB.
- **Stage 1c:** PASS.
- **Stage 2:** library 4 files (396 pieces, 100 windows per state); validation
  1,224 windows, test 1,760, delivery 1,587 (at 1,024 rows); 0.45 GB. The
  compute for Stages 1–3 was about 30 s.
- **Stage 3:** PASS on 15 files, all four states in every role.
- **Packed:** `data/archives/roles_all.tar.xz.part-aa`, 96 MB.
- **Stage 4 dry run:** 800 library and 2,448 validation windows at 512 / 512;
  2 trials.

---
 It's a companion to the full example
([`osm-agent-example-larco`](https://github.com/archetypeai/osm-agent-example-larco)):
the same stages, on about 3% of the data, in its own project.

## Why

The full example takes days to follow. The wall-clock time alone is about 9 hours,
spent mostly waiting:

| stage | full example | what it spends |
|---|---|---|
| 0 download | ~35 min | 4.7 GB from Zenodo |
| 1a–3 prep and preflights | ~10 min | 25.45 GB of role files |
| 4a bars | ~1 h | 9 window/step pairs |
| 4b search | ~3.5 h | 16 trials, one at a time, ~11 min fixed each |
| 4c confirm | 44 min | one trial on 25 files |
| 5 test | ~40 min | the Evals API (~40 min even for one file) |
| 6 delivery | ~3 h | 15 GB upload, 5 runs one at a time |
| 7 score | ~5 min | local |

Around that come the decisions, the reading and the reruns. The quickstart version keeps
every stage and every API a user needs (Optimize, promote, Evals, bundles, runs),
but on few, short cycles. So the only long waits left are the platform's fixed
costs.

## The data: short programs keep every state

Cotton and eco cycles run about 3 hours, and 83% of that is wash. The short `warm_*`
programs (15 min to 1.4 h) have **all four states, with fill, spin and drain
lasting about as long as in a cotton cycle** (fill 5–10 min, spin 2–15 min, drain
3–7 min). Drain is the scarce state, and they keep it.

The subset is drawn **from the existing split** (`data/split.json`, seed 20260928),
so no setting group crosses roles and the quickstart roles are subsets of the full ones:

| role | cycles (from the full split) | hours |
|---|---|---|
| library | becken `warm_15-min_40_2`, `warm_sport_40_2`, `warm_delicate_30_0`, `warm_sport_40_6`, `warm_20-deg_20_0`, `warm_mix_40_2`, `warm_delicate_30_6`, `warm_wool_40_0` | 7.1 |
| validation | becken `warm_15-min_40_0`, `warm_fast-45_40_2`, `warm_sport_40_0` | 1.8 |
| test | becken `warm_delicate_30_2`, `warm_fast-45_40_0`, `warm_20-deg_20_2` | 2.5 |
| delivery | becken-flt `warm_fast-15_2`, `warm_fast-15_0`, `warm_fast-15_6`, `warm_fast-45_40_2`, `warm_sport_40_2` | 2.3 |

That's 19 cycles and about 14 hours of recording (the full example has 199 cycles
and 517 hours).
- **Raw download:** ~200 MB.
- **Role files:** ~0.7 GB of CSV, ~150 MB packed. Every file is under 100 MB.
- **State check:** every role must still have all four states. The rule, not
  these names, is what's fixed: a script picks the cycles, and Stage 3 checks the
  result.
- **The trade-off, stated in the README:** the quickstart data has no cotton or eco. Its
  state mix is also different: wash is 30–60% of the time, not 83%. So its scores
  aren't comparable to the full example's. It teaches the lifecycle, not the
  result. An optional variant adds one cotton cycle to test and delivery (+6 h of
  data, and a few minutes of run time).

## Stage by stage

| stage | quickstart version | expected time |
|---|---|---|
| 0 split and download | `split.py` writes `data/split.json` (the subset above); download fetches only those 19 cycles | ~3 min |
| 1a–1c | unchanged scripts, on 19 cycles | ~1 min |
| 2 roles | library at **100 windows per state** (400 in the full); validation, test and delivery whole | <1 min |
| 3 preflight | unchanged | <1 min |
| 4a bar | **dropped** (user decision): no baseline in the quickstart | 0 |
| 4b Optimize | **2 trials**: k 5 and k 31 (cosine, uniform, 512 / 512). It shows a search and reading trial scores without the 3.5 h | ~25 min |
| 4c confirm | **dropped:** with 3 validation cycles there's nothing held out. The README says why the full example has it | 0 |
| 5 test | promote the better trial, **one eval** on the 3 test cycles | ~40 min (platform floor) |
| 6 delivery | one bundle, **one run** over the 5 files | ~5–10 min |
| 7 score | unchanged, plus the bar on the same windows | <1 min |
| **total** | | **~1.3–1.5 h**, almost all platform waiting |

## How it's built: a separate project (recommended)

**`osm-agent-example-larco-quickstart`, a new repo,** started from the full example at
commit `133cadb`. The full repo stays as it is, apart from a README link to the
quickstart one.

- **Why separate:** the full example is finished, with its results recorded.
  Adding a profile switch would mean editing every one of its verified scripts,
  and could change numbers it already reports. A separate project also lets
  the quickstart one be *simpler*, not just smaller:
  - its subset is fixed in the code, with no profile branching;
  - no Stage 4c, and no five-state history;
  - no probes (`probe_timestamps.py`, `probe_gaps.py`) and no diagnostics
    (`diagnose_wash_heating.py`); it links to the full example's findings
    instead.
- **What it costs:** two copies of the prep and fit code. That's acceptable
  here because the full example is frozen, so there's little to keep in sync.
  The quickstart README says which commit it came from. A fix made later in either
  repo is copied by hand, and noted.
- **Data:** the quickstart role files (~0.7 GB of CSV, ~150 MB packed) go in Git LFS
  with `prep/archive_roles.py`, so a colleague can skip even the quickstart Stages 0–3.
  - **Platform names:** separate blueprint keys and name prefixes
    (`osm-larco-quickstart-…`, `LARCO quickstart …`), so its platform objects never mix
    with the full example's.
  - **Upload caches:** separate by construction.
- **README:** one page, one command block per stage with its expected output
  and time. Each stage links to its section in the full example, for the why.

**The alternative, a profile inside the full repo (not chosen):** there's one
set of scripts, and a fix reaches both. But every clone of the full repo would
fetch its ~5 GB of LFS archives, even for the quickstart. But it means refactoring the finished
pipeline's paths and settings, and re-verifying the full profile against its
recorded outputs.

## Steps to build

1. **Done:** `~/Downloads/demo/osm-agent-example-larco-quickstart` created from the full
   repo at `133cadb` (its `plan.md` kept as `plan-full-reference.md` for now): the Stage 0–7 scripts, `requirements.txt`,
   `.env.example`, `.gitignore`, `.gitattributes`. No data, no `fit/out`.
2. `split.py`: the subset rule (the shortest cycles per role, drawn from the
   full split, enough pure windows of every state), writing `data/split.json`.
3. Trim: the quickstart settings go in the scripts (100 windows per state, 3 search
   cycles, one pair for the bar, 2 trials, one eval, one delivery run). Remove
   Stage 4c, the probes and the five-state code.
4. Run Stages 0–3; record the counts and sizes.
5. Run Stages 4–7 once, end to end; record the times and outputs.
6. Write the README and a short `plan.md`; pack `data/roles`; create
   `github.com/archetypeai/osm-agent-example-larco-quickstart` and push.
7. In the full repo: one README line linking to the quickstart version.

## Open questions

- **Keep the two-trial search in 4b, or use one fixed setting?** Two trials
  add ~12 min but show what a search is.
- **Add one cotton cycle** to test and delivery, for realism? +6 h of data,
  ~5 min more at runtime.
- **Stage 5's ~40-minute eval floor is the biggest single wait.** Scoring the test
  cycles as a one-trial optimization would take ~12 min, but it wouldn't show
  the Evals API. I'd keep Evals.
