/**
 * The washing machine: replays one becken-flt cycle from the Stage 6 delivery
 * files, a 512-row window (2.56 s at 200 Hz) at a time, as the machine's
 * accelerometers would deliver it.
 *
 *   data/roles/delivery/<file>.csv ──► larco/sensors   whole windows, z-scored, for the bridge
 *                                  └─► larco/raw       per window: level and a trace in g, for the page
 *   larco/status (retained)  where the replay is, the cycle list, speed, windows per query
 *   larco/control            the page's clicks: play, pause, restart, select, speed, wpq, reveal
 *   larco/truth              the held-back labels, only once the viewer reveals them
 *
 * - THE AGENT IS NEVER TOLD THE ANSWER. The labels (data/roles/delivery_labels/)
 *   are read alongside, but they go out only after a reveal or at the end of the
 *   cycle, on their own topic: a state the page shows before then is the agent's.
 * - STAGE 6'S ANSWER RIDES ALONG. When fit/out/delivery/<file>.csv exists (Stage
 *   6's batch output for this file), each window carries batch's state and
 *   probabilities, so the page can count served = batch the way fit/serve.py does.
 * - PACED BY THE DATA, HELD BY THE AGENT. A window is sent every 2.56 s / speed,
 *   but never more than a few ahead of the agent's answers, and not at all while
 *   the bridge has no running agent. "Max" sends as fast as the agent answers.
 * - A NEW CYCLE IS A NEW EPOCH. Selecting a cycle or restarting bumps it, and the
 *   bridge and the page drop anything from before.
 *
 * Env (app-serving/.env): CYCLE (part of a delivery file's name), SPEED (1, 2, 4,
 * 8 or max), WINDOWS_PER_QUERY.
 */
import mqtt from 'mqtt';
import fs from 'node:fs';
import path from 'node:path';
import readline from 'node:readline';
import { REPO, BROKER_URL, loadEnv, readJson } from './common.js';

loadEnv();
const ROLES = path.join(REPO, 'data', 'roles');
const manifest = readJson(path.join(ROLES, 'manifest.json'));
const zscore = readJson(path.join(ROLES, 'zscore_stats.json'));
if (!manifest || !zscore) {
	console.error(
		'replayer: no data/roles/: build or unpack the role files (Stages 0–3, or the shortcut)'
	);
	process.exit(1);
}
const CHANNELS = zscore.channels;
const PREFIX = 'becken-flt_BWM5381IX_';
const STATES = ['fill', 'wash', 'spin', 'drain'];
const SPEEDS = [1, 2, 4, 8, 'max'];
const TRACE_POINTS = 64;

/** Stage 6's delivery files: the 5 becken-flt cycles in data/roles/delivery/. */
const CYCLES = manifest.delivery.files
	.filter((f) => fs.existsSync(path.join(ROLES, f.file)))
	.map((f) => {
		const base = path.basename(f.file);
		return {
			file: f.file,
			base,
			name: base
				.replace(PREFIX, '')
				.replace(/\.csv$/, '')
				.replace(/__seg(\d+)$/, ' · seg $1'),
			rows: f.rows,
			seconds: f.seconds
		};
	})
	.sort((a, b) => a.name.localeCompare(b.name))
	.map((c, id) => ({ ...c, id }));
if (!CYCLES.length) {
	console.error('replayer: no delivery files in data/roles/delivery/');
	process.exit(1);
}

const pickSpeed = (v) => (SPEEDS.includes(v) ? v : SPEEDS.includes(Number(v)) ? Number(v) : 1);
const want = process.env.CYCLE ? CYCLES.find((c) => c.base.includes(process.env.CYCLE)) : null;
const st = {
	// a new replayer's epochs are later than any earlier one's, so a bridge that
	// outlived it doesn't drop the new windows as stale
	epoch: Date.now(),
	cycle: (want ?? CYCLES[0]).id,
	playing: true,
	ended: false,
	revealed: false,
	index: 0,
	total: 0,
	speed: pickSpeed(process.env.SPEED ?? 1),
	wpq: Math.min(8, Math.max(1, Number(process.env.WINDOWS_PER_QUERY ?? 1) || 1)),
	throttled: false,
	waiting: 'the agent',
	batch: false
};
/** The bridge's last larco/agent message: its window size and whether it is running. */
let agent = null;
/** Answers for this epoch, for flow control. */
let answered = 0;
let sent = 0;
let lastAnswer = Date.now();
/** Windows the bridge can lose (a restarted bridge, a dropped query): after this
 *  long with no answer, the replayer stops waiting for them. */
