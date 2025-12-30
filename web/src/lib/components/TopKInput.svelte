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
        gap: 5px;
        white-space: nowrap;
        background: var(--bg-tertiary);
        padding: 2px 4px 2px 6px;
        border-radius: 4px;
        border: 1px solid var(--border);
    }

    .label {
        font-size: 10px;
        color: var(--text-secondary);
        font-weight: 600;
    }

    input {
        width: 36px;
        padding: 2px 0;
        font-size: 11px;
        font-weight: 700;
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
