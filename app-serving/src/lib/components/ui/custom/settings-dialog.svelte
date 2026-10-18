<script>
	/**
	 * What you do to the demo rather than watch: the replay's pace, how many windows
	 * a query carries, what is moving over MQTT, the serving agent, and the reveal.
	 * Which cycle is replayed is chosen in the Input panel.
	 */
	import * as Dialog from '@archetypeai/ds-ui-svelte-console/primitives/dialog';
	import * as Tabs from '@archetypeai/ds-ui-svelte-console/primitives/tabs';
	import { Button } from '@archetypeai/ds-ui-svelte-console/primitives/button';
	import { Badge } from '@archetypeai/ds-ui-svelte-console/primitives/badge';
	import { Label } from '@archetypeai/ds-ui-svelte-console/primitives/label';
	import EyeIcon from '@lucide/svelte/icons/eye';
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import { larco, TOPICS } from '$lib/api/larco.svelte.js';
	import { STATES, STATE_META } from '$lib/states.js';
	import DataFlow from './data-flow.svelte';

	/** @type {{open?: boolean}} */
	let { open = $bindable(false) } = $props();
	const st = $derived(larco.status);
	const a = $derived(larco.agent);
	const c = $derived(larco.cycle);
	const truthTotal = $derived(st?.truth ? STATES.reduce((s, k) => s + (st.truth[k] ?? 0), 0) : 0);
	const PROSE = 'max-w-prose text-xs text-muted-foreground';
</script>

