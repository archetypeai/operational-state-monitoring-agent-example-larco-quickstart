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
 * - ONE SERVING WORKER, HELD WHILE THIS RUNS. The blueprint is packaged, the
 *   package deployed as `serving` (the agent placed on a worker and loaded), and a
 *   session opened on the deployment; queries go to the session. On exit the
 *   session is cancelled, the deployment stopped, and all three deleted; the ids sit
 *   in fit/out/app_serving_state.json until then, so `python fit/cleanup.py --yes`
 *   finds them after a hard kill. A session that stops taking queries (cancelled
 *   elsewhere, or its deployment ended) is replaced on the next window, with a new
 *   deployment if the old one is gone.
 * - EMBEDDINGS NEED THEIR OWN BLUEPRINT. A package carries no values, so it can't
 *   turn `output_embeddings` on: the bridge serves fit/library_embeddings.py's
 *   embeddings blueprint (Stage 5's configuration with embeddings on) when it was
 *   made from the current Stage 5 blueprint, else Stage 5's, without the trail.
 *
 * Env (repo .env): ATAI_API_ENDPOINT, ATAI_API_KEY.
 * Env (app-serving/.env): WINDOWS_PER_QUERY (default 1).
 */
import mqtt from 'mqtt';
import fs from 'node:fs';
import path from 'node:path';
import { REPO, BROKER_URL, loadEnv, readJson } from './common.js';

loadEnv();
// The deployment root, with any /api or /vX.Y suffix removed, then /api appended:
// every platform surface lives under it, and one .env works whichever form you have.
// Keeping /api on ENDPOINT also matches what fit/ writes into the state files.
const ROOT = (process.env.ATAI_API_ENDPOINT ?? '')
	.replace(/\/+$/, '')
	.replace(/\/api$/, '')
	.replace(/\/v\d+(\.\d+)*$/, '');
const ENDPOINT = ROOT ? `${ROOT}/api` : '';
const KEY = process.env.ATAI_API_KEY;
if (!ENDPOINT || !KEY) {
	console.error('bridge: set ATAI_API_ENDPOINT and ATAI_API_KEY in the repo .env');
	process.exit(1);
}
const STATE = path.join(REPO, 'fit', 'out', 'app_serving_state.json');
if (fs.existsSync(STATE)) {
	console.error(
		`bridge: ${path.relative(REPO, STATE)} holds an earlier bridge's deployment: run python fit/cleanup.py --yes`
	);
	process.exit(1);
}
const test = readJson(path.join(REPO, 'fit', 'out', 'test_state.json'));
if (!test?.blueprint?.key) {
	console.error('bridge: no fit/out/test_state.json: run fit/test.py (Stage 5) first');
	process.exit(1);
}
/** The deployment an endpoint addresses, whatever hostname it arrives under (as fit/atai.py). */
const deployment = (e) =>
	e
		.trim()
		.replace(/^[a-z]+:\/\//, '')
		.split('/')[0]
		.split(':')[0]
		.replace(/^(api|platform)\./, '')
		.toLowerCase();
if (test.endpoint && deployment(test.endpoint) !== deployment(ENDPOINT)) {
	console.error(
		`bridge: fit/out/test_state.json is from ${test.endpoint}, a different deployment from ${ENDPOINT}`
	);
	process.exit(1);
}
/** fit/library_embeddings.py's embeddings blueprint, if it was made from this Stage 5 blueprint here. */
const library = readJson(path.join(REPO, 'fit', 'out', 'library_state.json'));
const served =
	library?.embeddings_blueprint &&
	library.blueprint === test.blueprint.key &&
	library.endpoint &&
	deployment(library.endpoint) === deployment(ENDPOINT)
		? library.embeddings_blueprint
		: test.blueprint;
if (served === test.blueprint)
	console.warn(
		'bridge: no embeddings blueprint for this Stage 5 blueprint (python fit/library_embeddings.py makes one): serving it without embeddings'
	);
const PROMOTED = served.key;
const PROMOTED_ID = served.id;
const MODEL = 'larco-osm'; // the name a query gives in `model`, as fit/serve.py (not checked)
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

/** `sessions`: every session not yet deleted, the current one last. `agent` is the current
 *  session, under the name the page reads. */
const live = {
	package: null,
	deployment: null,
	agent: null,
	sessions: [],
	running: false,
	phase: 'starting',
	model: null,
	window: null
};
const save = () =>
	fs.writeFileSync(
		STATE,
		JSON.stringify(
			{
				endpoint: ENDPOINT,
				package: live.package,
				deployment: live.deployment,
				session: live.agent,
				sessions: live.sessions
			},
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
			deployment: live.deployment,
			package: live.package,
			blueprint: PROMOTED,
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

/** The served blueprint's window, and the model name a query gives. Served as it is: the
 *  platform supplies the serving connectors for a serving deployment. By id: a key
 *  registered in a project resolves only with ?project_id=. */
async function promotedModel() {
	const doc = (await api('GET', `/blueprints/${PROMOTED_ID}`)).document;
	return { model: MODEL, window: doc.values.window_size };
}

/** Poll `p` while its status is `waiting`; throw unless it then reaches `want`. */
async function until(p, want, waiting) {
	const t0 = Date.now();
	for (;;) {
		const o = await api('GET', p);
		if (o.status === want) return o;
		if (o.status !== waiting || Date.now() - t0 > 600_000)
			throw new Error(`${p} is ${o.status}${o.error ? `: ${o.error}` : ''}`);
		await sleep(2000);
	}
}

async function startAgent() {
	if (!live.package) {
		// The blueprint as it is: its columns (a served query has no CSV header) and, for the
		// embeddings blueprint, output_embeddings were pinned when it was optimized.
		live.package = (
			await api('POST', '/packages', {
				blueprint_ids: [PROMOTED_ID],
				target: { kind: 'platform' },
				name: 'LARCO app-serving'
			})
		).id;
		save();
		await until(`/packages/${live.package}`, 'ready', 'building');
	}
	if (!live.deployment) {
		live.deployment = (await api('POST', '/deployments', { package_id: live.package, kind: 'serving' })).id;
		save();
		announce('starting', `deployment ${live.deployment}`);
		await until(`/deployments/${live.deployment}`, 'ready', 'deploying');
	}
	live.agent = (await api('POST', '/sessions', { deployment_id: live.deployment })).id;
	live.sessions.push(live.agent);
	save();
	announce('starting', `session ${live.agent}`);
	await until(`/sessions/${live.agent}`, 'running', 'pending');
	live.running = true;
	announce('running');
	console.log(
		`bridge: session ${live.agent} running (deployment ${live.deployment}, package ${live.package}, ${PROMOTED})`
	);
}

/** Delete `p`, after `prep` (cancel or stop) where that still applies; true once it is gone (404 counts). */
async function drop(p, prep) {
	if (prep) await api('POST', `${p}/${prep}`, {}).catch(() => {}); // already ended: nothing to do
	try {
		await api('DELETE', p);
		return true;
	} catch (e) {
		if (e.status === 404) return true;
		console.error(`bridge: ${e.message}`);
		return false;
	}
}

async function dropSession(id) {
	if (!(await drop(`/sessions/${id}`, 'cancel'))) return false;
	live.sessions = live.sessions.filter((s) => s !== id);
	if (live.agent === id) live.agent = null;
	save();
	return true;
}

async function dropDeployment() {
	if (!live.deployment) return true;
	if (!(await drop(`/deployments/${live.deployment}`, 'stop'))) return false;
	live.deployment = null;
	save();
	return true;
}

/** True unless the current deployment is known to be gone or ended. */
async function deploymentReady() {
	if (!live.deployment) return false;
	const d = await api('GET', `/deployments/${live.deployment}`).catch(() => ({}));
	return d.status === 'ready';
}

/** Open a session (deploying first if needed), retrying while the serving workers are all taken. */
async function startWithRetry() {
	live.running = false;
	for (let wait = 15_000; !stopping; wait = Math.min(wait * 2, 120_000)) {
		try {
			await startAgent();
			return;
		} catch (e) {
			console.error(`bridge: could not start serving (${e.message}); retrying in ${wait / 1000} s`);
			announce('waiting', `no serving worker free; retrying in ${wait / 1000} s`);
			if (live.agent) await dropSession(live.agent);
			if (live.deployment && !(await deploymentReady())) await dropDeployment();
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
		for (const id of [...live.sessions]) if (!(await dropSession(id))) left++;
		if (!left && !(await dropDeployment())) left++;
		if (!left && live.package && !(await drop(`/packages/${live.package}`))) left++;
		if (left) {
			console.error(
				`bridge: not everything was deleted; run python fit/cleanup.py --yes (ids in ${path.relative(REPO, STATE)})`
			);
		} else {
			fs.rmSync(STATE, { force: true });
			console.log(`bridge: deleted its session(s), deployment and package ${live.package ?? '(none)'}`);
		}
		announce('stopped');
		await new Promise((r) => client.end(false, {}, r));
	} finally {
		process.exit(left ? 1 : 0);
	}
}
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP']) process.on(sig, () => shutdown(sig));
// Anything unexpected still cleans up: an exit without it would hold a serving worker.
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
				answer = await api('POST', `/sessions/${live.agent}/query`, {
					model: live.model,
					query: '',
					events: [{ type: 'data.numeric_array', event_data: { contents } }]
				});
			} catch (e) {
				if (e.status === 409) {
					// The session was cancelled elsewhere, or its deployment ended.
					console.warn(`bridge: session ${live.agent} no longer taking queries; replacing it`);
					announce('replacing', `session ${live.agent} ended`);
					if (live.agent) await dropSession(live.agent);
					if (!(await deploymentReady())) await dropDeployment();
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
	Object.assign(live, await promotedModel());
	await startWithRetry();
	drain().catch((e) => console.error(`bridge: ${e.message}`));
} catch (e) {
	console.error(`bridge: ${e.message}`);
	await shutdown('startup failure');
}
