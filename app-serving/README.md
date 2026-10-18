# LARCO Monitor: the Stage 5 model, served, on a washing machine

A browser workbench that replays one of the 5 becken-flt cycles Stage 6 delivers
(the faulty second unit, which the model never saw a cycle of), at 200 Hz, and shows the state a **serving
agent** on the Archetype AI platform calls for each 2.56 s window: fill, wash,
spin or drain. It is Stage 8 (`fit/serve.py`) made visible: the same served
blueprint, the same queries, and each answer checked against Stage 6's batch
output for the same window, as it arrives.

> **Dev only.** Agent serving is on `https://api.dev.u1.archetypeai.app`, with 3
> serving workers shared by everyone. The app holds one while it runs and gives it
> back when it stops ([Stop and clean up](#stop-and-clean-up)).

- **Input:** the cycle's nine accelerometer channels (back, side and top, x/y/z):
  each window's trace, its vibration level in g, and the level over the last
  minutes. Any of the 5 delivery cycles can be picked.
- **Output:** the machine drawn in the called state (water in, the drum rocking,
  spinning, water out), the four probabilities, a timeline of the whole cycle
  (the agent's calls; after a reveal, the held-back labels under them), how many
  windows match Stage 6's batch output, the embedding space and a log of state
  changes.
- **The labels stay hidden** until you reveal them in Settings, or the cycle ends.
  Then the timeline gets a Truth row and the panel a macro-F1, served and batch.

## Architecture

```
  data/roles/delivery/<cycle>.csv          fit/out/delivery/<cycle>.csv (Stage 6)
  data/roles/delivery_labels/<cycle>.csv           │
                 │                                 │
                 ▼                                 ▼
        ┌──────────────────┐  larco/sensors   ┌───────────┐  larco/sensors  ┌────────────────┐  POST /query  ┌───────────────┐
        │ replayer (node)  │ ───────────────► │ mosquitto │ ──────────────► │ serving bridge │ ────────────► │ serving agent │
        │ a 512-row window │ ◄─────────────── │ tcp :1892 │ ◄────────────── │ (node)         │ ◄──────────── │ platform, Dev │
        │ every 2.56 s     │  larco/control   │ ws  :9010 │  larco/results  │ holds the key  │               └───────────────┘
        └──────────────────┘  larco/results   └───────────┘  larco/agent    └────────────────┘
                  │ larco/raw · status · truth      ▲   │
                  └─────────────────────────────────┘   │ WebSockets
                                                        ▼
                                              web app (this page, :5182)
```

- **The replayer** (`scripts/replayer.js`) reads one delivery file a window at a
  time: 512 rows, z-scored as the model was fitted, to `larco/sensors`. For the page
  it sends each window's level and a decimated trace in g, and Stage 6's batch answer
  for the same window when `fit/out/delivery/` has the file. It sends a window every
  2.56 s divided by the speed, but never more than a few ahead of the agent's
  answers, and none while the bridge has no running agent.
- **The serving bridge** (`scripts/serving-bridge.js`) is the only process that
  talks to the platform, with the key from the repo's `.env`; the browser never
  sees it. At start it finds (or makes) Stage 8's served blueprint,
  `<promoted>-served-records`, creates a bundle of it with `output_embeddings: true`,
  and runs it in serving mode. Each query carries 1 to 8 whole consecutive windows
  (Settings → Windows per query), and each record goes back to `larco/results` with
  its window's cycle, index and timestamps (the served agent numbers rows within the
  query). `larco/agent` (retained) says what it is doing: starting, running,
  waiting for a free worker, replacing a released agent, stopped.
- **The page** subscribes to the broker over WebSockets; there is no backend.

**The served agent is the batch model.** Stage 8 measured all 3,178 states
identical to Stage 6's, and every probability but, on one rerun, 2 windows a
neighbour's vote off (k 5: a fifth). Settings →
This cycle counts the same thing live.

## The embedding space

The Embedding tab draws the library the model compares each window with: its
800 windows (200 a state, window 512, step 512) in PCA 1-2 of their Omega
embeddings, one density per state, with the last minute or two of the cycle as a
trail, each point in the state the agent called. The model is a registry artifact
with no download route, so the library's embeddings are recomputed once on the
platform:

```sh
python fit/library_embeddings.py      # 4 batch runs, one per library file, then the view
```

It writes `app-serving/static/library-view.{json,bin}` (about 90 KB), which the
page loads at start. Without it the tab says so and the rest of the app works.
The runs and their bundle go in `fit/out/library_state.json`, so a rerun resumes
and `python fit/cleanup.py --yes` deletes them.

It is a cross-section, not the model: the kNN compares all 6,912 dimensions
(cosine, k 5), and two are drawn, so a window can sit in one state's field here and still
be called another.

## Run it

Needs Stages 4b–6 run on Dev (`fit/out/test_state.json` and, for the batch
comparison, `fit/out/delivery/`), the role files in `data/roles/`, Node 20+ and
mosquitto (`brew install mosquitto`).

```sh
cd app-serving
npm install
npm run dev                  # broker, bridge, replayer and the page: http://localhost:5182
```

The agent takes a minute or so to start; the badge in the menubar turns to
`platform · serving` once it runs. The replay starts on its own.

- **Cycle:** the selector in the Input panel. `CYCLE=warm_sport_40_2` in
  `app-serving/.env` starts on another one.
- **Speed** (Settings): 1×, 2×, 4× or 8× real time, or Max, as fast as the agent
  answers. Stage 8 measured about 0.6 s a window at 1 a query and 0.38 s at 4
  (without embeddings), so about 4× and 7× real time; above that the replay waits
  for the agent.
- **Windows per query** (Settings): 1 is live, as the machine's controller would
  ask every 2.56 s; more wait for each other but cost less each.

## Stop and clean up

**Stop it once:** Ctrl-C in the `npm run dev` terminal, or `npm run stop` from
another. The bridge cancels and deletes its agent and deletes its bundle, which
takes a few platform calls; `npm run stop` waits for it and checks the ports are
free.

**Check nothing is left:**

```sh
ls ../fit/out/app_serving_state.json      # gone once the bridge cleaned up
```

If it is still there (a `kill -9`, a closed laptop), the bridge couldn't finish:
`python fit/cleanup.py --yes` from the repo deletes what it lists. A serving agent
left running holds one of Dev's 3 workers until it is deleted, or released after
20 minutes without a query. The bridge refuses to start while the file is there.

## Ports

|          | MQTT TCP | MQTT WS | Web  |
| -------- | -------- | ------- | ---- |
| this app | 1892     | 9010    | 5182 |

The slot after the RED and TEP serving apps (`mosquitto/mosquitto.conf` has the
table), so they can run side by side.

## Layout

| path                                                     | what it is                                                                              |
| -------------------------------------------------------- | --------------------------------------------------------------------------------------- |
| `scripts/serving-bridge.js`                              | the bridge: the served blueprint, the serving agent, queries, release and cleanup       |
| `scripts/replayer.js`                                    | the replay: windows, pace, the labels and Stage 6's answers                             |
| `scripts/common.js`                                      | the repo path, the broker, the two `.env` files                                         |
| `scripts/run-broker.sh`, `scripts/stop.sh`, `mosquitto/` | the broker; stop everything and check the ports                                         |
| `src/lib/api/larco.svelte.js`                            | the page's MQTT store                                                                   |
| `src/lib/states.js`                                      | the four states' names and colours, the sensors                                         |
| `src/lib/components/ui/custom/`                          | the panels: machine, probabilities, timeline, embedding space, log, settings, data flow |
| `src/lib/components/ui/patterns/pane/`                   | the workbench pane                                                                      |
| `CLAUDE.md`, `ds-manifest.json`, `components.json`       | the design system's agent config and component catalogue (`@archetypeai/ds-cli`)        |

Built on the Archetype AI design system (`@archetypeai/ds-ui-svelte-console` and
`-labs` 0.19), with Geist in place of the licensed brand faces. Two deliberate
departures from the installed `CLAUDE.md`: the API key stays with the bridge rather
than being pasted into the page, so the browser never holds it; and the theme's own
`@import 'tailwindcss'` is the only Tailwind entry (two drop the DS base typography
in dev).

## Licence and data

The code is under the repo's [Apache License 2.0](../LICENSE). The LARCO data is
under the dataset's own licence: see the repo README's
[Data attribution](../README.md#data-attribution).
