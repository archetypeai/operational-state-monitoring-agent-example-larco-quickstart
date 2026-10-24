<script>
	/**
	 * The whole cycle, one column a window, in the state the served agent called.
	 * Drawn as runs of one state, so a 3-hour cycle (4,000+ windows) is a few
	 * dozen rectangles.
	 */
	import { larco } from '$lib/api/larco.svelte.js';
	import { stateColor } from '$lib/states.js';

	const st = $derived(larco.status);
	const windows = $derived(larco.cycle.windows);
	const total = $derived(Math.max(st?.total ?? 0, windows.length, 1));
	let width = $state(400);

	const runs = $derived.by(() => {
		/** @type {{from: number, to: number, state: string}[]} */
		const out = [];
		for (const w of windows) {
			const s = w.served && !w.served.invalid ? w.served.state : null;
			if (!s) continue;
			const last = out.at(-1);
			if (last && last.state === s && last.to === w.index) last.to = w.index + 1;
			else out.push({ from: w.index, to: w.index + 1, state: s });
		}
		return out;
	});
	const x = (/** @type {number} */ i) => (i / total) * width;
	const ROW = 14;
</script>

<div bind:clientWidth={width}>
	<svg width="100%" height={ROW} aria-label="The agent's states through the cycle" role="img">
		<rect x="0" y="0" {width} height={ROW} fill="var(--color-muted)" />
		{#each runs as run (run.from)}
			<rect
				x={x(run.from)}
				y="0"
				width={Math.max(1, x(run.to) - x(run.from))}
				height={ROW}
				fill={stateColor(run.state)}
			/>
		{/each}
		{#if st?.index}
			<line
				x1={x(st.index)}
				x2={x(st.index)}
				y1="0"
				y2={ROW}
				stroke="var(--color-foreground)"
				stroke-width="1"
			/>
		{/if}
	</svg>
</div>
