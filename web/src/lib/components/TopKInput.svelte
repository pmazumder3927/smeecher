<script>
    import { topK, showTooltip, hideTooltip } from '../stores/state.js';
    import { createEventDispatcher } from 'svelte';

    const dispatch = createEventDispatcher();

    function handleChange() {
        dispatch('change');
    }

    function handleMouseEnter(event) {
        const rect = event.target.getBoundingClientRect();
        showTooltip(rect.left + rect.width / 2, rect.bottom + 8, {
            type: 'text',
            text: 'Maximum number of nodes to display (0 = unlimited)'
        });
    }
</script>

<div class="topk-control" data-walkthrough="limit" on:mouseenter={handleMouseEnter} on:mouseleave={hideTooltip} role="group" aria-label="Top K Control">
    <span class="label">Limit</span>
    <input
        type="number"
        bind:value={$topK}
        on:change={handleChange}
        min="0"
        max="500"
    />
</div>

<style>
    .topk-control {
        display: flex;
        align-items: center;
        gap: 8px;
        white-space: nowrap;
        background: var(--bg-tertiary);
        padding: 2px 6px 2px 8px;
        border-radius: 6px;
        border: 1px solid var(--border);
    }

    .label {
        font-size: 11px;
        color: var(--text-secondary);
        font-weight: 500;
    }

    input {
        width: 40px;
        padding: 4px 0;
        font-size: 12px;
        font-weight: 600;
        background: transparent;
        border: none;
        color: var(--text-primary);
        text-align: center;
        font-family: inherit;
    }

    input:focus {
        outline: none;
    }
</style>
