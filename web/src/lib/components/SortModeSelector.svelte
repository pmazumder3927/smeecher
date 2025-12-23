<script>
    import { sortMode, showTooltip, hideTooltip } from '../stores/state.js';

    const options = [
        { value: 'impact', label: 'Most Impactful', tooltip: 'Show nodes with the largest placement impact, positive or negative' },
        { value: 'helpful', label: 'Most Helpful', tooltip: 'Show nodes that improve your placement the most (negative delta)' },
        { value: 'harmful', label: 'Least Helpful', tooltip: 'Show nodes that hurt your placement the most (positive delta)' }
    ];

    function handleMouseEnter(event, opt) {
        const rect = event.target.getBoundingClientRect();
        showTooltip(rect.left + rect.width / 2, rect.bottom + 8, {
            type: 'text',
            text: opt.tooltip
        });
    }
</script>

<div class="sort-control">
    <span class="label">Show</span>
    <div class="button-group">
        {#each options as opt}
            <button
                class:active={$sortMode === opt.value}
                on:click={() => sortMode.set(opt.value)}
                on:mouseenter={(e) => handleMouseEnter(e, opt)}
                on:mouseleave={hideTooltip}
            >
                {opt.label}
            </button>
        {/each}
    </div>
</div>

<style>
    .sort-control {
        display: flex;
        align-items: center;
        gap: 8px;
        white-space: nowrap;
    }

    .label {
        font-size: 10px;
        color: var(--text-tertiary);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        font-weight: 500;
    }

    .button-group {
        display: flex;
        gap: 2px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        border-radius: 6px;
        padding: 2px;
    }

    button {
        padding: 4px 10px;
        font-size: 11px;
        font-weight: 500;
        background: transparent;
        border: none;
        border-radius: 4px;
        color: var(--text-secondary);
        cursor: pointer;
        transition: all 0.15s ease;
        font-family: inherit;
    }

    button:hover {
        color: var(--text-primary);
        background: var(--bg-secondary);
    }

    button.active {
        background: var(--accent);
        color: white;
    }
</style>
