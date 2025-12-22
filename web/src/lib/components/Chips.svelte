<script>
    import { selectedTokens, removeToken } from '../stores/state.js';
    import { getDisplayName } from '../stores/assets.js';
    import { getTokenType } from '../utils/tokens.js';

    function getChipLabel(token) {
        const type = getTokenType(token);

        if (token.startsWith('E:')) {
            const [unit, item] = token.slice(2).split('|');
            return `${getDisplayName('unit', unit)} → ${getDisplayName('item', item)}`;
        }

        return getDisplayName(type, token.slice(2));
    }

    function getChipType(token) {
        if (token.startsWith('E:')) return 'equipped';
        return getTokenType(token);
    }
</script>

<div class="chips-section">
    <div class="section-title">Active Filters</div>
    <div class="chips">
        {#if $selectedTokens.length === 0}
            <div class="empty-message">
                No filters applied. Search and select units, items, or traits to start.
            </div>
        {:else}
            {#each $selectedTokens as token}
                <div class="chip {getChipType(token)}">
                    <span>{getChipLabel(token)}</span>
                    <button on:click={() => removeToken(token)} aria-label="Remove filter">
                        ×
                    </button>
                </div>
            {/each}
        {/if}
    </div>
</div>

<style>
    .chips-section {
        flex: 1;
    }

    .section-title {
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.12em;
        color: var(--text-tertiary);
        margin-bottom: 6px;
    }

    .chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        min-height: 28px;
    }

    .empty-message {
        color: var(--text-tertiary);
        font-size: 12px;
    }

    .chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 5px 10px;
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 5px;
        font-size: 11px;
        font-weight: 500;
        transition: all 0.2s ease;
        position: relative;
        color: var(--text-primary);
    }

    .chip::before {
        content: '';
        position: absolute;
        left: 0;
        top: 0;
        bottom: 0;
        width: 3px;
        border-radius: 6px 0 0 6px;
    }

    .chip.unit::before {
        background: var(--unit);
    }

    .chip.item::before {
        background: var(--item);
    }

    .chip.equipped::before {
        background: var(--equipped);
    }

    .chip.trait::before {
        background: var(--trait);
    }

    .chip:hover {
        border-color: var(--border-hover);
        background: var(--bg-tertiary);
    }

    .chip button {
        background: none;
        border: none;
        color: var(--text-tertiary);
        cursor: pointer;
        font-size: 16px;
        padding: 0;
        line-height: 1;
        width: 16px;
        height: 16px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 3px;
        transition: all 0.15s ease;
    }

    .chip button:hover {
        color: var(--text-primary);
        background: var(--bg-tertiary);
    }
</style>
