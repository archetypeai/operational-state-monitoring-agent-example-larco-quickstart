<script>
	/**
	 * The app's processes and the MQTT topics between them, with the rate this
	 * browser tab observes on each: when something stops, it shows which hop.
	 */
	import { larco } from '$lib/api/larco.svelte.js';

	const f = $derived(larco.flows);
	const GOOD = 'var(--color-atai-good)';
	const MUTED = 'var(--color-muted-foreground)';
	const on = (/** @type {number} */ r) => (r > 0 ? GOOD : MUTED);
	const fmt = (/** @type {number} */ r, unit = 'msg/s') =>
		r > 0 ? `${r < 10 ? r.toFixed(1) : Math.round(r)} ${unit}` : 'idle';
	const replayer = $derived(f['larco/raw'] > 0 || f['larco/status'] > 0);
	const agentOn = $derived(larco.agent?.phase === 'running');
	const wpq = $derived(larco.agent?.wpq ?? 1);
</script>

<svg
	viewBox="0 0 860 400"
	class="w-full font-mono"
	role="img"
	aria-label="The app's processes and topics"
>
	<defs>
		<marker
			id="larco-arrow"
			viewBox="0 0 8 8"
			refX="7"
			refY="4"
			markerWidth="6"
			markerHeight="6"
			orient="auto"
		>
			<path d="M0,0 L8,4 L0,8 z" fill={MUTED} />
		</marker>
	</defs>
	{#snippet box(
		/** @type {number} */ x,
		/** @type {number} */ y,
		/** @type {number} */ w,
		/** @type {string} */ title,
		/** @type {string[]} */ lines,
		/** @type {boolean} */ alive
	)}
		<rect
			{x}
			{y}
			width={w}
			height={34 + lines.length * 16}
			rx="2"
			fill="var(--color-background)"
			stroke="var(--color-border)"
		/>
		<circle cx={x + 14} cy={y + 17} r="4" fill={on(alive ? 1 : 0)} />
		<text x={x + 26} y={y + 21} class="text-sm" fill="var(--color-foreground)">{title}</text>
		{#each lines as l, i (i)}
			<text x={x + 26} y={y + 40 + i * 16} class="text-2xs" fill={MUTED}>{l}</text>
		{/each}
	{/snippet}
	{#snippet edge(
		/** @type {string} */ d,
		/** @type {string} */ label,
		/** @type {number} */ rate,
		/** @type {number} */ lx,
		/** @type {number} */ ly,
		unit = 'msg/s'
	)}
		<path
			{d}
			fill="none"
			stroke="var(--color-border)"
			stroke-width="1.5"
			marker-end="url(#larco-arrow)"
		/>
		{#if rate > 0}
			<circle r="3.5" fill={GOOD}
				><animateMotion dur="1.4s" repeatCount="indefinite" path={d} /></circle
			>
		{/if}
		<text x={lx} y={ly} class="text-2xs" fill={on(rate)}>{label} · {fmt(rate, unit)}</text>
	{/snippet}

	{@render box(
		20,
		20,
		300,
		'replayer (node)',
		['data/roles/delivery/<cycle>.csv', 'a 512-row window every 2.56 s / speed'],
		replayer
	)}
	{@render box(
		540,
		20,
		300,
		'serving bridge (node)',
		[
			`POST /query, ${wpq} window(s) at a time`,
			'serving agent · platform',
			'the API key stays here'
		],
		agentOn
	)}
	{@render box(230, 180, 400, 'mosquitto · tcp :1892 · ws :9010', [], larco.connected)}
	{@render box(
		180,
		300,
		500,
		'web app (this tab, mqtt.js over WebSockets)',
		['machine · probabilities · timeline · embedding space'],
		larco.connected
	)}

	{@render edge(
		'M 170,104 C 170,150 220,190 228,196',
		'larco/sensors',
		f['larco/sensors'],
		40,
		140,
		'win/s'
	)}
	{@render edge(
		'M 632,196 C 680,170 690,130 690,106',
		'larco/sensors',
		f['larco/sensors'],
		600,
		140,
		'win/s'
	)}
	{@render edge(
		'M 780,106 C 780,160 700,200 632,208',
		'larco/results',
		f['larco/results'],
		700,
		170,
		'win/s'
	)}
	{@render edge(
		'M 430,214 L 430,298',
		'larco/raw · results · status',
		f['larco/raw'] + f['larco/results'] + f['larco/status'],
		440,
		262
	)}
	{@render edge(
		'M 300,298 C 300,260 290,230 290,216',
		'larco/control',
		f['larco/control'],
		120,
		262
	)}
</svg>
