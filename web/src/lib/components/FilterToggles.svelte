<script>
    import { activeTypes, toggleType } from '../stores/state.js';

    $: unitActive = $activeTypes.has('unit');
    $: itemActive = $activeTypes.has('item');
    $: traitActive = $activeTypes.has('trait');
</script>

<div class="filter-toggles">
    <button
        class="filter-toggle unit"
        class:active={unitActive}
        on:click={() => toggleType('unit')}
    >
        Units
    </button>
    <button
        class="filter-toggle item"
        class:active={itemActive}
        on:click={() => toggleType('item')}
    >
        Items
    </button>
    <button
        class="filter-toggle trait"
        class:active={traitActive}
        on:click={() => toggleType('trait')}
    >
        Traits
    </button>
</div>

<style>
    .filter-toggles {
        display: flex;
        gap: 8px;
    }

    .filter-toggle {
        padding: 6px 10px;
        border-radius: 5px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        color: var(--text-secondary);
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.2s ease;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        font-family: inherit;
    }

    .filter-toggle:hover {
        border-color: var(--border-hover);
        color: var(--text-primary);
    }

    .filter-toggle.active {
        border-color: transparent;
    }

    .filter-toggle.active.unit {
        background: var(--unit);
        color: #000;
    }

    .filter-toggle.active.item {
        background: var(--item);
        color: #000;
    }

    .filter-toggle.active.trait {
        background: var(--trait);
        color: #fff;
    }

    @media (max-width: 768px) {
        .filter-toggles {
            order: 2;
            flex: 1;
        }
    }
</style>
