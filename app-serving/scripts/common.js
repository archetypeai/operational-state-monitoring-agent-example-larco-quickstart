/**
 * What the bridge and the replayer share: where the repo is, the broker, and the
 * two .env files.
 *
 * Ports are this app's slot in the allocation the canonical-agent apps follow
 * (mosquitto/mosquitto.conf has the table), fixed so several apps can run side
 * by side: MQTT 1892, WebSockets 9010, web 5182.
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

export const APP = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
export const REPO = path.resolve(APP, '..');
export const BROKER_URL = process.env.BROKER ?? 'mqtt://127.0.0.1:1892';

function parse(file) {
	if (!fs.existsSync(file)) return {};
	const out = {};
	for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
		const m = /^\s*([A-Z0-9_]+)\s*=\s*(.*?)\s*(#.*)?$/.exec(line);
		if (m && !line.trimStart().startsWith('#')) out[m[1]] = m[2].replace(/^["']|["']$/g, '');
	}
	return out;
}

/** The repo .env (the key and endpoint, as the Python stages read them), then
 *  app-serving/.env for this app's own settings. An ATAI_* line in
 *  app-serving/.env is ignored: there is one key, in one place. A variable
 *  already exported wins over both. */
export function loadEnv() {
	const repo = parse(path.join(REPO, '.env'));
	const app = Object.fromEntries(
		Object.entries(parse(path.join(APP, '.env'))).filter(([k]) => !k.startsWith('ATAI_'))
	);
	for (const [k, v] of Object.entries({ ...app, ...repo })) if (!process.env[k]) process.env[k] = v;
}

export function readJson(file) {
	return fs.existsSync(file) ? JSON.parse(fs.readFileSync(file, 'utf8')) : null;
}
