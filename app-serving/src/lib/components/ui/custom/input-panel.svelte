<script>
	/**
	 * What goes into the agent: one becken-flt cycle from the delivery, the faulty
	 * second unit the model never saw a cycle of, replayed at 200 Hz.
	 */
	import Pane from '$lib/components/ui/patterns/pane/index.js';
	import * as Tooltip from '@archetypeai/ds-ui-svelte-console/primitives/tooltip';
	import { Progress } from '@archetypeai/ds-ui-svelte-console/primitives/progress';
	import CircleArrowUpIcon from '@lucide/svelte/icons/circle-arrow-up';
	import { larco } from '$lib/api/larco.svelte.js';
	import { clock } from '$lib/states.js';
	import CycleSelector from './cycle-selector.svelte';
	import SensorGrid from './sensor-grid.svelte';

	/** @type {{class?: string}} */
	let { class: className = '' } = $props();
	const st = $derived(larco.status);
	const secs = $derived(st?.windowSeconds ?? 2.56);
	const pace = $derived(
		!st
			? ''
			: st.waiting === 'the agent'
				? 'waiting for the agent'
				: !st.playing
					? st.ended
						? 'ended'
						: 'paused'
					: st.speed === 'max'
						? 'as fast as the agent answers'
						: `${st.speed}× real time${st.throttled ? ', held by the agent' : ''}`
	);
</script>

<Pane
	variant="panel"
	title="Input"
	icon={CircleArrowUpIcon}
	subtitle="LARCO · becken-flt, the faulty unit · 3 accelerometers × 3 axes @ 200 Hz"
	class={className}
>
	{#snippet actions()}
		<CycleSelector />
	{/snippet}

	<div class="flex min-h-0 flex-1 flex-col overflow-y-auto">
		<Pane title="Accelerometers" class="min-h-0 flex-1">
			{#snippet titleAfter()}
				<Tooltip.Root>
					<Tooltip.Trigger variant="info" aria-label="About the accelerometers" />
					<Tooltip.Content class="max-w-xs">
						The nine channels the agent is given, one window (512 readings, 2.56 s) at a time. The
						agent gets them z-scored with the library's statistics, as it was fitted; they are shown
						here in g. Level is the window's RMS about its own mean.
					</Tooltip.Content>
				</Tooltip.Root>
			{/snippet}
			<SensorGrid class="flex-1" />
		</Pane>

		<Pane title="Cycle" class="border-t border-border">
			<div class="flex items-center gap-4">
				<span class="shrink-0 font-mono text-sm tabular-nums">
					{clock((st?.index ?? 0) * secs)}
					<span class="text-muted-foreground">/ {clock((st?.total ?? 0) * secs)}</span>
				</span>
				<Progress
					value={st?.total ? (100 * st.index) / st.total : 0}
					size="sm"
					class="flex-1"
					aria-label="Position in the cycle"
				/>
				<span class="shrink-0 font-mono text-xs text-muted-foreground tabular-nums">
					window {(st?.index ?? 0).toLocaleString()} of {(st?.total ?? 0).toLocaleString()}
				</span>
			</div>
			<p class="text-xs text-muted-foreground">
				{pace}{st && !st.batch
					? " · no Stage 6 output for this file, so served isn't compared with batch"
					: ''}
			</p>
		</Pane>
	</div>
</Pane>
