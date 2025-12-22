<script>
    import { tooltip } from '../stores/state.js';

    $: style = `
        display: ${$tooltip.visible ? 'block' : 'none'};
        left: ${$tooltip.x}px;
        top: ${$tooltip.y}px;
    `;
</script>

<div class="tooltip" {style}>
    {#if $tooltip.content}
        {#if $tooltip.content.type === 'node'}
            <div class="tooltip-title">{$tooltip.content.title}</div>
            <div class="tooltip-row">
                <span class="label">Type</span>
                <span class="value">{$tooltip.content.nodeType}</span>
            </div>
            <div class="tooltip-row">
                <span class="label">Action</span>
                <span class="value">{$tooltip.content.isCenter ? 'Center node' : 'Click to add filter'}</span>
            </div>
        {:else if $tooltip.content.type === 'edge'}
            <div class="tooltip-title">{$tooltip.content.title}</div>
            <div class="tooltip-row">
                <span class="label">Placement Impact</span>
                <span
                    class="value impact"
                    class:positive={$tooltip.content.delta <= 0}
                    class:negative={$tooltip.content.delta > 0}
                >
                    {$tooltip.content.delta > 0 ? '+' : ''}{$tooltip.content.delta.toFixed(2)}
                </span>
            </div>
            <div class="tooltip-row">
                <span class="label">With this combo</span>
                <span class="value">{$tooltip.content.avgWith.toFixed(2)}</span>
            </div>
            <div class="tooltip-row">
                <span class="label">Base average</span>
                <span class="value">{$tooltip.content.avgBase.toFixed(2)}</span>
            </div>
            <div class="tooltip-row">
                <span class="label">Sample size</span>
                <span class="value">
                    {$tooltip.content.nWith.toLocaleString()} / {$tooltip.content.nBase.toLocaleString()}
                </span>
            </div>
        {/if}
    {/if}
</div>

<style>
    .tooltip {
        position: fixed;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 16px;
        font-size: 13px;
        pointer-events: none;
        z-index: 1000;
        max-width: 280px;
        box-shadow: 0 8px 24px var(--shadow);
        backdrop-filter: blur(10px);
    }

    .tooltip-title {
        font-weight: 600;
        margin-bottom: 12px;
        color: var(--text-primary);
        font-size: 14px;
    }

    .tooltip-row {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin: 6px 0;
        gap: 16px;
    }

    .tooltip-row .label {
        color: var(--text-secondary);
        font-size: 12px;
    }

    .tooltip-row .value {
        font-weight: 600;
        color: var(--text-primary);
    }

    .tooltip-row .value.impact {
        font-weight: 800;
        font-size: 15px;
    }

    .tooltip-row .value.positive {
        color: var(--success);
    }

    .tooltip-row .value.negative {
        color: var(--error);
    }
</style>
