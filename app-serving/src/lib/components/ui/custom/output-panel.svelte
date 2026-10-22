<script>
	/**
	 * What the agent produces: the state it calls for each window, from
	 * larco/results and nothing else.
	 *
	 *   body           the machine in the called state, the embedding space, or the log of changes
	 *   Machine state  the latest call and its probabilities, the cycle's timeline,
	 *                  and how served compares with Stage 6's batch output
	 */
	import Pane from '$lib/components/ui/patterns/pane/index.js';
	import { Badge } from '@archetypeai/ds-ui-svelte-console/primitives/badge';
	import { Separator } from '@archetypeai/ds-ui-svelte-console/primitives/separator';
	import * as Tabs from '@archetypeai/ds-ui-svelte-console/primitives/tabs';
	import * as Tooltip from '@archetypeai/ds-ui-svelte-console/primitives/tooltip';
	import CircleArrowDownIcon from '@lucide/svelte/icons/circle-arrow-down';
	import WashingMachineIcon from '@lucide/svelte/icons/washing-machine';
	import ScatterChartIcon from '@lucide/svelte/icons/chart-scatter';
	import ListIcon from '@lucide/svelte/icons/list';
	import { larco, macroF1 } from '$lib/api/larco.svelte.js';
	import { stateColor, stateLabel } from '$lib/states.js';
	import Machine from './machine.svelte';
	import EmbeddingSpace from './embedding-space.svelte';
	import StateLog from './state-log.svelte';
	import Probabilities from './probabilities.svelte';
	import StateTimeline from './state-timeline.svelte';
	import RunControls from './run-controls.svelte';

	/** @type {{settingsOpen?: boolean, class?: string}} */
	let { settingsOpen = $bindable(false), class: className = '' } = $props();

	let view = $state('machine');
	const c = $derived(larco.cycle);
	const st = $derived(larco.status);
	const latest = $derived(c.windows.findLast((w) => w.served && !w.served.invalid));
	/** e.g. "osm-larco-w512-s512-cosine-k31-uniform" → "k 31 · cosine" */
	const model = $derived.by(() => {
		const m = /-k(\d+)-/.exec(larco.agent?.promoted ?? '');
		const metric = /-(cosine|l1|l2)-/.exec(larco.agent?.promoted ?? '');
		return m ? `kNN, k ${m[1]}${metric ? ` · ${metric[1]}` : ''}` : 'kNN';
	});
	const served = $derived(st?.revealed ? macroF1(c.windows, 'served') : null);
	const batch = $derived(st?.revealed && st?.batch ? macroF1(c.windows, 'batch') : null);
	const perWindow = $derived(
		c.latency.length ? [...c.latency].sort((a, b) => a - b)[Math.floor(c.latency.length / 2)] : null
	);
	const totals = $derived([
		{ label: 'Windows', value: c.served.toLocaleString() },
		st?.batch
			? {
					label: 'Served = batch',
					value: c.compared
						? `${c.sameState.toLocaleString()} / ${c.compared.toLocaleString()}`
						: '—'
				}
			: { label: 'Invalid', value: String(c.invalid) },
		{
			label: batch ? 'Macro-F1 · batch' : 'Macro-F1',
			value: served
				? served.macro.toFixed(2) + (batch ? ` · ${batch.macro.toFixed(2)}` : '')
				: 'at reveal'
		},
		{ label: 'Per window', value: perWindow == null ? '—' : `${Math.round(perWindow)} ms` }
	]);
</script>

<Tabs.Root bind:value={view} class="flex min-h-0 flex-col {className}">
	<Pane
		variant="panel"
		title="OSM Agent"
		icon={CircleArrowDownIcon}
		subtitle="Omega 1.5 + {model} · Archetype AI platform (Dev), agent serving"
		class="relative min-h-0 flex-1"
	>
		{#snippet titleAfter()}
			<Tooltip.Root>
				<Tooltip.Trigger variant="info" aria-label="About the agent" />
				<Tooltip.Content class="max-w-xs">
					The Stage 5 model, served: each window goes to a serving agent on the platform, which
					embeds it with Omega and calls the state of its nearest library windows. The probabilities
					are their shares. Batch is Stage 6's answer for the same window, run as a delivery.
				</Tooltip.Content>
			</Tooltip.Root>
		{/snippet}
		{#snippet actions()}
			<Tabs.List variant="segmented">
				<Tabs.Trigger value="machine" variant="icon" title="Machine" aria-label="Machine"
					><WashingMachineIcon /></Tabs.Trigger
				>
				<Tabs.Trigger
					value="embedding"
					variant="icon"
					title="Embedding space"
					aria-label="Embedding space"><ScatterChartIcon /></Tabs.Trigger
				>
				<Tabs.Trigger value="log" variant="icon" title="State changes" aria-label="State changes"
					><ListIcon /></Tabs.Trigger
				>
			</Tabs.List>
		{/snippet}

		<div class="flex min-h-0 flex-1 flex-col overflow-hidden">
			<Tabs.Content value="machine" class="flex min-h-0 flex-1 flex-col p-4"
				><Machine class="flex-1" /></Tabs.Content
			>
			<Tabs.Content value="embedding" class="flex min-h-0 flex-1 flex-col p-4"
				><EmbeddingSpace class="flex-1" /></Tabs.Content
			>
			<Tabs.Content value="log" class="flex min-h-0 flex-1 flex-col p-4"
				><StateLog class="flex-1" /></Tabs.Content
			>

			<Separator />

			<Pane title="Machine state" class="shrink-0">
				{#snippet actions()}
					{#if latest}
						<Badge
							variant="status"
							class="transition-none"
							style="color: {stateColor(latest.served?.state)}"
							>{stateLabel(latest.served?.state)}</Badge
						>
					{:else}
						<Badge variant="status" class="text-muted-foreground"
							>{larco.agent?.phase === 'running'
								? 'Awaiting'
								: (larco.agent?.phase ?? 'No agent')}</Badge
						>
					{/if}
				{/snippet}
				<Probabilities />
				<div class="pt-2"><StateTimeline /></div>
				<div class="grid grid-cols-2 gap-x-6 gap-y-1 pt-2 pr-56 sm:grid-cols-4">
					{#each totals as t (t.label)}
						<div class="flex flex-col">
							<span class="font-mono text-2xs text-muted-foreground uppercase">{t.label}</span>
							<span class="font-mono text-sm tabular-nums">{t.value}</span>
						</div>
					{/each}
				</div>
			</Pane>
		</div>

		<div class="absolute right-4 bottom-4 z-10">
			<RunControls bind:settingsOpen />
		</div>
	</Pane>
</Tabs.Root>
