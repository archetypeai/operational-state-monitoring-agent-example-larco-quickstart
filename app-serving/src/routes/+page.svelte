<script>
	/**
	 * LARCO Monitor: the workbench. What goes into the agent on the left (the
	 * cycle's nine accelerometer channels), what comes out on the right (the
	 * state it calls, window by window).
	 *
	 * The agent runs on the Archetype AI platform: mosquitto carries the data,
	 * the serving bridge (scripts/serving-bridge.js) sends each window to a
	 * serving agent of the model fit/test.py promoted, and this page subscribes
	 * to the broker. The API key stays with the bridge.
	 */
	import { onMount, onDestroy } from 'svelte';
	import Menubar from '@archetypeai/ds-ui-svelte-labs/primitives/menubar';
	import { Badge } from '@archetypeai/ds-ui-svelte-console/primitives/badge';
	import { larco } from '$lib/api/larco.svelte.js';
	import InputPanel from '$lib/components/ui/custom/input-panel.svelte';
	import OutputPanel from '$lib/components/ui/custom/output-panel.svelte';
	import SettingsDialog from '$lib/components/ui/custom/settings-dialog.svelte';

	let settingsOpen = $state(false);
	const phase = $derived(!larco.connected ? 'no broker' : (larco.agent?.phase ?? 'no agent'));
	const tone = $derived(
		phase === 'running'
			? 'text-atai-good'
			: phase === 'no broker' || phase === 'stopped' || phase === 'no agent'
				? 'text-atai-critical'
				: 'text-atai-warning'
	);

	onMount(() => larco.connect());
	onDestroy(() => larco.disconnect());
</script>

<svelte:head><title>LARCO Monitor · platform</title></svelte:head>

<div class="grid h-dvh w-full grid-rows-[auto_1fr] overflow-hidden bg-background">
	<Menubar>
		{#snippet partnerLogo()}
			<span class="text-xs text-muted-foreground">LARCO Monitor</span>
		{/snippet}
		<span class="hidden font-mono text-xs text-muted-foreground lg:inline"
			>{larco.rate.toFixed(1)} win/s</span
		>
		<Badge variant="status" class={tone}>
			{phase === 'running' ? 'platform · serving' : phase}
		</Badge>
	</Menubar>

	<div class="grid min-h-0 grid-cols-1 lg:grid-cols-2">
		<InputPanel class="min-h-0 border-border lg:border-r" />
		<OutputPanel bind:settingsOpen class="min-h-0 border-t border-border lg:border-t-0" />
	</div>
</div>

<SettingsDialog bind:open={settingsOpen} />