const STALL_MS = 30_000;
/** Labels of the windows sent so far this epoch, for the reveal. */
let labels = [];

const client = mqtt.connect(BROKER_URL, { clientId: `larco-replayer-${Date.now()}` });

function publishStatus() {
	const c = CYCLES[st.cycle];
	client.publish(
		'larco/status',
		JSON.stringify({
			...st,
			channels: CHANNELS,
			zscore: { mean: zscore.mean, std: zscore.std },
			windowSeconds: windowSeconds(),
			cycleName: c.name,
			cycleFile: c.base,
			cycles: CYCLES.map(({ id, name, rows }) => ({ id, name, rows })),
			// what share of the cycle each state is: ground truth, so only after a reveal
			truth: st.revealed ? c.seconds : null
		}),
		{ retain: true, qos: 1 }
	);
}
const windowSeconds = () => (agent?.window ?? 512) / 200;

/** Stage 6's batch answer for this file, by finish time in ms. */
function loadBatch(c) {
	const file = path.join(REPO, 'fit', 'out', 'delivery', c.base);
	if (!fs.existsSync(file)) return null;
	const [head, ...rows] = fs.readFileSync(file, 'utf8').trim().split(/\r?\n/);
	const cols = head.split(',');
	const at = (n) => cols.indexOf(n);
	const out = new Map();
	for (const r of rows) {
		const v = r.split(',');
		if (v[at('invalid')]?.toLowerCase() === 'true') continue;
		out.set(Math.round(Number(v[at('finish_timestamp')]) * 1000), {
			state: v[at('predicted_state')],
			p: Object.fromEntries(STATES.map((s) => [s, Number(v[at(`p_${s}`)])]))
		});
	}
	return out;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const lines = (file) => {
	const stream = fs.createReadStream(file);
	const rl = readline.createInterface({ input: stream, crlfDelay: Infinity });
	return {
		it: rl[Symbol.asyncIterator](),
		close: () => {
			rl.close();
			stream.destroy();
		}
	};
};

/** Replay one cycle until it ends or the epoch moves on. */
async function play(epoch) {
	const c = CYCLES[st.cycle];
	const W = agent.window;
	const batch = loadBatch(c);
	st.batch = Boolean(batch);
	st.total = Math.floor(c.rows / W);
	const data = lines(path.join(ROLES, c.file));
	const lab = lines(path.join(ROLES, 'delivery_labels', c.base));
	await data.it.next();
	await lab.it.next(); // the headers
	const cols = CHANNELS.length;
	let due = Date.now();
	try {
		for (let index = 0; index < st.total; index++) {
			const ts = new Array(W);
			const x = CHANNELS.map(() => new Array(W));
			let label = null;
			for (let r = 0; r < W; r++) {
				const [d, l] = await Promise.all([data.it.next(), lab.it.next()]);
				if (d.done) return;
				const v = d.value.split(',');
				ts[r] = Math.round(Number(v[0]) * 1000);
				for (let k = 0; k < cols; k++) x[k][r] = Number(v[k + 1]);
				if (r === W - 1 && !l.done) label = l.value.split(',')[1]; // last_record, as Stages 5–7 score
			}
			// Hold: paused, no running agent, or too far ahead of its answers.
			for (;;) {
				if (st.epoch !== epoch) return;
				const ahead = sent - answered;
				const limit = Math.max(4, 2 * st.wpq);
				st.waiting = !agent?.running
					? 'the agent'
					: !st.playing
						? null
						: ahead >= limit
							? 'answers'
							: null;
				if (st.playing && agent?.running && ahead < limit) break;
				if (ahead >= limit && agent?.running && Date.now() - lastAnswer > STALL_MS) {
					console.warn(
						`replayer: no answer for ${STALL_MS / 1000} s; not waiting for ${ahead} window(s)`
					);
					answered = sent;
				}
				if (Date.now() > due) due = Date.now();
				await sleep(50);
			}
			if (st.speed !== 'max') {
				const wait = due - Date.now();
				if (wait > 0) await sleep(wait);
				due = Math.max(due, Date.now() - 1000) + (windowSeconds() * 1000) / st.speed;
			}
			if (st.epoch !== epoch) return;
			st.throttled = st.speed !== 'max' && sent - answered >= Math.max(4, 2 * st.wpq) - 1;

			const start_ms = ts[0];
			const finish_ms = ts[W - 1];
			client.publish(
				'larco/sensors',
				JSON.stringify({ epoch, cycle: c.id, index, start_ms, finish_ms, x }),
				{ qos: 1 }
			);
			sent++;
			labels.push(label);
			// The page's copy, in g: the window's vibration level per channel (RMS
			// about its own mean) and a decimated trace.
			const rms = [];
			const trace = [];
			for (let k = 0; k < cols; k++) {
				const sd = zscore.std[k];
				const mu = zscore.mean[k];
				let m = 0;
				for (const z of x[k]) m += z;
				m /= W;
				let e = 0;
				for (const z of x[k]) e += (z - m) ** 2;
				rms.push(Math.sqrt(e / W) * sd);
				const t = [];
				for (let i = 0; i < W; i += W / TRACE_POINTS) t.push(+(x[k][i] * sd + mu).toFixed(4));
				trace.push(t);
			}
			const b = batch?.get(finish_ms) ?? null;
			client.publish(
				'larco/raw',
				JSON.stringify({
					epoch,
					cycle: c.id,
					index,
					start_ms,
					finish_ms,
					rms,
					trace,
					batch: b,
					label: st.revealed ? label : undefined
				})
			);
			st.index = index + 1;
		}
		st.ended = true;
		st.playing = false;
		reveal();
	} finally {
		data.close();
		lab.close();
		publishStatus();
	}
}

function reveal() {
	if (!st.revealed) {
		st.revealed = true;
		client.publish('larco/truth', JSON.stringify({ epoch: st.epoch, cycle: st.cycle, labels }));
	}
	publishStatus();
}

/** Start the selected cycle from its first window, as a new epoch. */
function restart() {
	st.epoch++;
	st.index = 0;
	st.ended = false;
	st.revealed = false;
	st.throttled = false;
	sent = answered = 0;
	labels = [];
	publishStatus();
	run();
}

let running = null;
async function run() {
	const epoch = st.epoch;
	await running; // the previous cycle's reader closes first
	if (epoch !== st.epoch || !agent?.window) return;
	running = play(epoch).catch((e) => console.error(`replayer: ${e.message}`));
	await running;
}

client.on('connect', () => {
	client.subscribe(['larco/control', 'larco/agent', 'larco/results'], { qos: 1 });
	console.log(`replayer: ${CYCLES.length} delivery file(s); starting on ${CYCLES[st.cycle].name}`);
	publishStatus();
});
client.on('message', (topic, payload) => {
	let m;
	try {
		m = JSON.parse(payload.toString());
	} catch {
		return;
	}
	if (topic === 'larco/results') {
		if (m.epoch === st.epoch) answered++;
		lastAnswer = Date.now();
		return;
	}
	if (topic === 'larco/agent') {
		const first = !agent?.window && m.window;
		// A new agent (the bridge restarted, or replaced a released one) answers
		// nothing it wasn't sent: start the count again.
		if (m.running && (!agent?.running || m.agent !== agent.agent)) {
			answered = sent;
			lastAnswer = Date.now();
		}
		agent = m;
		if (first) restart();
		return;
	}
	const { action, value } = m;
	if (action === 'play') {
		if (st.ended) return restart();
		st.playing = true;
	} else if (action === 'pause') st.playing = false;
	else if (action === 'restart') {
		st.playing = true;
		return restart();
	} else if (action === 'select') {
		const id = Number(value);
		if (!CYCLES[id] || id === st.cycle) return;
		st.cycle = id;
		st.playing = true;
		return restart();
	} else if (action === 'speed') st.speed = pickSpeed(value);
	else if (action === 'wpq') st.wpq = Math.min(8, Math.max(1, Number(value) || 1));
	else if (action === 'reveal') return reveal();
	publishStatus();
});
client.on('error', (e) => console.error('replayer mqtt error:', e.message));

// The page reads the status while the replay runs, for the position.
setInterval(() => st.playing && publishStatus(), 1000);
for (const sig of ['SIGINT', 'SIGTERM', 'SIGHUP'])
	process.on(sig, () => client.end(false, {}, () => process.exit(0)));
