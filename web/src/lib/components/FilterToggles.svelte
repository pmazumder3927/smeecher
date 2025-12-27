<script>
    import { activeTypes, toggleType, showTooltip, hideTooltip } from '../stores/state.js';
    import ItemFilterMenu from './ItemFilterMenu.svelte';

    $: unitActive = $activeTypes.has('unit');
    $: itemActive = $activeTypes.has('item');
    $: traitActive = $activeTypes.has('trait');

    function handleMouseEnter(event, text) {
        const rect = event.currentTarget.getBoundingClientRect();
        showTooltip(rect.left + rect.width / 2, rect.bottom + 8, {
            type: 'text',
            text
        });
    }
</script>

<div class="filter-toggles" data-walkthrough="typeToggles">
    <button
        class="filter-toggle unit"
        class:active={unitActive}
        on:click={() => toggleType('unit')}
        on:mouseenter={(e) => handleMouseEnter(e, 'Toggle unit nodes in graph')}
        on:mouseleave={hideTooltip}
    >
        Units
    </button>
    <div class="item-toggle-group">
        <button
            class="filter-toggle item main"
            class:active={itemActive}
            on:click={() => toggleType('item')}
            on:mouseenter={(e) => handleMouseEnter(e, 'Toggle item nodes in graph')}
            on:mouseleave={hideTooltip}
        >
            Items
        </button>
        <div class="item-filter-trigger">
            <ItemFilterMenu compact active={itemActive} />
        </div>
    </div>
    <button
        class="filter-toggle trait"
        class:active={traitActive}
        on:click={() => toggleType('trait')}
        on:mouseenter={(e) => handleMouseEnter(e, 'Toggle trait nodes in graph')}
        on:mouseleave={hideTooltip}
    >
        Traits
    </button>
</div>

<style>
    .filter-toggles {
        display: flex;
        gap: 8px;
    }

    .item-toggle-group {
        display: inline-flex;
        align-items: stretch;
        gap: 0;
    }

    .filter-toggle.main {
        border-top-right-radius: 0;
        border-bottom-right-radius: 0;
    }

    .item-filter-trigger :global(.trigger) {
        border-top-left-radius: 0;
        border-bottom-left-radius: 0;
        margin-left: -1px;
    }

    .filter-toggle {
        height: 28px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        line-height: 1;
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
