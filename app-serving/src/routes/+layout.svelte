<script>
	import './layout.css';
	import favicon from '$lib/assets/favicon.svg';
	import { onMount } from 'svelte';
	import { darkMode } from '@archetypeai/ds-ui-svelte-console/primitives/theme';
	import * as Tooltip from '@archetypeai/ds-ui-svelte-console/primitives/tooltip';

	let { children } = $props();

	// Dark by default, as the sibling monitors are; the Menubar's toggle switches it.
	onMount(() => darkMode.set(true));
</script>

<svelte:head><link rel="icon" href={favicon} /></svelte:head>

<!-- Every info tooltip reads this context when it is created: without the
     provider the route fails during SSR. -->
<Tooltip.Provider delayDuration={200}>
	{@render children?.()}
</Tooltip.Provider>
