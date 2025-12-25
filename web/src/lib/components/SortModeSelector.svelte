<script>
    import { sortMode, showTooltip, hideTooltip } from '../stores/state.js';

    const options = [
        { value: 'impact', label: 'Impact', tooltip: 'Show nodes with the largest placement impact, positive or negative' },
        { value: 'helpful', label: 'Helpful', tooltip: 'Show nodes that improve your placement the most (negative delta)' },
        { value: 'harmful', label: 'Harmful', tooltip: 'Show nodes that hurt your placement the most (positive delta)' }
    ];

    function handleMouseEnter(event, opt) {
        const rect = event.target.getBoundingClientRect();
        showTooltip(rect.left + rect.width / 2, rect.bottom + 8, {
            type: 'text',
            text: opt.tooltip
        });
    }
</script>

<div class="sort-control" data-walkthrough="sortMode">
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
        white-space: nowrap;
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
