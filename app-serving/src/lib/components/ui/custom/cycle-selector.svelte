<script>
	/**
	 * Which becken-flt cycle is replayed: any of the delivery files on disk, the
	 * quickstart's five short cycles first. The name is the dataset's (temperature,
	 * programme, run), and says nothing about the states inside the cycle.
	 */
	import * as Select from '@archetypeai/ds-ui-svelte-console/primitives/select';
	import { larco } from '$lib/api/larco.svelte.js';

	const st = $derived(larco.status);
	const cycles = $derived(st?.cycles ?? []);
	const groups = $derived(
		[
			{
				label: "The quickstart's 5 short cycles",
				items: cycles.filter((/** @type {any} */ c) => c.quickstart)
			},
			{
				label: 'Every delivery file',
				items: cycles.filter((/** @type {any} */ c) => !c.quickstart)
			}
		].filter((g) => g.items.length)
	);
	const minutes = (/** @type {number} */ rows) => `${Math.round(rows / 200 / 60)} min`;
</script>

<Select.Root
	type="single"
	value={String(st?.cycle ?? '')}
	onValueChange={(/** @type {string} */ v) => v && larco.select(Number(v))}
	disabled={!larco.connected || !cycles.length}
>
	<Select.Trigger size="sm" typography="mono" class="max-w-72" aria-label="Cycle">
		<span class="truncate">{st?.cycleName ?? 'no cycle'}</span>
	</Select.Trigger>
	<Select.Content class="max-h-96">
		{#each groups as g (g.label)}
			<Select.Group>
				<Select.GroupHeading>{g.label}</Select.GroupHeading>
				{#each g.items as c (c.id)}
					<Select.Item value={String(c.id)} label={c.name} typography="mono">
						<span class="flex w-full justify-between gap-4">
							<span>{c.name}</span>
							<span class="text-muted-foreground">{minutes(c.rows)}</span>
						</span>
					</Select.Item>
				{/each}
			</Select.Group>
		{/each}
	</Select.Content>
</Select.Root>
