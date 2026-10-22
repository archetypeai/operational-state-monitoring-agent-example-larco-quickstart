/**
 * The four states and the nine channels: the one place a state turns into a
 * label and a colour, and a channel into a sensor and an axis.
 */

export const STATES = ['fill', 'wash', 'spin', 'drain'];

/** @type {Record<string, {label: string, color: string, what: string}>} */
export const STATE_META = {
	fill: { label: 'Fill', color: 'var(--state-fill)', what: 'water entering; the motor is off' },
	wash: {
		label: 'Wash',
		color: 'var(--state-wash)',
		what: 'drum tumbling back and forth, with pauses'
	},
	spin: {
		label: 'Spin',
		color: 'var(--state-spin)',
		what: 'steady high-speed rotation to extract water'
	},
	drain: {
		label: 'Drain',
		color: 'var(--state-drain)',
		what: 'the pump emptying the drum, without spin'
	}
};

export const stateColor = (/** @type {string | null | undefined} */ s) =>
	(s && STATE_META[s]?.color) || 'var(--color-muted-foreground)';
export const stateLabel = (/** @type {string | null | undefined} */ s) =>
	(s && STATE_META[s]?.label) || '—';

/** The three triaxial accelerometers, as the dataset names them. */
export const SENSORS = [
	{ key: 'back', label: 'Back' },
	{ key: 'side', label: 'Side' },
	{ key: 'top', label: 'Top' }
];
export const AXES = ['x', 'y', 'z'];

/** Seconds as m:ss, or h:mm:ss past an hour. @param {number} s */
export function clock(s) {
	const t = Math.max(0, Math.round(s));
	const h = Math.floor(t / 3600);
	const m = Math.floor((t % 3600) / 60);
	const sec = String(t % 60).padStart(2, '0');
	return h ? `${h}:${String(m).padStart(2, '0')}:${sec}` : `${m}:${sec}`;
}
