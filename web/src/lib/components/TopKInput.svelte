<script>
    import { topK } from '../stores/state.js';
    import { createEventDispatcher } from 'svelte';

    const dispatch = createEventDispatcher();

    function handleChange() {
        dispatch('change');
    }
</script>

<div class="topk-control">
    <span class="label">Top-K</span>
    <input
        type="number"
        bind:value={$topK}
        on:change={handleChange}
        min="0"
        max="500"
        title="0 = unlimited"
    />
</div>

<style>
    .topk-control {
        display: flex;
        align-items: center;
        gap: 6px;
        white-space: nowrap;
    }

    .label {
        font-size: 10px;
        color: var(--text-tertiary);
        text-transform: uppercase;
        letter-spacing: 0.04em;
        font-weight: 500;
    }

    input {
        width: 50px;
        padding: 4px 6px;
        font-size: 13px;
        font-weight: 600;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        border-radius: 4px;
        color: var(--text-primary);
        text-align: center;
        font-family: inherit;
    }

    input:focus {
        outline: none;
        border-color: var(--accent);
    }
</style>
