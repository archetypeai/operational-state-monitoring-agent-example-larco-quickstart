<script>
	/**
	 * The latest window's four probabilities: the share of its k nearest library
	 * windows (k from the promoted model, 5 here) in each state. A state at 0.6
	 * against 0.4 is a near-tie.
	 */
	import { larco } from '$lib/api/larco.svelte.js';
	import { STATES, STATE_META } from '$lib/states.js';

	const latest = $derived(larco.cycle.windows.findLast((w) => w.served && !w.served.invalid));
</script>

<div class="grid grid-cols-[3rem_minmax(0,1fr)_3rem] items-center gap-x-3 gap-y-1.5">
	{#each STATES as s (s)}
		{@const p = latest?.served?.p[s] ?? 0}
		<span
			class="text-xs {latest?.served?.state === s ? 'text-foreground' : 'text-muted-foreground'}"
			>{STATE_META[s].label}</span
		>
		<div class="h-2 overflow-hidden rounded-full bg-muted">
			<div
				class="h-full rounded-full"
				style="width: {p * 100}%; background: {STATE_META[s].color}"
			></div>
		</div>
		<span class="text-right font-mono text-xs tabular-nums">{p.toFixed(2)}</span>
	{/each}
</div>
