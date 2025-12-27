<script>
    import { onDestroy } from 'svelte';
    import { getPlacementColor } from '../utils/colors.js';

    export let value;
    export let decimals = 2;
    export let prefix = '';
    export let suffix = '';
    export let showDelta = false;
    export let deltaMs = 1600;

    let prevRounded;
    let delta = null;
    let deltaKey = 0;
    let timeoutId;
    let lastShowDelta = showDelta;
    let lastDecimals = decimals;

    function roundTo(num, places) {
        const factor = 10 ** places;
        return Math.round(num * factor) / factor;
    }

    $: placementColor =
        typeof value === 'number' && Number.isFinite(value) ? getPlacementColor(value) : 'inherit';
    $: formatted =
        typeof value === 'number' && Number.isFinite(value) ? roundTo(value, decimals).toFixed(decimals) : '—';
    $: deltaFormatted =
        delta === null ? '' : `${delta > 0 ? '+' : ''}${delta.toFixed(decimals)}`;

    $: {
        if (decimals !== lastDecimals) {
            lastDecimals = decimals;
            prevRounded = undefined;
            delta = null;
        }

        const justEnabled = showDelta && !lastShowDelta;
        lastShowDelta = showDelta;

        if (justEnabled) {
            if (typeof value === 'number' && Number.isFinite(value)) {
                prevRounded = roundTo(value, decimals);
            } else {
                prevRounded = undefined;
            }
            delta = null;
            if (timeoutId) {
                clearTimeout(timeoutId);
                timeoutId = undefined;
            }
        } else if (typeof value !== 'number' || !Number.isFinite(value)) {
            prevRounded = undefined;
            delta = null;
            if (timeoutId) {
                clearTimeout(timeoutId);
                timeoutId = undefined;
            }
        } else {
            const nextRounded = roundTo(value, decimals);
            if (prevRounded === undefined) {
                prevRounded = nextRounded;
            } else if (nextRounded !== prevRounded) {
                const nextDelta = roundTo(nextRounded - prevRounded, decimals);
                prevRounded = nextRounded;

                if (showDelta && nextDelta !== 0) {
                    delta = nextDelta;
                    deltaKey += 1;

                    if (timeoutId) clearTimeout(timeoutId);
                    timeoutId = setTimeout(() => {
                        delta = null;
                    }, deltaMs);
                }
            }
        }
    }

    onDestroy(() => {
        if (timeoutId) clearTimeout(timeoutId);
    });
</script>

<span class="avg-placement" style={`color: ${placementColor}; --avp-delta-ms: ${deltaMs}ms;`}>
    {#if prefix}{prefix}{/if}
    <span class="value-layout" class:delta-enabled={showDelta}>
        {#if showDelta}
            <span class="delta-spacer" aria-hidden="true"></span>
        {/if}
        <span class="value">{formatted}</span>
        {#if showDelta}
            <span class="delta-slot" aria-hidden={delta === null}>
                {#if delta !== null}
                    {#key deltaKey}
                        <span class="delta" class:positive={delta < 0} class:negative={delta > 0}>
                            {deltaFormatted}
                        </span>
                    {/key}
                {/if}
            </span>
        {/if}
    </span>
    {#if suffix}{suffix}{/if}
</span>

<style>
    .avg-placement {
        display: inline-flex;
        align-items: baseline;
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .value-layout {
        display: inline-flex;
        align-items: baseline;
        line-height: 1;
    }

    .value-layout.delta-enabled {
        gap: 0.6ch;
    }

    .value {
        display: inline-block;
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .delta-spacer,
    .delta-slot {
        display: inline-block;
        width: 5ch;
    }

    .delta {
        display: inline-block;
        opacity: 0;
        font-size: 0.72em;
        font-weight: 800;
        letter-spacing: -0.01em;
        line-height: 1;
        pointer-events: none;
        white-space: nowrap;
        color: var(--text-tertiary);
        text-shadow: 0 1px 2px rgba(0, 0, 0, 0.45);
        animation: avp-delta-inline var(--avp-delta-ms, 1600ms) cubic-bezier(0.16, 1, 0.3, 1) forwards;
        will-change: transform, opacity;
    }

    .delta.positive {
        color: var(--success);
    }

    .delta.negative {
        color: var(--error);
    }

    @keyframes avp-delta-inline {
        0% {
            opacity: 0;
            transform: translateY(2px);
        }
        15% {
            opacity: 1;
            transform: translateY(0);
        }
        82% {
            opacity: 1;
            transform: translateY(0);
        }
        100% {
            opacity: 0;
            transform: translateY(-1px);
        }
    }

    @media (prefers-reduced-motion: reduce) {
        .delta {
            animation: none;
            opacity: 1;
            transform: none;
        }
    }
</style>