<Dialog.Root bind:open>
	<Dialog.Content class="sm:max-w-4xl">
		<Dialog.Header>
			<Dialog.Title>Settings</Dialog.Title>
			<Dialog.Description>
				The replay's pace and what the serving agent is doing. Nothing here changes the model.
			</Dialog.Description>
		</Dialog.Header>
		<Dialog.Body class="space-y-5">
			<section class="space-y-2">
				<Label variant="sectionHeader">Agent</Label>
				<div class="flex flex-wrap items-center gap-x-6 gap-y-1 font-mono text-xs">
					<Badge
						variant="status"
						class={a?.phase === 'running' ? 'text-atai-good' : 'text-atai-warning'}
						>{a?.phase ?? 'no bridge'}</Badge
					>
					<span><span class="text-muted-foreground">agent</span> {a?.agent ?? '—'}</span>
					<span><span class="text-muted-foreground">bundle</span> {a?.bundle ?? '—'}</span>
					<span
						><span class="text-muted-foreground">median query</span>
						{a?.medianLatencyMs != null ? `${a.medianLatencyMs} ms` : '—'}</span
					>
				</div>
				<p class="font-mono text-xs text-muted-foreground">{a?.blueprint ?? ''}</p>
				{#if a?.detail}<p class={PROSE}>{a.detail}</p>{/if}
			</section>

			<section class="space-y-2">
				<Label variant="sectionHeader">Windows per query</Label>
				<Tabs.Root
					value={String(st?.wpq ?? 1)}
					onValueChange={(/** @type {string} */ v) => larco.setWpq(Number(v))}
				>
					<Tabs.List variant="segmented">
						{#each [1, 2, 4, 8] as n (n)}
							<Tabs.Trigger value={String(n)}>{n}</Tabs.Trigger>
						{/each}
					</Tabs.List>
				</Tabs.Root>
				<p class={PROSE}>
					1 is live: the latest window, as the machine's controller would ask every 2.56 s. More
					windows a query wait for each other but cost less each (Stage 8: 8 a query took half the
					time a window, with the same answers).
				</p>
			</section>

			<section class="space-y-2">
				<Label variant="sectionHeader">Speed</Label>
				<div class="flex flex-wrap items-center gap-4">
					<Tabs.Root
						value={String(st?.speed ?? 1)}
						onValueChange={(/** @type {string} */ v) =>
							larco.setSpeed(v === 'max' ? 'max' : Number(v))}
					>
						<Tabs.List variant="segmented">
							{#each ['1', '2', '4', '8', 'max'] as v (v)}
								<Tabs.Trigger value={v}>{v === 'max' ? 'Max' : `${v}×`}</Tabs.Trigger>
							{/each}
						</Tabs.List>
					</Tabs.Root>
					<Button
						variant="outline"
						size="sm"
						disabled={!larco.connected}
						onclick={() => larco.restart()}
					>
						<RotateCcwIcon aria-hidden="true" />Restart the cycle
					</Button>
				</div>
				<p class={PROSE}>
					Times real time, at most: the replayer never runs more than a few windows ahead of the
					agent's answers. Max sends as fast as the agent answers ({larco.rate.toFixed(1)} windows/s now).
				</p>
			</section>

			<section class="space-y-2">
				<Label variant="sectionHeader">This cycle</Label>
				<dl class="flex flex-wrap gap-x-6 gap-y-1 font-mono text-sm">
					<div class="flex gap-1.5">
						<dt class="text-muted-foreground">answered</dt>
						<dd>{c.served.toLocaleString()}</dd>
					</div>
					<div class="flex gap-1.5">
						<dt class="text-muted-foreground">invalid</dt>
						<dd class={c.invalid ? 'text-atai-warning' : ''}>{c.invalid}</dd>
					</div>
					{#if st?.batch}
						<div class="flex gap-1.5">
							<dt class="text-muted-foreground">same state as batch</dt>
							<dd>{c.sameState.toLocaleString()} / {c.compared.toLocaleString()}</dd>
						</div>
						<div class="flex gap-1.5">
							<dt class="text-muted-foreground">probabilities within 1e-4</dt>
							<dd>{c.sameP.toLocaleString()} / {c.compared.toLocaleString()}</dd>
						</div>
					{/if}
				</dl>
				<p class={PROSE}>
					{st?.batch
						? "Batch is Stage 6's output for this file (fit/out/delivery/). A window near a tie can differ by one neighbour's vote (Stage 8: a few in 3,178)."
						: 'This file has no Stage 6 output in fit/out/delivery/, so there is nothing to compare with.'}
				</p>
			</section>

			<section class="space-y-2">
				<Label variant="sectionHeader">Ground truth</Label>
				{#if st?.revealed && st?.truth}
					<div class="flex flex-wrap gap-x-6 gap-y-1 text-sm">
						{#each STATES as s (s)}
							<span
								><span style="color: {STATE_META[s].color}">{STATE_META[s].label}</span>
								<span class="font-mono"
									>{truthTotal ? Math.round((100 * (st.truth[s] ?? 0)) / truthTotal) : 0}%</span
								></span
							>
						{/each}
					</div>
					<p class={PROSE}>
						The held-back labels (data/roles/delivery_labels/) now show as the timeline's Truth row,
						in the log, and in the macro-F1.
					</p>
				{:else}
					<p class={PROSE}>
						Withheld until you ask, or until the cycle ends: the agent never sees them, and the page
						shows only what it calls.
					</p>
					<Button
						variant="outline"
						size="sm"
						disabled={!larco.connected}
						onclick={() => larco.reveal()}
					>
						<EyeIcon aria-hidden="true" />Reveal the labels
					</Button>
				{/if}
			</section>

			<section class="space-y-2">
				<Label variant="sectionHeader">Live data flow</Label>
				<div class="grid grid-cols-2 gap-x-6 gap-y-1 font-mono text-xs sm:grid-cols-4">
					{#each TOPICS as t (t)}
						{@const r = larco.flows[t] ?? 0}
						<span class={r > 0 ? 'text-atai-good' : 'text-muted-foreground'}
							>{t} {r > 0 ? `${r < 10 ? r.toFixed(1) : Math.round(r)}/s` : 'idle'}</span
						>
					{/each}
				</div>
				<DataFlow />
			</section>
		</Dialog.Body>
	</Dialog.Content>
</Dialog.Root>
