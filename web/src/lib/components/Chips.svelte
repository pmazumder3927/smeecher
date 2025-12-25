<script>
    import { onMount, tick } from 'svelte';
    import { selectedTokens, removeToken, clearTokens, equipItemOnUnit, removeUnitFilters } from '../stores/state.js';
    import { getDisplayName } from '../stores/assets.js';
    import { getTokenType, parseToken } from '../utils/tokens.js';
    import { getSearchIndex } from '../utils/searchIndexCache.js';

    let equipOpenUnit = null;
    let equipQuery = '';
    let equipResults = [];
    let equipSelectedIndex = -1;
    let equipInputEl = null;

    let itemIndex = [];
    let itemsReady = false;
    let itemsError = null;

    function normalizeSearchText(text) {
        return text.toLowerCase().replace(/[^a-z0-9]/g, '');
    }

    function scoreMatch(entry, qNorm) {
        if (entry.labelNorm === qNorm || entry.tokenSuffixNorm === qNorm) return 0;
        if (entry.labelNorm.startsWith(qNorm) || entry.tokenSuffixNorm.startsWith(qNorm)) return 1;
        return 2;
    }

    function searchItems(query) {
        const qNorm = normalizeSearchText(query);
        if (!qNorm) return itemIndex.slice(0, 14);

        const matches = [];
        for (const entry of itemIndex) {
            if (entry.labelNorm.includes(qNorm) || entry.tokenSuffixNorm.includes(qNorm)) {
                matches.push(entry);
            }
        }

        matches.sort((a, b) => {
            const sa = scoreMatch(a, qNorm);
            const sb = scoreMatch(b, qNorm);
            if (sa !== sb) return sa - sb;
            if (a.count !== b.count) return b.count - a.count;
            return a.label.length - b.label.length;
        });

        return matches.slice(0, 20);
    }

    function isEquipped(unit, itemName) {
        const prefix = `E:${unit}|`;
        return $selectedTokens.includes(`${prefix}${itemName}`);
    }

    function openEquip(unit) {
        equipOpenUnit = unit;
        equipQuery = '';
        equipSelectedIndex = 0;
        equipResults = searchItems(equipQuery);
        tick().then(() => equipInputEl?.focus?.());
    }

    function closeEquip() {
        equipOpenUnit = null;
        equipQuery = '';
        equipResults = [];
        equipSelectedIndex = -1;
    }

    function handleEquipInput() {
        equipResults = searchItems(equipQuery);
        if (equipResults.length > 0 && (equipSelectedIndex < 0 || equipSelectedIndex >= equipResults.length)) {
            equipSelectedIndex = 0;
        }
    }

    function handleEquipKeydown(event, unit) {
        if (!equipOpenUnit) return;

        if (event.key === 'ArrowDown') {
            event.preventDefault();
            equipSelectedIndex = Math.min(equipSelectedIndex + 1, equipResults.length - 1);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            equipSelectedIndex = Math.max(equipSelectedIndex - 1, 0);
        } else if (event.key === 'Enter') {
            event.preventDefault();
            if (equipSelectedIndex < 0 || equipSelectedIndex >= equipResults.length) return;
            const entry = equipResults[equipSelectedIndex];
            const itemName = entry.token.slice(2);
            if (!isEquipped(unit, itemName)) {
                equipItemOnUnit(unit, itemName, 'equip_ui');
            }
            equipQuery = '';
            handleEquipInput();
            tick().then(() => equipInputEl?.focus?.());
        } else if (event.key === 'Escape') {
            event.preventDefault();
            closeEquip();
        }
    }

    function handleDocClick(event) {
        if (!equipOpenUnit) return;
        if (event.target.closest('.equip-popover')) return;
        if (event.target.closest('.equip-btn')) return;
        closeEquip();
    }

    onMount(async () => {
        try {
            const index = await getSearchIndex();
            itemIndex = index
                .filter((e) => e.type === 'item')
                .map((e) => ({
                    ...e,
                    labelNorm: normalizeSearchText(e.label),
                    tokenSuffixNorm: normalizeSearchText(e.token.slice(2)),
                    count: e.count || 0,
                }))
                .sort((a, b) => b.count - a.count);
            itemsReady = true;
            equipResults = searchItems(equipQuery);
        } catch (e) {
            itemsError = e?.message ?? String(e);
        }
    });

    function getChipLabel(token) {
        const type = getTokenType(token);
        return getDisplayName(type, token.slice(2));
    }

    function getChipType(token) {
        return getTokenType(token);
    }

    function buildUnitGroups(tokens) {
        const byUnit = new Map();

        for (const token of tokens) {
            const parsed = parseToken(token);
            if (parsed.type === 'unit') {
                const unit = parsed.unit;
                if (!byUnit.has(unit)) byUnit.set(unit, { unit, equipped: [] });
            } else if (parsed.type === 'equipped') {
                const unit = parsed.unit;
                const item = parsed.item;
                if (!byUnit.has(unit)) byUnit.set(unit, { unit, equipped: [] });
                byUnit.get(unit).equipped.push({ token, item });
            }
        }

        for (const group of byUnit.values()) {
            group.equipped.sort((a, b) =>
                getDisplayName('item', a.item).localeCompare(getDisplayName('item', b.item))
            );
        }

        return Array.from(byUnit.values()).sort((a, b) =>
            getDisplayName('unit', a.unit).localeCompare(getDisplayName('unit', b.unit))
        );
    }

    $: unitGroups = buildUnitGroups($selectedTokens);
    $: otherTokens = $selectedTokens.filter((t) => {
        const type = getTokenType(t);
        return type !== 'unit' && type !== 'equipped';
    });
