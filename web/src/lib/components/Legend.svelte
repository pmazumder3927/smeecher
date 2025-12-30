<script>
    import { activeTypes, toggleType } from '../stores/state.js';

    $: unitDisabled = !$activeTypes.has('unit');
    $: itemDisabled = !$activeTypes.has('item');
    $: traitDisabled = !$activeTypes.has('trait');
</script>

<div class="legend">
    <div class="legend-group">
        <div class="legend-group-title">Node Types</div>
        <div class="legend-items">
            <button
                class="legend-item"
                class:disabled={unitDisabled}
                on:click={() => toggleType('unit')}
            >
                <div class="legend-dot unit"></div>
                <span>Unit</span>
            </button>
            <button
                class="legend-item"
                class:disabled={itemDisabled}
                on:click={() => toggleType('item')}
            >
                <div class="legend-dot item"></div>
                <span>Item</span>
            </button>
            <button
                class="legend-item"
                class:disabled={traitDisabled}
                on:click={() => toggleType('trait')}
            >
                <div class="legend-dot trait"></div>
                <span>Trait</span>
            </button>
        </div>
    </div>
    <div class="legend-group">
        <div class="legend-group-title">Placement Impact</div>
        <div class="legend-items">
            <div class="legend-item static">
                <div class="legend-line positive"></div>
                <span>Improves placement</span>
            </div>
            <div class="legend-item static">
                <div class="legend-line negative"></div>
                <span>Worsens placement</span>
            </div>
        </div>
    </div>
</div>

<style>
    .legend {
        position: absolute;
        bottom: 12px;
        left: 12px;
        display: flex;
        flex-wrap: wrap;
        gap: 16px;
        padding: 8px 12px;
        background: rgba(10, 10, 10, 0.85);
        backdrop-filter: blur(8px);
        border: 1px solid var(--border);
        border-radius: 8px;
        font-size: 10px;
        opacity: 0.6;
        transition: opacity 0.2s ease;
        z-index: 10;
    }

    .legend:hover {
        opacity: 1;
    }

    .legend-group {
        display: flex;
        align-items: center;
        gap: 10px;
    }

    .legend-group-title {
        font-size: 9px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        color: var(--text-tertiary);
    }

    .legend-items {
        display: flex;
        gap: 10px;
    }

    .legend-item {
        display: flex;
        align-items: center;
        gap: 5px;
        color: var(--text-tertiary);
        font-size: 10px;
        cursor: pointer;
        transition: opacity 0.2s ease, color 0.15s ease;
        user-select: none;
        background: none;
        border: none;
        padding: 0;
        font-family: inherit;
    }

    .legend-item.static {
        cursor: default;
    }

    .legend-item:hover:not(.static) {
        color: var(--text-primary);
    }

    .legend-item.disabled {
        opacity: 0.4;
        filter: grayscale(100%);
    }

    .legend-dot {
        width: 7px;
        height: 7px;
        border-radius: 50%;
        flex-shrink: 0;
    }

    .legend-dot.unit {
        background: var(--unit);
    }

    .legend-dot.item {
        background: var(--item);
    }

    .legend-dot.trait {
        background: var(--trait);
    }

    .legend-line {
        width: 14px;
        height: 2px;
        flex-shrink: 0;
    }

    .legend-line.positive {
        background: var(--success);
    }

    .legend-line.negative {
        background: var(--error);
    }

    @media (max-width: 768px) {
        .legend {
            bottom: 8px;
            left: 8px;
            gap: 12px;
            padding: 6px 10px;
        }

        .legend-group-title {
            display: none;
        }
    }
</style>
