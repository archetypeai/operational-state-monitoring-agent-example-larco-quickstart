# OSM quickstart: washing machines (LARCO)

The whole lifecycle of an Operational State Monitoring (OSM) agent, from raw data to a
delivered agent and its score, in about **1.5 hours**. Most of that is platform waiting.

It's the short version of [`osm-agent-example-larco`](https://github.com/archetypeai/osm-agent-example-larco),
which runs the same stages on all 199 cycles over several days. This one uses 19 short
cycles, about 3% of the data. Every stage and every platform API is still here:
Optimize, promote, Evals, bundles, runs. For the reasons behind each choice, follow
the links to the full example.

## TL;DR

- **What:** an agent that labels each 2.56 s window of a washing machine's vibration
  (9 accelerometer channels, 200 Hz) as **`fill`**, **`wash`**, **`spin`** or
  **`drain`**, using frozen Omega 1.5 embeddings and the platform's kNN.
- **Data:** [LARCO](#data-attribution) (CC BY 4.0).
  - **Training and scoring:** 14 short cycles of a healthy Becken BWM5381IX,
    split into library (training), validation and test.
  - **Delivery:** 5 cycles of a second, faulty unit of the same model, with
    their labels held back.
- **The lifecycle:**

| stage | what happens | time |
|---|---|---|
| 0 | pick the cycles and download them (142 MB) | ~2 min |
| 1a–1c | check the raw data, resample it to an exact 200 Hz grid, check again | ~10 s |
| 2–3 | build the files the platform receives, and check them against its rules | ~15 s |
| 4 | **Optimize:** train on the library, score 2 settings on validation | ~25 min |
| 5 | **promote** the best setting to a blueprint, **test** it once with the Evals API | ~45 min |
| 6 | **deliver:** a bundle from that blueprint, run over the faulty unit's cycles | ~10 min |
| 7 | **score** the delivery against the held-back labels | seconds |

- **Skip Stages 0–3:** the role files are in the repo, packed in Git LFS
  (~96 MB). See [Shortcut](#shortcut-skip-stages-03).

> **The scores here aren't the full example's.** The quickstart uses only short
> `warm_*` programs. It has no cotton or eco cycles, and its state mix is
> different (wash is ~60% of the time, not 83%). It shows how the lifecycle works;
> for results, see the full example.

## Setup

```sh
git clone https://github.com/archetypeai/osm-agent-example-larco-quickstart.git   # needs Git LFS
cd osm-agent-example-larco-quickstart
python3 -m venv .venv
source .venv/bin/activate          # every later command assumes this
pip install -r requirements.txt    # numpy, pandas, pyarrow, scipy
cp .env.example .env               # then set ATAI_API_KEY (needed from Stage 4 on)
```

### Shortcut: skip Stages 0–3

```sh
git lfs pull                               # data/archives/ (skip if the clone already fetched it)
python prep/archive_roles.py --unpack      # checks SHA256SUMS, rebuilds data/roles/ (0.45 GB)
python prep/preflight_roles.py             # Stage 3, to confirm: RESULT: PASS
```

Then go to [Stage 4](#stage-4-optimize).

## The stages

Everything runs from the repo root. Each stage is safe to rerun. The four long ones
(0, 4, 5, 6) take `--background`, which relaunches the command under `nohup` and,
on macOS, `caffeinate`, and logs to a file (`tail -f` it).

### Stage 0: pick the cycles, then download them

```sh
python prep/split.py                          # the 19 cycles → data/split.json
python prep/download.py --vibration           # 40 files, 142 MB, from Zenodo
```

- **The split:** `split.py` draws the full example's split (by setting group,
  seed 20260928), then keeps the 19 cycles in its `QUICKSTART` list, each in the
  role the full split gave it. These are the shortest `warm_*` programs of each
  role. They still have all four states, with as much fill, spin and drain as a
  3-hour cotton cycle.
- **What you should see:** `library 8 groups, 8 cycles`, `validation 3`, `test 3`,
  `delivery 5 cycles of becken-flt_BWM5381IX`; then `40 files, 142 MB`.
- **Why a split by setting group:** so near-identical twin cycles never end up
  on both sides. See the
  [full example](https://github.com/archetypeai/osm-agent-example-larco#how-beckens-cycles-are-split).

### Stage 1: check, prepare, check again

```sh
python prep/preflight_raw.py         # 1a: the raw cycles, read-only
python prep/prepare.py               # 1b: an exact 200 Hz grid, one state per row
python prep/preflight_prepared.py    # 1c: the prepared files, read-only
```

- **1a** checks each cycle: files, schema, label values, timelines, sample
  rate, glitches, channels, states, coverage and alignment.
  - **Expect:** `RESULT: PASS`, with 3 warnings. One is a clipping warning on
    delivery `warm_fast-15_6`; two are known spin-end offsets in the library.
- **1b** resamples each cycle's vibration onto an exact 5 ms grid (cubic spline)
  and gives every row a state:
  - `spin` if the machine's spin label is on;
  - else `fill` or `drain` from its water-flow label;
  - else `wash`.

  The heater isn't a state, because vibration can't tell it from wash
  ([why](https://github.com/archetypeai/osm-agent-example-larco#the-states)).
  **Expect:** `19 cycles, 9,826,559 rows, 0.13 GB`.
- **1c** re-checks the grid, the values and every row's state against the raw
  labels, and runs a resampling tone test. **Expect:** `RESULT: PASS`. The
  hours per state are printed per role, e.g. library fill 0.9, wash 4.5, spin
  1.0, drain 0.7.

Each preflight prints PASS / WARN / FAIL per check. The next stage won't run
while one fails.

### Stage 2: build the role files

```sh
python prep/build_roles.py           # → data/roles/ (0.45 GB)
```

- **Normalisation:** every channel is z-scored with the mean and standard
  deviation of the library cycles only.
- **Library:** 4 files, one per state, each holding **100 windows** of 1,024
  rows from the 8 library cycles, as continuous pieces with real timestamps.
- **Validation, test and delivery:** one continuous file per cycle.
  Validation and test have a `label` column. Delivery doesn't: its labels go
  to `delivery_labels/`.

**Expect:**

```
library: 4 files (one per state, 396 pieces), windows per state {'fill': 100, 'wash': 100, 'spin': 100, 'drain': 100}
validation: 3 files from 3 cycles, 1,224 windows
test: 3 files from 3 cycles, 1,760 windows
delivery: 5 files from 5 cycles, 1,587 windows
```

(Windows here are 1,024 rows; the platform uses 512, so about twice as many.)
Why one file per state and not one per piece: the platform stores each
optimization's config in a 1 MiB object, which too many training files overflow
([details](https://github.com/archetypeai/osm-agent-example-larco#platform-behaviour-worth-knowing)).

### Stage 3: check the role files against the platform's rules

```sh
python prep/preflight_roles.py
```

It checks:
- timestamps, an exact 5 ms timeline and finite values;
- one state in every library piece, with time jumps only between pieces;
- unlabelled delivery files, with a matching held-back label file each;
- scaling with the library statistics, and no cycle in two roles;
- 100 windows per state;
- all four states in validation, test and delivery.

**Expect:** every check `PASS`, then `RESULT: PASS, no blocking failures. Stage 4 may run.`

### Stage 4: Optimize

```sh
python fit/optimize.py --dry-run                 # the plan; no uploads, no jobs
python fit/optimize.py --background              # log fit/out/optimize.log
```

1. **Upload:** it uploads the 4 library files and 3 validation files once.
   The ids are cached in `fit/out/uploads.json`.
2. **Optimize:** it creates an optimization on the `osm` blueprint. Each
   library file is a training example labelled by its state; each validation
   file is scored on its `label` column, each window by its last row.
3. **Search space:** two settings, window 512, step 512, cosine, uniform, with
   **k = 5** and **k = 31**. The platform embeds every window with Omega 1.5,
   fits a kNN on the library and reports macro-F1 on validation for each.

The platform runs trials one after another, about 12 minutes each. The
full example searched 16 settings and found 512 / 512, k 31, cosine, uniform best.
Try others with `--k`, `--windows`, `--steps`, `--metrics`, `--weights`.

**Expect:** `window 512, step 512: library 4 files / 800 windows kept; validation 3
files / 2,448 windows`, `2 trial(s) of a 2-point space`, then one line per
finished trial and a ranking by macro-F1.
**Writes:** `fit/out/optimize_<optimization id>.json`.

### Stage 5: promote, then test once

```sh
python fit/test.py --background                  # log fit/out/test.log
```

1. **Promote** the best trial from Stage 4 to a blueprint,
   `osm-larco-quickstart-w512-s512-…`: its setting and fitted kNN become a
   reusable model. `--trial otr_…` picks another trial.
2. **Upload** the 3 test cycles.
3. **Evaluate** them **once** with the Evals API, which scores the blueprint
   on files it has never seen. The eval takes about 40 minutes, even for small
   files.

This is the one-shot test. Once you've seen its number, the setting is
frozen: going back to tune it would make the test number optimistic.
**Writes:** `fit/out/test.json` and `fit/out/test_state.json` (the ids, so a
rerun resumes instead of testing again).

### Stage 6: deliver to the second machine

```sh
python fit/deliver.py --background               # log fit/out/deliver.log
```

1. **Upload** becken-flt's 5 cycles, which have no labels.
2. **Bundle:** create a bundle from Stage 5's blueprint, unchanged: the
   deployable agent.
3. **Run** it over the files. One worker takes the files in turn and writes one
   output per file: each window's `predicted_state`, its `finish_timestamp`, an
   `invalid` flag and a probability per state.
4. **Download** the outputs and match them to files by timestamp.

**Writes:** `fit/out/delivery/<file>.csv` and `fit/out/delivery/runs.json`
(`--resume` collects the runs without starting new ones).

### Stage 7: score the delivery

```sh
python fit/score_delivery.py
```

- **Pairing:** each prediction is paired with the held-back label at its
  window's last row.
- **Left out and counted:** windows the platform marked invalid.
- **Reported:** macro-F1 and F1 per state, pooled and per cycle, next to
  Stage 5's test number. The test is new settings of the same machine; the
  delivery is a second, faulty unit. Comparing the two shows how well the
  agent carries over to another machine.

**Writes:** `fit/out/delivery/scores.json`.

## What's left out, compared with the full example

| full example | here | why |
|---|---|---|
| 199 cycles (all programs) | 19 short `warm_*` cycles | time: 142 MB instead of 4.7 GB |
| 400 library windows per state | 100 | 8 library cycles instead of 54 |
| a bar (simple baselines) | none | the quickstart shows the lifecycle, not a benchmark |
| a 16-trial search | 2 trials | each trial costs ~12 min of platform time |
| a confirmation on all validation cycles (4c) | none | here all validation cycles are already in the search |
| 5 delivery runs of 25 files | 1 run | 5 files |

## Repo layout

| path | what it is |
|---|---|
| `prep/split.py`, `prep/download.py`, `prep/larco.py` | Stage 0: the 19 cycles, and reading them from Zenodo |
| `prep/preflight_raw.py`, `prep/prepare.py`, `prep/preflight_prepared.py` | Stage 1 |
| `prep/states.py` | the four states and the per-second rule, shared by every stage |
| `prep/build_roles.py`, `prep/preflight_roles.py` | Stages 2 and 3 |
| `prep/preflight_common.py`, `prep/acknowledged.py`, `prep/background.py` | the shared preflight report, kept FAILs (none here), `--background` |
| `prep/archive_roles.py` | pack or unpack `data/roles/` (Git LFS) |
| `fit/atai.py` | stdlib platform helpers: requests, uploads, polling |
| `fit/optimize.py` | Stage 4 |
| `fit/test.py` | Stage 5 |
| `fit/deliver.py` | Stage 6 |
| `fit/score_delivery.py` | Stage 7 |
| `data/` | the split, the Zenodo listing, the licence, metadata and preflight reports; `data/archives/` holds the packed role files |
| `plan.md` | how the quickstart was designed |

## Data attribution

All washing-machine data used here comes from the **LARCO** dataset, "Household
**La**undry Appliance **R**esource **C**onsumption and **O**peration Dataset":

- **Authors:** Žygimantas Jasiūnas, João Alexandre Braz Ferreira, Tiago Julião,
  José Cecílio, Guilherme Carrilho da Graça, Pedro M. Ferreira.
- **Dataset:** Zenodo, 2026, [doi:10.5281/zenodo.19666168](https://doi.org/10.5281/zenodo.19666168).
- **Paper:** *Scientific Data*, 2026, [doi:10.1038/s41597-026-07361-6](https://doi.org/10.1038/s41597-026-07361-6).
- **Licence:** `general.zip` and `vibrations.zip` are under
  [Creative Commons Attribution 4.0 International (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).
  The dataset's `LICENCE.txt` is in `data/`.
- **No endorsement:** the authors are not involved in this example and do not
  endorse it.

**What was changed** (as CC BY 4.0 requires):
- a subset of 19 cycles was selected;
- the vibration was resampled to an exact 200 Hz grid, with samples beyond
  ±2.2 g removed;
- labels were derived per second: fill / wash / spin / drain, with heating
  folded into its drum or water state;
- every channel was z-scored with the library cycles' statistics.

The packed role files in `data/archives/` are these derived files. Code in this
repository is Archetype AI's and carries no LARCO licence obligation; the
attribution above applies to the data.
