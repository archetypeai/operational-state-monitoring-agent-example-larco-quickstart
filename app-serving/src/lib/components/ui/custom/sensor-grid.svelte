<script>
	/**
	 * The nine channels the agent sees: three accelerometers (back, side, top of the
	 * machine), three axes each, at 200 Hz.
	 *
	 * Per channel: the latest window's trace (2.56 s, decimated to 64 points), its
	 * vibration level (RMS about the window's own mean, in g), and the level over
	 * the last ~6 minutes. The level is what tells the states apart by eye: spin is
	 * several times louder than wash, fill and drain sit in between.
	 */
	import { larco } from '$lib/api/larco.svelte.js';
	import { SENSORS, AXES } from '$lib/states.js';
	import { cn } from '$lib/utils';

	/** @type {{class?: string}} */
	let { class: className = '' } = $props();
	const c = $derived(larco.cycle);
	const channels = $derived(
		larco.status?.channels ?? SENSORS.flatMap((s) => AXES.map((a) => `${s.key}.${a}`))
	);

	/** @param {number[] | undefined} v @param {number} w @param {number} h */
	function line(v, w, h) {
		if (!v || v.length < 2) return '';
		let lo = Infinity;
		let hi = -Infinity;
		for (const x of v) {
			lo = Math.min(lo, x);
			hi = Math.max(hi, x);
		}
		const r = hi - lo || 1;
		return v
			.map(
				(x, i) =>
					`${i ? 'L' : 'M'}${((i / (v.length - 1)) * w).toFixed(1)} ${(h - 1 - ((x - lo) / r) * (h - 2)).toFixed(1)}`
			)
			.join(' ');
	}
	/** @param {number | undefined} g */
	const fmt = (g) =>
		g === undefined || !Number.isFinite(g) ? '—' : g >= 0.1 ? g.toFixed(2) : g.toFixed(3);
</script>

<div class={cn('flex flex-col justify-evenly gap-4', className)}>
	{#each SENSORS as s (s.key)}
		<div class="flex flex-col gap-1">
			<div class="flex items-center gap-2">
				<span class="font-mono text-2xs text-muted-foreground uppercase">{s.label}</span>
				<span class="h-px flex-1 bg-border"></span>
			</div>
			{#each AXES as a (a)}
				{@const k = channels.indexOf(`${s.key}.${a}`)}
				<div class="grid grid-cols-[1.5rem_minmax(0,1fr)_4rem_6rem] items-center gap-3">
					<span class="font-mono text-xs text-muted-foreground">{a}</span>
					<svg
						viewBox="0 0 200 32"
						preserveAspectRatio="none"
						class="h-8 w-full"
						fill="none"
						aria-hidden="true"
					>
						<path
							d={line(c.trace[k], 200, 32)}
							stroke="var(--plate-ink)"
							stroke-width="1"
							vector-effect="non-scaling-stroke"
						/>
					</svg>
					<span class="text-right font-mono text-xs tabular-nums"
						>{fmt(c.rms[k])} <span class="text-muted-foreground">g</span></span
					>
					<svg
						viewBox="0 0 96 32"
						preserveAspectRatio="none"
						class="h-8 w-24"
						fill="none"
						aria-hidden="true"
					>
						<path
							d={line(c.history[k], 96, 32)}
							stroke="var(--color-muted-foreground)"
							stroke-width="1"
							vector-effect="non-scaling-stroke"
						/>
					</svg>
				</div>
			{/each}
		</div>
	{/each}
	<div class="grid grid-cols-[1.5rem_minmax(0,1fr)_4rem_6rem] gap-3 text-2xs text-muted-foreground">
		<span></span><span>this window, 2.56 s</span><span class="text-right">level</span><span
			>last ~6 min</span
		>
	</div>
</div>
