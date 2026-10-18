/**
 * The agent, on the Archetype AI platform: a bridge between the local broker and
 * a serving agent of the model fit/test.py promoted (Stage 5).
 *
 *   larco/sensors ──► bridge: up to N whole windows ──POST /query──► serving agent
 *                       ▲                                                  │
 *                       └──────────── larco/results ◄── one record a window ┘
 *
 * - THE BROWSER NEVER HOLDS THE API KEY. The bridge reads it from the repo's .env
 *   and is the only process that talks to the platform.
 * - A QUERY CARRIES WHOLE WINDOWS. A served agent keeps nothing between queries
 *   (Stage 8), and the model's step is its window (512 / 512), so the replayer
 *   sends each window whole and the bridge asks about 1 to 8 consecutive ones at
 *   a time (Settings → Windows per query; Stage 8 measured 8 at half the time a
 *   window, with the same answers).
 * - THE ANSWER GETS ITS WINDOW BACK. The served agent numbers rows 0..511 within
 *   the query; the bridge knows which windows it sent and puts their cycle, index
 *   and timestamps on each record.
 * - ONE SERVING WORKER, HELD WHILE THIS RUNS. Dev has 3 for everyone. On exit the
 *   agent is cancelled and deleted, and the bundle deleted; the ids sit in
 *   fit/out/app_serving_state.json until then, so `python fit/cleanup.py --yes`
 *   finds them after a hard kill. An agent the platform released (20 minutes
 *   without a query, e.g. a paused replay) is replaced on the next window.
 *
 * Env (repo .env): ATAI_API_ENDPOINT (Dev only), ATAI_API_KEY.
 * Env (app-serving/.env): WINDOWS_PER_QUERY (default 1).
 */
import mqtt from 'mqtt';
import fs from 'node:fs';
import path from 'node:path';
import { REPO, BROKER_URL, loadEnv, readJson } from './common.js';

loadEnv();
const ENDPOINT = (process.env.ATAI_API_ENDPOINT ?? '')
	.replace(/\/+$/, '')
	.replace(/\/v\d+(\.\d+)*$/, '');
const KEY = process.env.ATAI_API_KEY;
if (!ENDPOINT || !KEY) {
	console.error('bridge: set ATAI_API_ENDPOINT and ATAI_API_KEY in the repo .env');
	process.exit(1);
}
if (!ENDPOINT.includes('api.dev.')) {
	console.error(`bridge: ${ENDPOINT}: agent serving is on Dev only for now`);
	process.exit(1);
}
const STATE = path.join(REPO, 'fit', 'out', 'app_serving_state.json');
if (fs.existsSync(STATE)) {
	console.error(
		`bridge: ${path.relative(REPO, STATE)} holds an earlier bridge's agent: run python fit/cleanup.py --yes`
	);
	process.exit(1);
}
const test = readJson(path.join(REPO, 'fit', 'out', 'test_state.json'));
if (!test?.blueprint?.key) {
	console.error('bridge: no fit/out/test_state.json: run fit/test.py (Stage 5) first');
	process.exit(1);
}
if (test.endpoint && test.endpoint !== ENDPOINT) {
	console.error(`bridge: fit/out/test_state.json is from ${test.endpoint}, not ${ENDPOINT}`);
	process.exit(1);
}
const PROMOTED = test.blueprint.key;
const SERVED = `${PROMOTED}-served-records`;
const CHANNELS = readJson(path.join(REPO, 'data', 'roles', 'zscore_stats.json'))?.channels;
if (!CHANNELS) {
	console.error(
		'bridge: no data/roles/zscore_stats.json: build or unpack the role files (Stage 2)'
	);
	process.exit(1);
}
const MAX_WPQ = 8;
let wpq = Math.min(MAX_WPQ, Math.max(1, Number(process.env.WINDOWS_PER_QUERY ?? 1) || 1));

// ------------------------------------------------------------------ the platform

