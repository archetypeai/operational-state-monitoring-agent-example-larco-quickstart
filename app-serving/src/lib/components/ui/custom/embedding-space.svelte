<script>
	/**
	 * Where the latest windows sit among the library the model compares them with.
	 *
	 * The field is the library's 800 windows (window 512, step 512, 200 a state),
	 * recomputed by fit/library_embeddings.py and drawn, a dot each and a density per
	 * state, on the two directions of their Omega embeddings that best separate the
	 * four states (unit length, PCA to 50, LDA to 2). The trail is the last windows of
	 * this cycle through the same two axes, from the embeddings the served agent
	 * returns, each dot in the state the agent called.
	 *
	 * A cross-section, not the model: the kNN compares all 6,912 dimensions
	 * (cosine), and two directions are drawn, so a window can sit in one state's
	 * field here and still be called another.
	 */
	// @ts-expect-error - d3-contour ships no types
	import { contourDensity } from 'd3-contour';
	import { larco } from '$lib/api/larco.svelte.js';
	import { STATES, STATE_META, stateColor } from '$lib/states.js';
	import { cn } from '$lib/utils';

	/** @type {{class?: string}} */
	let { class: className = '' } = $props();
	const view = $derived(larco.view);
	const trail = $derived(larco.cycle.trail);

	let boxW = $state(560);
	let boxH = $state(360);
	const W = $derived(Math.max(boxW, 240));
	const H = $derived(Math.max(boxH, 200));
	const PAD = 20;

	// Framed on the library, not on the trail, so a window leaving the field looks it.
	const bound = $derived.by(() => {
		if (!view) return 3;
		const xs = [...view.ref].map(Math.abs).sort((a, b) => a - b);
		return xs[Math.floor(xs.length * 0.995)] * 1.15;
	});
	const unit = $derived(Math.min(W - 2 * PAD, H - 2 * PAD) / (2 * bound));
	const sx = (/** @type {number} */ v) => W / 2 + v * unit;
	const sy = (/** @type {number} */ v) => H / 2 - v * unit;

	/** d3-contour's GeoJSON rings as SVG path data. @param {any} mp */
	function toPath(mp) {
		let d = '';
		for (const poly of mp.coordinates ?? [])
			for (const ring of poly)
				d +=
					'M' +
					ring
						.map((/** @type {number[]} */ p) => `${p[0].toFixed(1)} ${p[1].toFixed(1)}`)
						.join('L') +
					'Z';
		return d;
	}

	const fields = $derived.by(() => {
		if (!view) return [];
		return STATES.map((s, code) => {
			/** @type {[number, number][]} */
			const pts = [];
			for (let i = 0; i < view.state.length; i++)
				if (view.state[i] === code) pts.push([view.ref[2 * i], view.ref[2 * i + 1]]);
			const bands = contourDensity()
				.x((/** @type {any} */ d) => sx(d[0]))
				.y((/** @type {any} */ d) => sy(d[1]))
				.size([W, H])
				.bandwidth(Math.max(8, unit * 0.12))
				.thresholds(4)(pts);
			return {
				state: s,
				bands: bands.map((/** @type {any} */ b, /** @type {number} */ i) => ({
					d: toPath(b),
					o: 0.08 + (i / Math.max(bands.length - 1, 1)) * 0.22
				}))
			};
		});
	});
	const path = $derived(
		trail.map((p, i) => `${i ? 'L' : 'M'}${sx(p.x).toFixed(1)} ${sy(p.y).toFixed(1)}`).join(' ')
	);
	const head = $derived(trail.at(-1));
	const axes = $derived(view?.manifest.axes ?? ['PC 1', 'PC 2']);
	/** The library's own windows, a dot each, under the trail. */
	const dots = $derived.by(() => {
		if (!view) return [];
		const out = [];
		for (let i = 0; i < view.state.length; i++)
			out.push({ x: sx(view.ref[2 * i]), y: sy(view.ref[2 * i + 1]), s: STATES[view.state[i]] });
		return out;
	});
</script>

<div class={cn('flex min-h-0 flex-col gap-2', className)}>
	{#if !view}
		<p class="m-auto max-w-sm text-center text-sm text-muted-foreground">
			{larco.viewMissing
				? 'No library view yet. Run python fit/library_embeddings.py once (4 batch runs), then reload.'
				: 'Loading the library view…'}
		</p>
	{:else}
		<div class="min-h-0 w-full flex-1" bind:clientWidth={boxW} bind:clientHeight={boxH}>
			<svg
				viewBox="0 0 {W} {H}"
				class="h-full w-full"
				fill="none"
				role="img"
				aria-label="The latest windows among the library"
			>
				<line x1={sx(0)} y1={PAD} x2={sx(0)} y2={H - PAD} stroke="var(--color-border)" />
				<line x1={PAD} y1={sy(0)} x2={W - PAD} y2={sy(0)} stroke="var(--color-border)" />
				{#each fields as f (f.state)}
					{#each f.bands as b, i (i)}
						<path d={b.d} fill={STATE_META[f.state].color} fill-rule="evenodd" opacity={b.o} />
					{/each}
				{/each}
				{#each dots as d, i (i)}
					<circle cx={d.x} cy={d.y} r="1.25" fill={STATE_META[d.s].color} opacity="0.45" />
				{/each}
				{#if trail.length > 1}
					<path
						d={path}
						stroke="var(--plate-ink)"
						stroke-width="1"
						stroke-linejoin="round"
						opacity="0.6"
					/>
				{/if}
				{#each trail as p, i (i)}
					<circle cx={sx(p.x)} cy={sy(p.y)} r="2" fill={stateColor(p.state)} />
				{/each}
				{#if head}
					<circle
						cx={sx(head.x)}
						cy={sy(head.y)}
						r="5"
						fill={stateColor(head.state)}
						stroke="var(--color-background)"
						stroke-width="1.5"
					/>
				{/if}
				<text x={W - PAD} y={sy(0) - 6} text-anchor="end" class="fill-muted-foreground text-2xs"
					>{axes[0]}</text
				>
				<text x={sx(0) + 6} y={PAD + 10} class="fill-muted-foreground text-2xs">{axes[1]}</text>
			</svg>
		</div>
		<div
			class="flex flex-wrap items-center justify-center gap-x-6 gap-y-1 text-xs text-muted-foreground"
		>
			{#each STATES as s (s)}
				<span class="flex items-center gap-1.5">
					<span class="size-2 rounded-full" style="background: {STATE_META[s].color}"></span>
					{STATE_META[s].label}
					<span class="font-mono">{view.manifest.perState?.[s] ?? ''}</span>
				</span>
			{/each}
			<span>library windows · the trail is the last {trail.length} of this cycle</span>
		</div>
	{/if}
</div>
