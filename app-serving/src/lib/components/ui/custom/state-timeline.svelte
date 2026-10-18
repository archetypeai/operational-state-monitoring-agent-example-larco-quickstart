<script>
	/**
	 * The whole cycle, one column a window: what the served agent called and, once
	 * revealed, the held-back label (the two rows are labelled only then). Served
	 * against batch is a count under the timeline, not a row: the two match. Drawn
	 * as runs of one state, so a 3-hour cycle (4,000+ windows) is a few dozen
	 * rectangles.
	 */
	import { larco } from '$lib/api/larco.svelte.js';
	import { stateColor } from '$lib/states.js';

	const st = $derived(larco.status);
	const windows = $derived(larco.cycle.windows);
	const total = $derived(Math.max(st?.total ?? 0, windows.length, 1));
	let width = $state(400);

	/** @param {(w: any) => string | null | undefined} pick */
	function runs(pick) {
		/** @type {{from: number, to: number, state: string}[]} */
		const out = [];
		for (const w of windows) {
			const s = pick(w);
			if (!s) continue;
			const last = out.at(-1);
			if (last && last.state === s && last.to === w.index) last.to = w.index + 1;
			else out.push({ from: w.index, to: w.index + 1, state: s });
		}
		return out;
	}
	const rows = $derived([
		{ label: 'Agent', runs: runs((w) => (w.served && !w.served.invalid ? w.served.state : null)) },
		...(st?.revealed ? [{ label: 'Truth', runs: runs((w) => w.label) }] : [])
	]);
	const x = (/** @type {number} */ i) => (i / total) * width;
	const ROW = 14;
	const GAP = 6;
</script>

<div class={rows.length > 1 ? 'grid grid-cols-[3rem_minmax(0,1fr)] gap-x-3' : ''}>
	{#if rows.length > 1}
		<div class="flex flex-col" style="gap: {GAP}px">
			{#each rows as r (r.label)}
				<span class="flex items-center text-2xs text-muted-foreground" style="height: {ROW}px"
					>{r.label}</span
				>
			{/each}
		</div>
	{/if}
	<div bind:clientWidth={width}>
		<svg
			width="100%"
			height={rows.length * (ROW + GAP) - GAP}
			aria-label="States through the cycle"
			role="img"
		>
			{#each rows as r, j (r.label)}
				<rect x="0" y={j * (ROW + GAP)} {width} height={ROW} fill="var(--color-muted)" />
				{#each r.runs as run (run.from)}
					<rect
						x={x(run.from)}
						y={j * (ROW + GAP)}
						width={Math.max(1, x(run.to) - x(run.from))}
						height={ROW}
						fill={stateColor(run.state)}
					/>
				{/each}
			{/each}
			{#if st?.index}
				<line
					x1={x(st.index)}
					x2={x(st.index)}
					y1="0"
					y2={rows.length * (ROW + GAP)}
					stroke="var(--color-foreground)"
					stroke-width="1"
				/>
			{/if}
		</svg>
	</div>
</div>