async function api(method, p, body) {
	const r = await fetch(`${ENDPOINT}/agents${p}`, {
		method,
		headers: {
			Authorization: `Bearer ${KEY}`,
			...(body ? { 'Content-Type': 'application/json' } : {})
		},
		body: body ? JSON.stringify(body) : undefined,
		signal: AbortSignal.timeout(120_000)
	});
	const text = await r.text();
	if (!r.ok) {
		const e = new Error(`${method} ${p} → ${r.status}: ${text.slice(0, 300)}`);
		e.status = r.status;
		throw e;
	}
	return text ? JSON.parse(text) : {};
}

/** `agents`: every agent of the bundle not yet deleted, the current one last. */
const live = {
	bundle: null,
	agent: null,
	agents: [],
	running: false,
	phase: 'starting',
	model: null,
	window: null
};
const save = () =>
	fs.writeFileSync(
		STATE,
		JSON.stringify(
			{ endpoint: ENDPOINT, bundle: live.bundle, agent: live.agent, agents: live.agents },
			null,
			1
		)
	);
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const latencies = [];

/** What the page shows about the agent (retained, so a reload sees it at once). */
function announce(phase, detail = '') {
	live.phase = phase;
	const ms = [...latencies].sort((a, b) => a - b);
	client.publish(
		'larco/agent',
		JSON.stringify({
			phase,
			detail,
			running: live.running,
			agent: live.agent,
			bundle: live.bundle,
			blueprint: SERVED,
			promoted: PROMOTED,
			model: live.model,
			window: live.window,
			wpq,
			queued: queue.length,
			medianLatencyMs: ms.length ? ms[Math.floor(ms.length / 2)] : null
		}),
		{ retain: true }
	);
}

/** The served copy fit/serve.py makes (and reuses): Stage 5's blueprint with an
 *  atai-records serving source and sink. Returns the model name it answers to. */
async function ensureServedBlueprint() {
	try {
		const doc = (await api('GET', `/blueprints/${SERVED}`)).document;
		return { model: doc.connectors.source.config.models[0], window: doc.values.window_size };
	} catch (e) {
		if (e.status !== 404) throw e;
	}
	const src = await api('GET', `/blueprints/${PROMOTED}`);
	const doc = structuredClone(src.document);
	delete doc.blueprint_id;
	const endpoint = { format: 'serving', kind: 'http', protocols: ['atai-records'] };
	doc.connectors = {
		source: {
			key: 'RecordsSource',
			config: {
				...endpoint,
				models: ['larco-osm'],
				data_columns: [{ dtype: 'f32', names: CHANNELS }],
				timestamp_column: 'timestamp'
			}
		},
		sink: { key: 'RecordsSink', config: { ...endpoint } }
	};
	doc.name = `${src.name} (served, records)`;
	await api('POST', '/blueprints', {
		blueprint_key: SERVED,
		name: doc.name,
		document: doc,
		description: `${PROMOTED} served with atai-records.`
	});
	console.log(`bridge: created served blueprint ${SERVED}`);
	return { model: 'larco-osm', window: doc.values.window_size };
}

async function startAgent() {
	if (!live.bundle) {
		// Embeddings on, for the embedding space; the classifier is unchanged.
		live.bundle = (
			await api('POST', '/bundles', {
				blueprint: SERVED,
				name: 'LARCO app-serving',
				values: { output_embeddings: true }
			})
		).id;
		save();
	}
	live.agent = (await api('POST', `/bundles/${live.bundle}/run`, { mode: 'serving' })).id;
	live.agents.push(live.agent);
	save();
	announce('starting', `agent ${live.agent}`);
	const t0 = Date.now();
	for (;;) {
		const s = (await api('GET', `/instances/${live.agent}`)).status;
		if (s === 'running') break;
		if (['failed', 'completed', 'cancelled', 'canceled'].includes(s) || Date.now() - t0 > 600_000)
			throw new Error(`agent ${live.agent} is ${s}`);
		await sleep(2000);
	}
	live.running = true;
	announce('running');
	console.log(`bridge: serving agent ${live.agent} running (bundle ${live.bundle}, ${SERVED})`);
}

