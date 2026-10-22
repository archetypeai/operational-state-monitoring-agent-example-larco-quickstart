/**
 * The one MQTT-backed store the page reads from. The browser subscribes to
 * mosquitto's WebSocket listener directly; there is no backend.
 *
 *   larco/raw       replayer -> a window's vibration level and trace in g, and Stage 6's answer for it
 *   larco/status    replayer -> where the replay is, the cycle list, speed, windows per query (retained)
 *   larco/truth     replayer -> the held-back labels, after a reveal
 *   larco/agent     bridge   -> the serving agent: starting, running, replacing (retained)
 *   larco/results   bridge   -> the served agent's answer for one window
 *
 * What the agent says comes from larco/results only. The labels arrive on their
 * own topic after a reveal, and never colour the agent's answer.
 */
import mqtt from 'mqtt';
import { STATES } from '$lib/states.js';

const WS_PORT = '9010';
/** Windows of level history kept per channel for the sparklines (~6 min of data). */
const MAX_HISTORY = 150;
/** Points on the embedding trail: the last couple of minutes of the cycle. */
const MAX_TRAIL = 60;
const RATE_WINDOW_MS = 5000;
export const TOPICS = [
	'larco/sensors',
	'larco/raw',
	'larco/status',
	'larco/control',
	'larco/agent',
	'larco/results',
	'larco/truth'
];

function emptyCycle() {
	return {
		/** One entry per window of the cycle, by index.
		 *  @type {{index: number, served?: {state: string, p: Record<string, number>, invalid: boolean},
		 *          batch?: {state: string, p: Record<string, number>} | null, label?: string | null}[]} */
		windows: [],
		/** Latest window's level per channel (g RMS) and trace. @type {number[]} */
		rms: [],
		/** @type {number[][]} */
		trace: [],
		/** @type {number[][]} level per channel, oldest first */
		history: [],
		/** @type {{x: number, y: number, state: string}[]} */
		trail: [],
		/** State changes the agent called: one row per change, newest last.
		 *  @type {{index: number, from: string | null, to: string, p: number}[]} */
		changes: [],
		served: 0,
		invalid: 0,
		/** windows with a batch answer to compare, and how many agree */
		compared: 0,
		sameState: 0,
		sameP: 0,
		/** @type {number[]} */
		latency: []
	};
}

class LarcoStore {
	connected = $state(false);
	/** @type {any} */
	status = $state(null);
	/** @type {any} */
	agent = $state(null);
	cycle = $state(emptyCycle());
	rate = $state(0);
	/** @type {Record<string, number>} */
	flows = $state(Object.fromEntries(TOPICS.map((t) => [t, 0])));
	/** @type {{manifest: any, ref: Float32Array, state: Float32Array, mean: Float32Array, pc0: Float32Array, pc1: Float32Array} | null} */
	view = $state.raw(null);
	viewMissing = $state(false);

	/** @type {import('mqtt').MqttClient | null} */
	#client = null;
	#epoch = -1;
	/** @type {Record<string, number[]>} */
	#arrivals = Object.fromEntries(TOPICS.map((t) => [t, []]));
	/** @type {number[]} */
	#answers = [];
	/** @type {ReturnType<typeof setInterval> | null} */
	#timer = null;

