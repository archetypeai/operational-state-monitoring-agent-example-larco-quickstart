# Operational State Monitoring on washing machines (LARCO): quickstart

> This is the quickstart version. For the full version, see
> [archetypeai/osm-agent-example-larco](https://github.com/archetypeai/osm-agent-example-larco).

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

| stage | what happens | time (measured 2026-09-30) |
|---|---|---|
| 0 | pick the cycles and download them (142 MB) | ~2 min |
| 1a–1c | check the raw data, resample it to an exact 200 Hz grid, check again | ~10 s |
| 2–3 | build the files the platform receives, and check them against its rules | ~15 s |
| 4 | **Optimize:** train on the library, score 2 settings on validation | 4 min |
| 5 | **promote** the best setting to a blueprint, **test** it once with the Evals API | 3 min |
| 6 | **deliver:** a bundle from that blueprint, run over the faulty unit's cycles | 3 min |
| 7 | **score** the delivery against the held-back labels | seconds |

  About **15 minutes** end to end, 10 of them on the platform. Platform times vary
  with load: the full example's much larger jobs took ~12 minutes per trial and
  ~40 per eval.

- **Skip Stages 0–3:** the role files are in the repo, packed in Git LFS
  (~96 MB). See [Shortcut](#shortcut-skip-stages-03).

> **The scores here aren't the full example's.** The quickstart uses only short
> `warm_*` programs. It has no cotton or eco cycles, and its state mix is
> different (wash is ~60% of the time, not 83%). It shows how the lifecycle works;
> for results, see the full example.

## The scenario

- **A customer shares labelled cycles from one washing machine,** the healthy
  **Becken BWM5381IX** (`becken`). Here that's 14 short cycles.
- **We report how the agent does on settings of that machine it has never
  seen** (the test).
- **We then run it over a second unit of the same model** (`becken-flt`, 5
  cycles), with its labels hidden, and score the predictions afterwards (the
  delivery).

Two things about the second unit:
- **The dataset marks becken-flt as faulty,** without saying what the fault is.
  It plays no part in any choice here, so its delivery score is a clean
  "machine the model has never seen" number.
- **The test number is the easier one:** new settings of the same machine, not a
  new machine. Stage 7 prints both side by side.

**The model sees only the 9 vibration channels** (3 triaxial accelerometers: back,
side, top). The dataset also records power, water flow and temperatures at 1 Hz,
but the labels are derived from exactly those readings, so as inputs they would
give the answer away. A clip-on accelerometer is all you'd need; no access to the
machine's controller.

### The states

| state | what the machine is doing | what the accelerometer sees |
|---|---|---|
| `fill` | water entering, motor off | water rushing through the valve and pipes: louder than wash |
| `wash` | drum tumbling back and forth, with pauses | a slow reversing pattern, quiet in the pauses |
| `spin` | steady high-speed rotation | a strong, steady tone: the loudest state |
| `drain` | the pump emptying the drum | louder than wash, in short bursts |

### The cycles, and what each role's files are

The 19 cycles are the shortest `warm_*` programs of each role in the full example's
split. That split assigns whole setting groups (program × temperature × load) to
roles at random, so near-identical cycles never end up on both sides.

| role | cycles | files | windows at 512 / 512 | labels | used in |
|---|---|---|---|---|---|
| library (training) | 8 becken: `warm_15-min_40_2`, `sport_40_2`, `delicate_30_0`, `sport_40_6`, `20-deg_20_0`, `mix_40_2`, `delicate_30_6`, `wool_40_0` | **4, one per state** | 800 (200 per state) | the state, by filename | 4: every trial |
| validation | 3 becken: `warm_15-min_40_0`, `fast-45_40_2`, `sport_40_0` | 3 | 2,448 | `label` column | 4: scores each trial |
| test | 3 becken: `warm_20-deg_20_2`, `delicate_30_2`, `fast-45_40_0` | 3 | 3,520 | `label` column | 5: scored once |
| delivery | 5 becken-flt: `warm_fast-15_0`, `fast-15_2`, `fast-15_6`, `fast-45_40_2`, `sport_40_2` | 5 | 3,178 | none (held back in `delivery_labels/`) | 6: run; 7: scored against the held-back labels |

- **The library:** each state's file holds its windows as continuous pieces from
  all 8 cycles, in time order with real timestamps. Jumps fall only between
  pieces, and in training any window across a jump is skipped.
- **Validation, test and delivery** stay **one continuous file per cycle.** A
  single scored window across a time jump would fail the whole trial or eval.

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
python prep/archive_roles.py --unpack      # checks SHA256SUMS, rebuilds data/roles/ (0.47 GB)
python prep/preflight_roles.py             # Stage 3, to confirm: RESULT: PASS
```

Stage 3 then warns on two checks, `one state` and `scaling`: they compare the role
files with `data/prepared/`, which only Stage 1 makes. The other checks run as
usual, and the result is still `PASS`.

Then go to [Stage 4](#stage-4-optimize).

## The stages

**To run again from scratch,** move the previous outputs aside first:
`data/raw`, `data/prepared`, `data/roles` and `fit/out`. `fit/out/` matters most:
it holds `test_state.json` and `delivery/runs.json`, and with them `test.py` and
`deliver.py` resume the earlier run instead of starting a new one.

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
python prep/build_roles.py           # → data/roles/ (0.47 GB)
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

The platform runs trials one after another. Ours took 4 minutes for both; on busier days or bigger jobs it can take much longer (the full example's trials took ~12 minutes each). The
full example searched 16 settings and found 512 / 512, k 31, cosine, uniform best.
Try others with `--k`, `--windows`, `--steps`, `--metrics`, `--weights`.

**Expect:** `window 512, step 512: library 4 files / 800 windows kept; validation 3
files / 2,448 windows`, `2 trial(s) of a 2-point space`, then one line per
finished trial and a ranking by macro-F1. Ours:

```
  #1   w=512  step=512  k=5   cosine uniform  completed macro-F1 0.8305  fill 0.99  wash 0.90  spin 0.65  drain 0.78  windows 2,448
  #2   w=512  step=512  k=31  cosine uniform  completed macro-F1 0.7980  fill 0.99  wash 0.89  spin 0.59  drain 0.72  windows 2,448
```

Here k = 5 wins, unlike the full example (k 31). This library has 200 windows per
state at 512 / 512, not 800, so a smaller neighbourhood fits it better.
**Writes:** `fit/out/optimize_<optimization id>.json`.

### Stage 5: promote, then test once

```sh
python fit/test.py --background                  # log fit/out/test.log
```

1. **Promote** the best trial from Stage 4 to a blueprint,
   `osm-larco-quickstart-w512-s512-…-<trial>` (the trial's id makes it unique
   per run): its setting and fitted kNN become a reusable model. `--trial otr_…` picks another trial.
2. **Upload** the 3 test cycles.
3. **Evaluate** them **once** with the Evals API, which scores the blueprint
   on files it has never seen. Ours took 3 minutes (the full example's evals
   took ~40).

**Expect** (ours):

```
  all 3 test cycles  macro-F1 0.8617  fill 0.99  wash 0.93  spin 0.75  drain 0.78  windows 3,520
```

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

**Expect:** the upload (5 files, 134 MB), `bundle bnd_… from osm-larco-quickstart-…`,
one run, then `5 of 5 files have predictions: 3,178 windows (0 invalid); 0 rows
matched no file; failed runs: none`.

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

**Expect** (ours):

```
Stage 7: 5 becken-flt cycles, 5 files, 3,178 windows scored; left out {'invalid': 0, 'no label at finish time': 0}
  all 5 cycles                             macro-F1 0.7707  fill 0.84  wash 0.86  spin 0.67  drain 0.71  windows 3,178
  ...
next to Stage 5's test (becken, new settings): macro-F1 0.8617  fill 0.99  wash 0.93  spin 0.75  drain 0.78
```

The second machine scores 0.09 lower than the test, with every state lower. Its
weakest cycles are the 15-minute programs (0.65–0.75). The full example saw the
same drop from test to delivery, mainly on spin and fill.

**Writes:** `fit/out/delivery/scores.json`.

## Results, and they reproduce

We ran the quickstart twice from Stage 0: the second time with a fresh download
from Zenodo and `fit/out/` moved aside. Every number came out the same:

| stage | first run | fresh run |
|---|---|---|
| 1–3 | 9,826,559 rows; 1,224 / 1,760 / 1,587 windows | same |
| 4 (k 5 / k 31) | 0.8305 / 0.7980 | same |
| 5 test | 0.8617 | same, with the same confusion matrix, from its own blueprint `…-7ddzrn` |
| 7 delivery | 0.7707 | same, per cycle too |

So the pipeline and the platform are deterministic here. If your run gives
different numbers, something differs in your data or setup, and the expected
outputs above show at which stage.

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