/** Cancel and delete an agent; true once it is gone (404 counts). */
async function dropAgent(id) {
	await api('POST', `/instances/${id}/cancel`, {}).catch(() => {}); // a released agent is already stopped
	try {
		await api('DELETE', `/instances/${id}`);
	} catch (e) {
		if (e.status !== 404) {
			console.error(`bridge: ${e.message}`);
			return false;
		}
	}
	live.agents = live.agents.filter((a) => a !== id);
	if (live.agent === id) live.agent = null;
	save();
	return true;
}

/** Start an agent, retrying while Dev's serving workers are all taken. */
async function startWithRetry() {
	live.running = false;
	for (let wait = 15_000; !stopping; wait = Math.min(wait * 2, 120_000)) {
		try {
			await startAgent();
			return;
		} catch (e) {
			console.error(
				`bridge: could not start a serving agent (${e.message}); retrying in ${wait / 1000} s`
			);
			announce('waiting', `no serving worker free; retrying in ${wait / 1000} s`);
			if (live.agent) await dropAgent(live.agent);
			await sleep(wait);
		}
	}
}

let stopping = false;
async function shutdown(sig) {
	if (stopping) return;
	stopping = true;
	console.log(`bridge: ${sig}, cleaning up`);
	live.running = false;
	let left = 0;
	try {
		for (const id of [...live.agents]) if (!(await dropAgent(id))) left++;
		if (live.bundle && !left) {
			try {
				await api('DELETE', `/bundles/${live.bundle}`);
			} catch (e) {
				if (e.status !== 404) {
					console.error(`bridge: ${e.message}`);
					left++;
				}
			}
		}
		if (left) {
			console.error(
				`bridge: not everything was deleted; run python fit/cleanup.py --yes (ids in ${path.relative(REPO, STATE)})`
			);
		} else {
			fs.rmSync(STATE, { force: true });
			console.log(`bridge: deleted its agent(s) and bundle ${live.bundle ?? '(none)'}`);
		}
		announce('stopped');
		await new Promise((r) => client.end(false, {}, r));
	} finally {
		process.exit(left ? 1 : 0);
	}
}
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(sig, () => shutdown(sig));
// Anything unexpected still cleans up: an exit without it would hold a Dev worker.
process.on('uncaughtException', (e) => {
	console.error('bridge:', e);
	shutdown('error');
});
process.on('unhandledRejection', (e) => {
	console.error('bridge:', e);
	shutdown('error');
});

// ------------------------------------------------------------------ the windows

/** Windows waiting for a query, oldest first: {epoch, cycle, index, start_ms, finish_ms, x}. */
const queue = [];
/** The replayer's epoch: bumped on every cycle switch or restart, so windows (and
 *  answers) from before it are dropped. */
let epoch = -1;
let busy = false;

function onWindow(payload) {
	const w = JSON.parse(payload.toString());
	if (w.epoch < epoch) return;
	queue.push(w);
	drain().catch((e) => console.error(`bridge: ${e.message}`));
}

/** Up to `wpq` consecutive windows of one cycle from the head of the queue. */
function takeBatch() {
	const batch = [queue.shift()];
	while (
		batch.length < wpq &&
		queue.length &&
		queue[0].epoch === batch[0].epoch &&
		queue[0].index === batch.at(-1).index + 1
	)
		batch.push(queue.shift());
	return batch;
}