	connect() {
		if (this.#client) return;
		const url =
			typeof location === 'undefined'
				? `ws://127.0.0.1:${WS_PORT}`
				: `ws://${location.hostname}:${WS_PORT}`;
		this.#client = mqtt.connect(url, {
			clientId: `larco-app-${Math.random().toString(16).slice(2)}`
		});
		this.#client.on('connect', () => {
			this.connected = true;
			this.#client?.subscribe(TOPICS, { qos: 0 });
		});
		this.#client.on('close', () => (this.connected = false));
		this.#client.on('error', () => (this.connected = false));
		this.#client.on('message', (topic, payload) => this.#onMessage(topic, payload));
		this.#timer = setInterval(() => {
			const now = Date.now();
			/** @type {Record<string, number>} */
			const flows = {};
			for (const t of TOPICS) {
				this.#arrivals[t] = this.#arrivals[t].filter((x) => now - x < RATE_WINDOW_MS);
				flows[t] = this.#arrivals[t].length / (RATE_WINDOW_MS / 1000);
			}
			this.flows = flows;
			this.#answers = this.#answers.filter((x) => now - x < RATE_WINDOW_MS);
			this.rate = this.#answers.length / (RATE_WINDOW_MS / 1000);
		}, 1000);
		this.loadView();
	}

	disconnect() {
		if (this.#timer) clearInterval(this.#timer);
		this.#client?.end(true);
		this.#client = null;
	}

	/** PCA 1-2 of the library, written by fit/library_embeddings.py. */
	async loadView() {
		try {
			const [manifest, buf] = await Promise.all([
				fetch('/library-view.json').then((r) => (r.ok ? r.json() : Promise.reject())),
				fetch('/library-view.bin').then((r) => (r.ok ? r.arrayBuffer() : Promise.reject()))
			]);
			const take = (/** @type {string} */ k) => {
				const l = manifest.layout[k];
				return new Float32Array(
					buf,
					l.offset,
					l.shape.reduce((/** @type {number} */ a, /** @type {number} */ b) => a * b, 1)
				);
			};
			this.view = {
				manifest,
				ref: take('ref'),
				state: take('state'),
				mean: take('mean'),
				pc0: take('pc0'),
				pc1: take('pc1')
			};
		} catch {
			this.viewMissing = true;
		}
	}

	/** A window's embedding on the library's two axes. @param {number[]} e */
	#project(e) {
		const v = this.view;
		if (!v || !Array.isArray(e) || e.length !== v.mean.length) return null;
		let x = 0;
		let y = 0;
		for (let i = 0; i < e.length; i++) {
			const d = e[i] - v.mean[i];
			x += v.pc0[i] * d;
			y += v.pc1[i] * d;
		}
		return { x, y };
	}

	/** @param {number} index */
	#window(index) {
		const w = this.cycle.windows;
		while (w.length <= index) w.push({ index: w.length });
		return w[index];
	}

	/** @param {string} topic @param {Uint8Array} payload */
	#onMessage(topic, payload) {
		if (topic in this.#arrivals) this.#arrivals[topic].push(Date.now());
		if (topic === 'larco/sensors' || topic === 'larco/control') return; // counted, not parsed
		let m;
		try {
			m = JSON.parse(payload.toString());
		} catch {
			return;
		}
		if (topic === 'larco/status') {
			this.status = m;
			if (m.epoch !== this.#epoch) {
				this.#epoch = m.epoch;
				this.cycle = emptyCycle();
			}
			return;
		}
		if (topic === 'larco/agent') {
			this.agent = m;
			return;
		}
		if (m.epoch !== this.#epoch) return;
		const c = this.cycle;
		if (topic === 'larco/truth') {
			m.labels.forEach(
				(/** @type {string | null} */ l, /** @type {number} */ i) => (this.#window(i).label = l)
			);
			return;
		}
		if (topic === 'larco/raw') {
			const w = this.#window(m.index);
			w.batch = m.batch;
			if (m.label !== undefined) w.label = m.label;
			c.rms = m.rms;
			c.trace = m.trace;
			m.rms.forEach((/** @type {number} */ v, /** @type {number} */ k) => {
				if (!c.history[k]) c.history[k] = [];
				c.history[k].push(v);
				if (c.history[k].length > MAX_HISTORY) c.history[k].shift();
			});
			this.#compare(w);
			return;
		}
		// larco/results: the agent's answer
		this.#answers.push(Date.now());
		const w = this.#window(m.index);
		const invalid = m.invalid === 'true' || m.predicted_state === 'INVALID';
		const p = Object.fromEntries(STATES.map((s) => [s, Number(m[`p_${s}`] ?? 0)]));
		w.served = { state: m.predicted_state, p, invalid };
		c.latency.push(m.latency_ms / (m.windows_in_query || 1));
		if (c.latency.length > 200) c.latency.shift();
		if (invalid) {
			c.invalid++;
			return;
		}
		c.served++;
		const prev = c.changes.at(-1)?.to ?? null;
		if (m.predicted_state !== prev)
			c.changes.push({
				index: m.index,
				from: prev,
				to: m.predicted_state,
				p: p[m.predicted_state] ?? 0
			});
		const xy = m.embedding ? this.#project(m.embedding) : null;
		if (xy) {
			c.trail.push({ ...xy, state: m.predicted_state });
			if (c.trail.length > MAX_TRAIL) c.trail.shift();
		}
		this.#compare(w);
	}

	/** Served against Stage 6's batch answer for the same window, once both are in.
	 *  @param {any} w */
	#compare(w) {
		if (w.compared || !w.served || w.served.invalid || !w.batch) return;
		w.compared = true;
		const c = this.cycle;
		c.compared++;
		if (w.served.state === w.batch.state) c.sameState++;
		if (STATES.every((s) => Math.abs(w.served.p[s] - w.batch.p[s]) < 1e-4)) c.sameP++;
	}

	/** @param {string} action @param {unknown} [value] */
	#publish(action, value) {
		this.#client?.publish('larco/control', JSON.stringify({ action, value }));
	}
	play() {
		this.#publish('play');
	}
	pause() {
		this.#publish('pause');
	}
	restart() {
		this.#publish('restart');
	}
	reveal() {
		this.#publish('reveal');
	}
	/** @param {number} id */
	select(id) {
		this.#publish('select', id);
	}
	/** @param {number | 'max'} v */
	setSpeed(v) {
		this.#publish('speed', v);
	}
	/** @param {number} n */
	setWpq(n) {
		this.#publish('wpq', n);
	}
}

export const larco = new LarcoStore();

/** Macro-F1 over the four states, platform-style (a state with no windows and no
 *  calls scores 0), for windows with both a label and a valid answer.
 *  @param {any[]} windows @param {'served' | 'batch'} who */
export function macroF1(windows, who) {
	const pairs = windows
		.filter((w) => w.label && w[who] && !w[who].invalid)
		.map((w) => [w.label, w[who].state]);
	if (!pairs.length) return null;
	const f1 = Object.fromEntries(
		STATES.map((s) => {
			const tp = pairs.filter(([y, p]) => y === s && p === s).length;
			const fp = pairs.filter(([y, p]) => y !== s && p === s).length;
			const fn = pairs.filter(([y, p]) => y === s && p !== s).length;
			return [s, tp ? (2 * tp) / (2 * tp + fp + fn) : 0];
		})
	);
	return { macro: STATES.reduce((a, s) => a + f1[s], 0) / STATES.length, f1, n: pairs.length };
}
