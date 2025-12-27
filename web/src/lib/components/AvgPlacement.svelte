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
    $: deltaSign = delta === null ? '' : delta > 0 ? '+' : delta < 0 ? '−' : '';
    $: deltaNum = delta === null ? '' : Math.abs(delta).toFixed(decimals);

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
    <span class="value">{formatted}</span>
    {#if suffix}{suffix}{/if}
    {#if showDelta && delta !== null}
        {#key deltaKey}
            <span class="delta" class:positive={delta < 0} class:negative={delta > 0}>
                <span class="delta-sign">{deltaSign}</span><span class="delta-num">{deltaNum}</span>
            </span>
        {/key}
    {/if}
</span>

<style>
    .avg-placement {
        position: relative;
        display: inline-flex;
        align-items: baseline;
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .value {
        display: inline-block;
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .delta {
        position: absolute;
        top: 100%;
        left: 0;
        right: 0;
        display: flex;
        justify-content: center;
        margin-top: 4px;
        opacity: 0;
        font-size: 11px;
        font-weight: 700;
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
        letter-spacing: -0.01em;
        line-height: 1;
        pointer-events: none;
        white-space: nowrap;
        color: var(--text-tertiary);
        animation: avp-delta var(--avp-delta-ms, 1600ms) cubic-bezier(0.16, 1, 0.3, 1) forwards;
        will-change: transform, opacity;
    }

    .delta-sign {
        position: absolute;
        right: 100%;
    }

    .delta-num {
        position: relative;
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
            transform: translateY(-2px);
        }
        12% {
            opacity: 1;
            transform: translateY(0);
        }
        80% {
            opacity: 1;
            transform: translateY(0);
        }
        100% {
            opacity: 0;
            transform: translateY(2px);
        }
    }

    @media (prefers-reduced-motion: reduce) {
        .delta {
            animation: none;
            opacity: 1;
        }
    }
</style>