async function drain() {
	if (busy || !live.running) return;
	busy = true;
	try {
		while (queue.length && live.running && !stopping) {
			while (queue.length && queue[0].epoch < epoch) queue.shift();
			if (!queue.length) break;
			const batch = takeBatch();
			const contents = CHANNELS.map((_, c) => batch.flatMap((w) => w.x[c]));
			const t = Date.now();
			let answer;
			try {
				answer = await api('POST', `/instances/${live.agent}/query`, {
					model: live.model,
					query: '',
					events: [{ type: 'data.numeric_array', event_data: { contents } }]
				});
			} catch (e) {
				if (e.status === 409) {
					// Released after 20 minutes without a query, or cancelled elsewhere.
					console.warn(`bridge: agent ${live.agent} no longer running; replacing it`);
					announce('replacing', `agent ${live.agent} was released`);
					if (live.agent) await dropAgent(live.agent);
					queue.unshift(...batch);
					busy = false;
					await startWithRetry();
					busy = true;
					continue;
				}
				console.error(`bridge: query failed: ${e.message}`);
				for (const w of batch)
					publish(
						w,
						{ predicted_state: 'INVALID', invalid: 'true', error: e.message.slice(0, 200) },
						Date.now() - t,
						batch.length
					);
				continue;
			}
			const ms = Date.now() - t;
			latencies.push(ms);
			if (latencies.length > 200) latencies.shift();
			const records = (answer.response ?? answer).data;
			if (!Array.isArray(records) || records.length !== batch.length) {
				console.error(
					`bridge: expected ${batch.length} record(s), got ${JSON.stringify(answer).slice(0, 200)}`
				);
				for (const w of batch)
					publish(
						w,
						{ predicted_state: 'INVALID', invalid: 'true', error: 'no record' },
						ms,
						batch.length
					);
				continue;
			}
			batch.forEach((w, i) => publish(w, records[i], ms, batch.length));
			if (latencies.length % 10 === 0) announce('running');
		}
	} finally {
		busy = false;
	}
}

/** One record as the page reads it: the classifier's answer, the window it
 *  belongs to, and the embedding in the channels' order (for the embedding space). */
function publish(w, rec, ms, count) {
	if (w.epoch < epoch) return;
	const msg = {
		epoch: w.epoch,
		cycle: w.cycle,
		index: w.index,
		start_ms: w.start_ms,
		finish_ms: w.finish_ms,
		predicted_state: rec.predicted_state,
		invalid: String(rec.invalid ?? 'false'),
		latency_ms: ms,
		windows_in_query: count
	};
	for (const [k, v] of Object.entries(rec)) if (k.startsWith('p_')) msg[k] = Number(v);
	if (rec.error) msg.error = rec.error;
	const emb = CHANNELS.map((c) => rec[`embedding_${c}`]);
	if (emb.every(Array.isArray)) msg.embedding = emb.flat();
	client.publish('larco/results', JSON.stringify(msg));
}

// The will clears a retained "running" if this process dies without saying so (kill -9).
const client = mqtt.connect(BROKER_URL, {
	clientId: `larco-serving-bridge-${Date.now()}`,
	will: {
		topic: 'larco/agent',
		payload: JSON.stringify({ phase: 'stopped', running: false }),
		retain: true,
		qos: 1
	}
});

client.on('connect', () => {
	client.subscribe(['larco/sensors', 'larco/status'], { qos: 1 });
	if (live.phase === 'starting' && !live.agent) announce('starting', 'creating the agent');
	console.log(`bridge: connected to ${BROKER_URL}`);
});
client.on('message', (topic, payload) => {
	if (topic === 'larco/sensors') return onWindow(payload);
	// larco/status: a new epoch drops what is queued; the viewer's windows per query.
	try {
		const s = JSON.parse(payload.toString());
		if (Number.isInteger(s.epoch) && s.epoch > epoch) {
			epoch = s.epoch;
			while (queue.length && queue[0].epoch < epoch) queue.shift();
		}
		const want = Number(s.wpq);
		if (Number.isInteger(want) && want >= 1 && want <= MAX_WPQ && want !== wpq) {
			wpq = want;
			console.log(`bridge: ${wpq} window(s) per query`);
			announce(live.phase);
		}
	} catch {
		/* not ours to parse */
	}
});
client.on('error', (e) => console.error('bridge mqtt error:', e.message));

// ------------------------------------------------------------------------ start

try {
	Object.assign(live, await ensureServedBlueprint());
	await startWithRetry();
	drain().catch((e) => console.error(`bridge: ${e.message}`));
} catch (e) {
	console.error(`bridge: ${e.message}`);
	await shutdown('startup failure');
}