</script>

<svelte:document on:click={handleDocClick} />

<div class="chips-section" data-walkthrough="filters">
    <div class="section-title">Active Filters</div>
    <div class="chips">
        {#if $selectedTokens.length === 0}
            <div class="empty-message">
                No filters applied. Search and select units, items, or traits to start.
                <span class="empty-hint">Tip: add a unit, then click + to equip items.</span>
            </div>
        {:else}
            {#each unitGroups as group (group.unit)}
                <div class="unit-group">
                    <div class="chip unit unit-chip">
                        <span>{getDisplayName('unit', group.unit)}</span>
                        <div class="unit-actions">
                            <button
                                class="equip-btn"
                                on:click={() => openEquip(group.unit)}
                                aria-label="Equip item"
                                title="Equip item"
                            >
                                +
                            </button>
                            <button
                                class="remove-group-btn"
                                on:click={() => removeUnitFilters(group.unit)}
                                aria-label="Remove unit and equipped items"
                                title="Remove unit and equipped items"
                            >
                                ×
                            </button>
                        </div>
                    </div>

                    {#if group.equipped.length > 0}
                        <div class="equipped-row">
                            {#each group.equipped as eq (eq.token)}
                                <div class="equip-chip">
                                    <span class="equip-label">{getDisplayName('item', eq.item)}</span>
                                    <button on:click={() => removeToken(eq.token)} aria-label="Remove equipped item">×</button>
                                </div>
                            {/each}
                        </div>
                    {/if}

                    {#if equipOpenUnit === group.unit}
                        <div class="equip-popover">
                            <div class="equip-title">
                                Equip <span class="equip-unit">{getDisplayName('unit', group.unit)}</span>
                            </div>

                            {#if itemsError}
                                <div class="equip-error">Failed to load items: {itemsError}</div>
                            {:else if !itemsReady}
                                <div class="equip-loading">Loading items…</div>
                            {:else}
                                <input
                                    class="equip-input"
                                    bind:value={equipQuery}
                                    bind:this={equipInputEl}
                                    on:input={handleEquipInput}
                                    on:keydown={(e) => handleEquipKeydown(e, group.unit)}
                                    placeholder="Search items…"
                                    autocomplete="off"
                                />

                                <div class="equip-results">
                                    {#each equipResults as item, i (item.token)}
                                        {@const itemName = item.token.slice(2)}
                                        {@const already = isEquipped(group.unit, itemName)}
                                        <button
                                            class="equip-result"
                                            class:selected={i === equipSelectedIndex}
                                            disabled={already}
                                            on:click={() => {
                                                if (!already) equipItemOnUnit(group.unit, itemName, 'equip_ui');
                                                equipQuery = '';
                                                handleEquipInput();
                                                tick().then(() => equipInputEl?.focus?.());
                                            }}
                                            title={already ? 'Already equipped' : 'Equip item'}
                                        >
                                            <span class="equip-name">{getDisplayName('item', item.label)}</span>
                                            {#if already}
                                                <span class="equip-status">Added</span>
                                            {:else if item.count > 0}
                                                <span class="equip-count">{item.count.toLocaleString()} games</span>
                                            {/if}
                                        </button>
                                    {/each}
                                    {#if equipResults.length === 0}
                                        <div class="equip-no-results">No items found</div>
                                    {/if}
                                </div>

                                <div class="equip-hint">
                                    <kbd>Enter</kbd> equips • <kbd>Esc</kbd> closes
                                </div>
                            {/if}
                        </div>
                    {/if}
                </div>
            {/each}

            {#each otherTokens as token (token)}
                <div class="chip {getChipType(token)}">
                    <span>{getChipLabel(token)}</span>
                    <button on:click={() => removeToken(token)} aria-label="Remove filter">
                        ×
                    </button>
                </div>
            {/each}
            <div class="chip clear-all">
                <span>Clear all ({$selectedTokens.length})</span>
                <button on:click={clearTokens} aria-label="Clear all filters">
                    ×
                </button>
            </div>
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

    .empty-hint {
        display: block;
        margin-top: 6px;
        color: var(--text-secondary);
        font-size: 11px;
        opacity: 0.8;
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

    .chip.clear-all {
        color: rgba(255, 68, 68, 0.95);
        border-style: dashed;
        border-color: rgba(255, 68, 68, 0.35);
        background: rgba(255, 68, 68, 0.07);
    }

    .chip.clear-all::before {
        background: var(--error);
    }

    .chip.clear-all:hover {
        border-color: rgba(255, 68, 68, 0.6);
        background: rgba(255, 68, 68, 0.11);
        color: rgba(255, 68, 68, 0.98);
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

    .chip.clear-all button:hover {
        color: rgba(255, 255, 255, 0.95);
        background: rgba(255, 68, 68, 0.18);
    }

    .unit-group {
        position: relative;
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
    }

    .unit-chip {
        padding-right: 6px;
        gap: 8px;
    }

    .unit-actions {
        display: inline-flex;
        gap: 4px;
        align-items: center;
        margin-left: 2px;
    }

    .equip-btn,
    .remove-group-btn {
        background: none;
        border: none;
        color: var(--text-tertiary);
        cursor: pointer;
        font-size: 14px;
        padding: 0;
        line-height: 1;
        width: 18px;
        height: 18px;
        display: flex;
        align-items: center;
        justify-content: center;
        border-radius: 4px;
        transition: all 0.15s ease;
        flex-shrink: 0;
    }

    .equip-btn:hover {
        color: var(--text-primary);
        background: rgba(245, 166, 35, 0.14);
    }

    .remove-group-btn:hover {
        color: var(--text-primary);
        background: var(--bg-tertiary);
    }

    .equipped-row {
        display: inline-flex;
        gap: 6px;
        flex-wrap: wrap;
        align-items: center;
    }

    .equip-chip {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 5px 10px;
        border-radius: 999px;
        border: 1px solid rgba(245, 166, 35, 0.28);
        background: rgba(245, 166, 35, 0.08);
        font-size: 11px;
        font-weight: 600;
        color: var(--text-primary);
        position: relative;
    }

    .equip-chip::before {
        content: '';
        width: 6px;
        height: 6px;
        border-radius: 50%;
        background: var(--equipped);
        opacity: 0.95;
    }

    .equip-chip button {
        background: none;
        border: none;
        color: rgba(255, 255, 255, 0.55);
        cursor: pointer;
        font-size: 14px;
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

    .equip-chip button:hover {
        color: rgba(255, 255, 255, 0.9);
        background: rgba(0, 0, 0, 0.25);
    }

    .equip-popover {
        position: absolute;
        top: calc(100% + 8px);
        left: 0;
        width: min(360px, 92vw);
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 10px;
        box-shadow: 0 10px 38px rgba(0, 0, 0, 0.55);
        z-index: 50;
        padding: 10px;
    }

    .equip-title {
        font-size: 11px;
        font-weight: 800;
        letter-spacing: 0.06em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        margin-bottom: 8px;
        display: flex;
        gap: 6px;
        align-items: baseline;
    }

    .equip-unit {
        color: var(--text-primary);
        letter-spacing: 0.02em;
    }

    .equip-loading,
    .equip-error {
        font-size: 12px;
        color: var(--text-secondary);
        padding: 6px 2px;
    }

    .equip-input {
        width: 100%;
        padding: 8px 10px;
        font-size: 12px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        border-radius: 8px;
        color: var(--text-primary);
        outline: none;
        font-family: inherit;
        margin-bottom: 8px;
    }

    .equip-input:focus {
        border-color: rgba(245, 166, 35, 0.6);
        box-shadow: 0 6px 18px rgba(245, 166, 35, 0.12);
    }

    .equip-results {
        max-height: 240px;
        overflow: auto;
        border: 1px solid var(--border);
        border-radius: 8px;
        background: rgba(0, 0, 0, 0.12);
    }

    .equip-result {
        width: 100%;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        padding: 9px 10px;
        border: none;
        background: transparent;
        color: var(--text-primary);
        cursor: pointer;
        text-align: left;
        font-family: inherit;
        border-bottom: 1px solid var(--border);
    }

    .equip-result:last-child {
        border-bottom: none;
    }

    .equip-result:hover:not(:disabled),
    .equip-result.selected:not(:disabled) {
        background: rgba(245, 166, 35, 0.08);
    }

    .equip-result:disabled {
        cursor: default;
        opacity: 0.55;
    }

    .equip-name {
        font-size: 12px;
        font-weight: 650;
    }

    .equip-count,
    .equip-status {
        font-size: 11px;
        color: var(--text-secondary);
        white-space: nowrap;
    }

    .equip-status {
        color: rgba(0, 217, 165, 0.95);
        font-weight: 700;
    }

    .equip-no-results {
        padding: 10px;
        font-size: 12px;
        color: var(--text-tertiary);
    }

    .equip-hint {
        display: flex;
        gap: 8px;
        align-items: center;
        justify-content: flex-end;
        margin-top: 8px;
        font-size: 11px;
        color: var(--text-tertiary);
    }

    .equip-hint kbd {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        padding: 1px 6px;
        border-radius: 6px;
        border: 1px solid var(--border);
        background: rgba(0, 0, 0, 0.25);
        font-size: 11px;
        font-weight: 650;
        color: var(--text-secondary);
        line-height: 1.4;
    }
</style>
