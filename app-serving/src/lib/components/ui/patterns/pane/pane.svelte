<script>
	/**
	 * Workbench pane: a captioned region of the page, separated from its neighbours
	 * by the grid's hairlines rather than wrapped in a card (the OSM and TEP demos'
	 * pattern). Two levels:
	 *
	 * - `panel`: a top-level region (Input / Output); the caption is the DS
	 *   `workbenchHeader` label with an icon, and the content is flush.
	 * - `block`: a section within a panel; the caption is the `sectionHeader`
	 *   label, and the content is inset.
	 *
	 * @typedef {Object} Props
	 * @property {string} [title]
	 * @property {'panel'|'block'} [variant]
	 * @property {import('svelte').Component} [icon] lucide icon left of the caption
	 * @property {string} [subtitle] a second line of standing facts (panel only)
	 * @property {string} [class]
	 * @property {import('svelte').Snippet} [actions] controls pinned right in the caption row
	 * @property {import('svelte').Snippet} [titleAfter] right after the caption, e.g. an info tooltip
	 * @property {import('svelte').Snippet} [children]
	 */
	import { cn } from '$lib/utils';
	import { Label } from '@archetypeai/ds-ui-svelte-console/primitives/label';

	/** @type {Props} */
	let {
		title = '',
		subtitle = '',
		variant = 'block',
		icon: Icon = undefined,
		class: className,
		actions,
		titleAfter,
		children
	} = $props();
	const isPanel = $derived(variant === 'panel');
</script>

<section class={cn('flex min-h-0 flex-col', isPanel ? '' : 'gap-2 p-4', className)}>
	{#if title || actions}
		<header
			class={cn(
				'flex items-center justify-between gap-2',
				isPanel ? 'min-h-12 px-4 pt-4' : 'min-h-6'
			)}
		>
			<div class="flex min-w-0 items-center gap-2">
				<Label
					variant={isPanel ? 'workbenchHeader' : 'sectionHeader'}
					class={isPanel ? 'text-foreground' : ''}
				>
					{#if Icon}<Icon class="size-4" strokeWidth={1.25} aria-hidden="true" />{/if}
					{title}
				</Label>
				{@render titleAfter?.()}
			</div>
			{#if actions}
				<div class="flex items-center gap-2">{@render actions()}</div>
			{/if}
		</header>
	{/if}
	{#if isPanel && subtitle}
		<p class="truncate px-4 pt-1 text-xs text-muted-foreground">{subtitle}</p>
	{/if}
	{@render children?.()}
</section>
