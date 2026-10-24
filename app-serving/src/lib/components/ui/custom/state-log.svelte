<script>
	/**
	 * Every change of state the agent called, newest first: one row per change, not
	 * per window (a wash phase is hundreds of identical windows).
	 */
	import * as Table from '@archetypeai/ds-ui-svelte-console/primitives/table';
	import { EmptyState } from '@archetypeai/ds-ui-svelte-console/primitives/empty-state';
	import { larco } from '$lib/api/larco.svelte.js';
	import { clock, stateColor, stateLabel } from '$lib/states.js';
	import { cn } from '$lib/utils';

	/** @type {{class?: string}} */
	let { class: className = '' } = $props();
	const secs = $derived(larco.status?.windowSeconds ?? 2.56);
	const rows = $derived([...larco.cycle.changes].reverse().slice(0, 200));
</script>

<div class={cn('min-h-0 overflow-y-auto', className)}>
	{#if rows.length}
		<Table.Root>
			<Table.Header>
				<Table.Row>
					<Table.Head>At</Table.Head>
					<Table.Head>Window</Table.Head>
					<Table.Head>From</Table.Head>
					<Table.Head>To</Table.Head>
					<Table.Head>p</Table.Head>
				</Table.Row>
			</Table.Header>
			<Table.Body typography="mono" class="text-xs">
				{#each rows as r (r.index)}
					<Table.Row>
						<Table.Cell>{clock((r.index + 1) * secs)}</Table.Cell>
						<Table.Cell class="text-muted-foreground">{r.index.toLocaleString()}</Table.Cell>
						<Table.Cell style="color: {stateColor(r.from)}">{stateLabel(r.from)}</Table.Cell>
						<Table.Cell style="color: {stateColor(r.to)}">{stateLabel(r.to)}</Table.Cell>
						<Table.Cell>{r.p.toFixed(2)}</Table.Cell>
					</Table.Row>
				{/each}
			</Table.Body>
		</Table.Root>
	{:else}
		<EmptyState variant="list">No state called yet.</EmptyState>
	{/if}
</div>
