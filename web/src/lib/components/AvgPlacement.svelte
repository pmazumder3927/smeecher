<script>
    import { onDestroy } from 'svelte';
    import { getPlacementColor } from '../utils/colors.js';

    export let value;
    export let decimals = 2;
    export let prefix = '';
    export let suffix = '';
    export let showDelta = true;
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
    <span class="value-wrap">
        <span class="value">{formatted}</span>
        {#if showDelta && delta !== null}
            {#key deltaKey}
                <span class="delta" class:positive={delta < 0} class:negative={delta > 0}>
                    {delta > 0 ? '+' : ''}{delta.toFixed(decimals)}
                </span>
            {/key}
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

    .value-wrap {
        position: relative;
        display: inline-block;
        line-height: 1;
    }

    .value {
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .delta {
        position: absolute;
        left: 50%;
        top: 0;
        transform: translate(-50%, 8px);
        opacity: 0;
        font-size: 0.85em;
        font-weight: 900;
        line-height: 1;
        pointer-events: none;
        white-space: nowrap;
        padding: 2px 6px;
        border-radius: 999px;
        background: rgba(0, 0, 0, 0.55);
        border: 1px solid rgba(255, 255, 255, 0.08);
        text-shadow: 0 2px 8px rgba(0, 0, 0, 0.75);
        animation: avp-delta var(--avp-delta-ms, 1600ms) ease-out forwards;
    }

    .delta.positive {
        color: var(--success);
    }

    .delta.negative {
        color: var(--error);
    }

    @keyframes avp-delta {
        0% {
            opacity: 0;
            transform: translate(-50%, 10px) scale(0.96);
        }
        15% {
            opacity: 1;
            transform: translate(-50%, -10px) scale(1);
        }
        60% {
            opacity: 1;
            transform: translate(-50%, -14px) scale(1);
        }
        100% {
            opacity: 0;
            transform: translate(-50%, -20px) scale(1);
        }
    }

    @media (prefers-reduced-motion: reduce) {
        .delta {
            animation: none;
            opacity: 1;
            transform: translate(-50%, -14px);
        }
    }
</style>
