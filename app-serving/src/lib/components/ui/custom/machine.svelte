<script>
	/**
	 * The machine, drawn in the state the agent calls for the latest window: water
	 * coming in (fill), the drum rocking back and forth (wash), spinning fast (spin),
	 * the water pumped out (drain). It shows the agent's call, not the truth: when
	 * the agent is wrong, the drawing is wrong with it.
	 */
	import { larco } from '$lib/api/larco.svelte.js';
	import { STATE_META, stateColor } from '$lib/states.js';
	import { cn } from '$lib/utils';

	/** @type {{class?: string}} */
	let { class: className = '' } = $props();
	const latest = $derived(larco.cycle.windows.findLast((w) => w.served && !w.served.invalid));
	const state = $derived(latest?.served?.state ?? null);
	const tint = $derived(stateColor(state));
	const CX = 160;
	const CY = 172;
	const R = 70;
	/** water level, as a share of the drum's height */
	const level = $derived(
		state === 'spin'
			? 0.08
			: state === 'wash'
				? 0.38
				: state === 'fill' || state === 'drain'
					? 0.3
					: 0.2
	);
</script>

<div class={cn('flex min-h-0 flex-col items-center justify-center gap-2', className)}>
	<svg
		viewBox="0 0 320 310"
		class="h-full max-h-80 w-auto"
		fill="none"
		role="img"
		aria-label="The machine in the state the agent calls: {STATE_META[state ?? '']?.label ??
			'no call yet'}"
	>
		<defs>
			<clipPath id="larco-drum"><circle cx={CX} cy={CY} r={R} /></clipPath>
		</defs>
		<!-- cabinet and control strip -->
		<rect
			x="40"
			y="20"
			width="240"
			height="270"
			rx="10"
			stroke="var(--plate-ink)"
			stroke-width="1.5"
		/>
		<line x1="40" y1="64" x2="280" y2="64" stroke="var(--plate-detail)" />
		<rect x="58" y="34" width="92" height="18" rx="3" stroke="var(--plate-detail)" />
		<text x="104" y="47" text-anchor="middle" class="fill-foreground font-mono text-2xs uppercase"
			>{STATE_META[state ?? '']?.label ?? '—'}</text
		>
		<circle cx="236" cy="43" r="10" stroke="var(--plate-detail)" />
		<circle cx="200" cy="43" r="4" fill={tint} />

		<!-- water inlet (fill) and drain hose (drain) -->
		<path d="M 4 8 H 100 V 20" stroke="var(--plate-detail)" stroke-width="2" />
		{#if state === 'fill'}
			<path
				class="flow"
				d="M 4 8 H 100 V 64 Q 104 96 124 112"
				stroke={tint}
				stroke-width="3"
				stroke-dasharray="6 6"
			/>
		{/if}
		<path d="M 280 272 H 300 V 306" stroke="var(--plate-detail)" stroke-width="2" />
		{#if state === 'drain'}
			<path
				class="flow"
				d="M 206 232 Q 240 268 280 272 H 300 V 306"
				stroke={tint}
				stroke-width="3"
				stroke-dasharray="6 6"
			/>
		{/if}

		<!-- door and drum -->
		<circle cx={CX} cy={CY} r={R + 16} stroke="var(--plate-ink)" stroke-width="1.5" />
		<g clip-path="url(#larco-drum)">
			<rect
				class={state === 'fill' ? 'rise' : state === 'drain' ? 'fall' : ''}
				x={CX - R}
				y={CY + R - 2 * R * level}
				width={2 * R}
				height={2 * R * level + 2}
				fill={tint}
				opacity="0.35"
				style="transform-origin: {CX}px {CY + R}px"
			/>
		</g>
		<g
			class={state === 'spin' ? 'spin' : state === 'wash' ? 'rock' : ''}
			style="transform-origin: {CX}px {CY}px"
		>
			<circle cx={CX} cy={CY} r={R} stroke="var(--plate-ink)" stroke-width="1" />
			{#each [0, 120, 240] as a (a)}
				<rect
					x={CX - 5}
					y={CY - R}
					width="10"
					height="16"
					rx="2"
					fill={tint}
					opacity="0.8"
					transform="rotate({a} {CX} {CY})"
				/>
			{/each}
			{#each [30, 90, 150, 210, 270, 330] as a (a)}
				<circle
					cx={CX + (R - 26) * Math.cos((a * Math.PI) / 180)}
					cy={CY + (R - 26) * Math.sin((a * Math.PI) / 180)}
					r="2"
					fill="var(--plate-detail)"
				/>
			{/each}
		</g>
	</svg>
	<p class="text-center text-xs text-muted-foreground">
		{#if state && STATE_META[state]}
			<span class="text-foreground">{STATE_META[state].label}</span>: {STATE_META[state].what}
		{:else}
			No call yet
		{/if}
	</p>
</div>

<style>
	.rock {
		animation: rock 2.4s ease-in-out infinite;
	}
	.spin {
		animation: spin 0.45s linear infinite;
	}
	.flow {
		animation: flow 0.6s linear infinite;
	}
	.rise {
		animation: rise 6s ease-out infinite;
	}
	.fall {
		animation: rise 6s ease-in infinite reverse;
	}
	@keyframes rock {
		0%,
		100% {
			transform: rotate(-60deg);
		}
		50% {
			transform: rotate(60deg);
		}
	}
	@keyframes spin {
		to {
			transform: rotate(360deg);
		}
	}
	@keyframes flow {
		to {
			stroke-dashoffset: -12;
		}
	}
	@keyframes rise {
		from {
			transform: scaleY(0.3);
		}
		to {
			transform: scaleY(1);
		}
	}
	@media (prefers-reduced-motion: reduce) {
		.rock,
		.spin,
		.flow,
		.rise,
		.fall {
			animation: none;
		}
	}
</style>
