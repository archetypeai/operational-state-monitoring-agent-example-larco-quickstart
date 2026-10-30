<script>
	/**
	 * What you do to the demo rather than watch: the replay's pace, how many windows
	 * a query carries, what is moving over MQTT, and the serving agent.
	 * Which cycle is replayed is chosen in the Input panel.
	 */
	import * as Dialog from '@archetypeai/ds-ui-svelte-console/primitives/dialog';
	import * as Tabs from '@archetypeai/ds-ui-svelte-console/primitives/tabs';
	import { Button } from '@archetypeai/ds-ui-svelte-console/primitives/button';
	import { Badge } from '@archetypeai/ds-ui-svelte-console/primitives/badge';
	import { Label } from '@archetypeai/ds-ui-svelte-console/primitives/label';
	import RotateCcwIcon from '@lucide/svelte/icons/rotate-ccw';
	import { larco, TOPICS } from '$lib/api/larco.svelte.js';
	import DataFlow from './data-flow.svelte';

	/** @type {{open?: boolean}} */
	let { open = $bindable(false) } = $props();
	const st = $derived(larco.status);
	const a = $derived(larco.agent);
	const c = $derived(larco.cycle);
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
				</dl>
				<p class={PROSE}>
					An invalid window is one the agent rejected, or a query the platform didn't answer (the
					serving bridge's log says which).
				</p>
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
