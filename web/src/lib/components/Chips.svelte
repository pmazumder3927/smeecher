<script>
    import { onMount, tick } from 'svelte';
    import { selectedTokens, removeToken, clearTokens, equipItemOnUnit, excludeItemOnUnit, removeUnitFilters, setUnitStarFilter } from '../stores/state.js';
    import { getDisplayName, getIconUrl, hasIconFailed, markIconFailed } from '../stores/assets.js';
    import { getTokenType, getTokenLabel, parseToken } from '../utils/tokens.js';
    import { getSearchIndex } from '../utils/searchIndexCache.js';

    let equipOpenKey = null;
    let equipOpenUnit = null;
    let equipOpenNegated = false;
    let equipQuery = '';
    let equipResults = [];
    let equipSelectedIndex = -1;
    let equipInputEl = null;

    let starOpenKey = null;
    let starFirstOptionEl = null;

    let itemIndex = [];
    let equippedCountIndex = new Map();
    let tokenLabelIndex = new Map();
    let unitStarsIndex = new Map();
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
        const unit = equipOpenUnit;
        const qNorm = normalizeSearchText(query);
        const limit = qNorm ? 20 : 14;

        const matches = [];
        for (const entry of itemIndex) {
            if (!qNorm || entry.labelNorm.includes(qNorm) || entry.tokenSuffixNorm.includes(qNorm)) {
                matches.push(entry);
            }
        }

        matches.sort((a, b) => {
            if (qNorm) {
                const sa = scoreMatch(a, qNorm);
                const sb = scoreMatch(b, qNorm);
                if (sa !== sb) return sa - sb;
            }
            if (unit) {
                const ca = getEquippedCount(unit, a.apiName);
                const cb = getEquippedCount(unit, b.apiName);
                if (ca !== cb) return cb - ca;
            }
            if (a.count !== b.count) return b.count - a.count;
            return a.label.length - b.label.length;
        });

        return matches.slice(0, limit);
    }

    function isEquipped(unit, itemName, negated = false) {
        const prefix = `${negated ? '-' : ''}E:${unit}|`;
        const altPrefix = `${negated ? '!' : ''}E:${unit}|`;
        return (
            $selectedTokens.includes(`${prefix}${itemName}`) ||
            (negated && $selectedTokens.includes(`${altPrefix}${itemName}`))
        );
    }

    function getEquippedCount(unit, itemName) {
        return equippedCountIndex.get(`${unit}|${itemName}`) ?? 0;
    }

    function openEquip(group) {
        starOpenKey = null;
        equipOpenKey = group.key;
        equipOpenUnit = group.unit;
        equipOpenNegated = group.negated;
        equipQuery = '';
        equipSelectedIndex = 0;
        equipResults = searchItems(equipQuery);
        tick().then(() => equipInputEl?.focus?.());
    }

    function closeEquip() {
        equipOpenKey = null;
        equipOpenUnit = null;
        equipOpenNegated = false;
        equipQuery = '';
        equipResults = [];
        equipSelectedIndex = -1;
    }

    function openStarMenu(group) {
        starOpenKey = group.key;
        tick().then(() => starFirstOptionEl?.focus?.());
    }

    function toggleStarMenu(group) {
        if (starOpenKey === group.key) {
            starOpenKey = null;
        } else {
            openStarMenu(group);
        }
    }

    function closeStarMenu() {
        starOpenKey = null;
    }

    function selectStar(unit, stars, negated = false) {
        setUnitStarFilter(unit, stars, 'ui', { negated });
        closeStarMenu();
    }

    function handleStarKeydown(event, group) {
        if (event.key === 'Escape') {
            event.preventDefault();
            closeStarMenu();
            return;
        }
        if (event.key === 'Enter' || event.key === ' ') {
            event.preventDefault();
            toggleStarMenu(group);
        } else if (event.key === 'ArrowDown') {
            event.preventDefault();
            openStarMenu(group);
        }
    }

    function handleEquipInput() {
        equipResults = searchItems(equipQuery);
        if (equipResults.length > 0 && (equipSelectedIndex < 0 || equipSelectedIndex >= equipResults.length)) {
            equipSelectedIndex = 0;
        }
    }

    function handleEquipKeydown(event, unit, negated) {
        if (!equipOpenKey) return;

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
            if (!isEquipped(unit, itemName, negated)) {
                if (negated) excludeItemOnUnit(unit, itemName, 'equip_ui');
                else equipItemOnUnit(unit, itemName, 'equip_ui');
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
        if (equipOpenUnit) {
            if (!event.target.closest('.equip-popover') && !event.target.closest('.equip-trigger')) {
                closeEquip();
            }
        }

        if (starOpenKey) {
            if (!event.target.closest('.star-popover') && !event.target.closest('.star-trigger')) {
                closeStarMenu();
            }
        }
    }

    onMount(async () => {
        try {
            const index = await getSearchIndex();
            tokenLabelIndex = new Map(index.map((e) => [e.token, e.label]));

            unitStarsIndex = new Map();
            for (const e of index) {
                if (e.type !== 'unit' || typeof e.token !== 'string' || !e.token.startsWith('U:')) continue;
                const parsed = parseToken(e.token);
                if (parsed.type !== 'unit' || !parsed.unit || !parsed.stars) continue;
                if (!unitStarsIndex.has(parsed.unit)) unitStarsIndex.set(parsed.unit, new Set());
                unitStarsIndex.get(parsed.unit).add(parsed.stars);
            }

            equippedCountIndex = new Map();
            for (const e of index) {
                if (e.type !== 'equipped' || typeof e.token !== 'string' || !e.token.startsWith('E:')) continue;
                const rest = e.token.slice(2);
                const [unit, item] = rest.split('|');
                if (!unit || !item) continue;
                equippedCountIndex.set(`${unit}|${item}`, e.count || 0);
            }

            itemIndex = index
                .filter((e) => e.type === 'item')
                .map((e) => ({
                    ...e,
                    apiName: e.token.slice(2),
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
        const parsed = parseToken(token);
        const baseToken = parsed?.negated ? token.slice(1) : token;
        const label = tokenLabelIndex.get(baseToken);
        if (label) return parsed?.negated ? `Not ${label}` : label;
        return getTokenLabel(token, getDisplayName);
    }

    function getChipType(token) {
        return getTokenType(token);
    }

    function itemIcon(itemName) {
        return getIconUrl('item', itemName);
    }

    function buildUnitGroups(tokens) {
        const byUnit = new Map();

        for (const token of tokens) {
            const parsed = parseToken(token);
            if (parsed.type === 'unit') {
                const unit = parsed.unit;
                const key = `${parsed.negated ? '-' : ''}${unit}`;
                if (!byUnit.has(key)) byUnit.set(key, { key, unit, negated: !!parsed.negated, unitToken: `${parsed.negated ? '-' : ''}U:${unit}`, stars: null, equipped: [] });
                const group = byUnit.get(key);
                if (parsed.stars) {
                    if (!group.stars || parsed.stars > group.stars) {
                        group.stars = parsed.stars;
                    }
                }
            } else if (parsed.type === 'equipped') {
                const unit = parsed.unit;
                const item = parsed.item;
                const key = `${parsed.negated ? '-' : ''}${unit}`;
                if (!byUnit.has(key)) byUnit.set(key, { key, unit, negated: !!parsed.negated, unitToken: `${parsed.negated ? '-' : ''}U:${unit}`, stars: null, equipped: [] });
                byUnit.get(key).equipped.push({ token, item });
            }
        }

        for (const group of byUnit.values()) {
            group.equipped.sort((a, b) =>
                getDisplayName('item', a.item).localeCompare(getDisplayName('item', b.item))
            );
        }

        return Array.from(byUnit.values()).sort((a, b) => {
            const la = getDisplayName('unit', a.unit);
            const lb = getDisplayName('unit', b.unit);
            const cmp = la.localeCompare(lb);
            if (cmp !== 0) return cmp;
            if (a.negated !== b.negated) return a.negated ? 1 : -1;
            return 0;
        });
    }

    function getStarOptions(unit, selectedStars) {
        const set = unitStarsIndex.get(unit);
        const options = set ? Array.from(set) : [1, 2, 3];
        if (selectedStars && !options.includes(selectedStars)) options.push(selectedStars);
        return options
            .filter((n) => Number.isFinite(n) && n >= 1)
            .sort((a, b) => a - b);
    }

    $: unitGroups = buildUnitGroups($selectedTokens);
    $: otherTokens = $selectedTokens.filter((t) => {
        const parsed = parseToken(t);
        return parsed.type !== 'unit' && parsed.type !== 'equipped';
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
	            {#each unitGroups as group (group.key)}
	                <div class="unit-group">
	                    <div class="chip unit champion-chip" class:excluded={group.negated}>
	                        <div class="champion-header">
	                            <span class="champion-name">{getChipLabel(group.unitToken)}</span>
	                            <div class="champion-stars">
	                                <button
	                                    type="button"
	                                    class="star-trigger"
	                                    class:open={starOpenKey === group.key}
	                                    aria-label="Star level filter"
	                                    aria-haspopup="listbox"
	                                    aria-expanded={starOpenKey === group.key}
	                                    on:click={() => toggleStarMenu(group)}
	                                    on:keydown={(e) => handleStarKeydown(e, group)}
	                                >
	                                    <span class="star-value">
	                                        {#if group.stars}
	                                            {group.stars}★
	                                        {:else}
                                            Any
                                        {/if}
                                    </span>
	                                    <span class="star-chevron" aria-hidden="true">▾</span>
	                                </button>

	                                {#if starOpenKey === group.key}
	                                    <div
	                                        class="star-popover"
	                                        role="listbox"
	                                        tabindex="-1"
                                        aria-label="Star level filter"
                                        on:keydown={(e) => {
                                            if (e.key === 'Escape') {
                                                e.preventDefault();
                                                closeStarMenu();
                                            }
                                        }}
                                    >
                                        <button
                                            bind:this={starFirstOptionEl}
                                            type="button"
                                            role="option"
	                                            class="star-option"
	                                            class:selected={!group.stars}
	                                            aria-selected={!group.stars}
	                                            on:click={() => selectStar(group.unit, null, group.negated)}
	                                        >
	                                            <span class="star-option-label">Any</span>
	                                            {#if !group.stars}
	                                                <span class="star-option-check" aria-hidden="true">✓</span>
                                            {/if}
                                        </button>

                                        {#each getStarOptions(group.unit, group.stars) as s (s)}
                                            <button
                                                type="button"
	                                                role="option"
	                                                class="star-option"
	                                                class:selected={group.stars === s}
	                                                aria-selected={group.stars === s}
	                                                on:click={() => selectStar(group.unit, s, group.negated)}
	                                            >
	                                                <span class="star-option-label">{s}★</span>
	                                                {#if group.stars === s}
	                                                    <span class="star-option-check" aria-hidden="true">✓</span>
                                                {/if}
                                            </button>
                                        {/each}
                                    </div>
                                {/if}
                            </div>
                        </div>

                        <div class="champion-items">
                            {#each group.equipped as eq (eq.token)}
                                <button
                                    class="champion-item"
                                    on:click={() => removeToken(eq.token)}
                                    aria-label={`Remove ${getDisplayName('item', eq.item)} from ${getDisplayName('unit', group.unit)}`}
                                    title={`Remove ${getDisplayName('item', eq.item)}`}
                                >
                                    {#if itemIcon(eq.item) && !hasIconFailed('item', eq.item)}
                                        <img
                                            class="champion-item-icon"
                                            src={itemIcon(eq.item)}
                                            alt=""
                                            on:error={() => markIconFailed('item', eq.item)}
                                        />
                                    {:else}
                                        <span class="champion-item-fallback">
                                            {getDisplayName('item', eq.item).slice(0, 2)}
                                        </span>
                                    {/if}
                                    <span class="champion-item-x">×</span>
                                </button>
                            {/each}

	                            <button
	                                data-walkthrough="equip"
	                                class="champion-item-add equip-trigger"
	                                on:click={() => openEquip(group)}
	                                aria-label={group.negated ? 'Exclude items on this unit filter' : 'Equip items on this unit filter'}
	                                title={group.equipped.length > 0
	                                    ? group.negated
	                                        ? 'Exclude another item'
	                                        : 'Add item'
	                                    : group.negated
	                                        ? 'Exclude items'
	                                        : 'Equip items'}
	                            >
	                                <span class="champion-item-add-plus">+</span>
	                            </button>
	                        </div>

	                        <button
	                            class="remove-group-btn"
	                            on:click={() => {
	                                removeUnitFilters(group.unit, 'ui', { negated: group.negated });
	                                if (equipOpenKey === group.key) closeEquip();
	                                if (starOpenKey === group.key) closeStarMenu();
	                            }}
	                            aria-label={group.negated ? 'Remove excluded unit filters' : 'Remove unit and equipped items'}
	                            title={group.negated ? 'Remove excluded unit filters' : 'Remove unit and equipped items'}
	                        >
	                            ×
	                        </button>
	                    </div>

	                    {#if equipOpenKey === group.key}
	                        <div class="equip-popover">
	                            <div class="equip-title">
	                                {group.negated ? 'Exclude' : 'Equip'} <span class="equip-unit">{getDisplayName('unit', group.unit)}</span>
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
	                                    on:keydown={(e) => handleEquipKeydown(e, group.unit, group.negated)}
	                                    placeholder="Search items…"
	                                    autocomplete="off"
	                                />

	                                <div class="equip-results">
	                                    {#each equipResults as item, i (item.token)}
	                                        {@const itemName = item.apiName}
	                                        {@const already = isEquipped(group.unit, itemName, group.negated)}
	                                        {@const eqCount = getEquippedCount(group.unit, itemName)}
	                                        <button
	                                            class="equip-result"
	                                            class:selected={i === equipSelectedIndex}
	                                            disabled={already}
	                                            on:click={() => {
	                                                if (!already) {
	                                                    if (group.negated) excludeItemOnUnit(group.unit, itemName, 'equip_ui');
	                                                    else equipItemOnUnit(group.unit, itemName, 'equip_ui');
	                                                }
	                                                equipQuery = '';
	                                                handleEquipInput();
	                                                tick().then(() => equipInputEl?.focus?.());
	                                            }}
	                                            title={already ? (group.negated ? 'Already excluded' : 'Already equipped') : (group.negated ? 'Exclude item' : 'Equip item')}
	                                        >
	                                            <span class="equip-name">{getDisplayName('item', item.label)}</span>
	                                            {#if already}
	                                                <span class="equip-status">{group.negated ? 'Excluded' : 'Added'}</span>
	                                            {:else if eqCount > 0}
	                                                <span class="equip-count">{eqCount.toLocaleString()} games</span>
	                                            {:else}
	                                                <span class="equip-count">No data</span>
                                            {/if}
                                        </button>
                                    {/each}
                                    {#if equipResults.length === 0}
                                        <div class="equip-no-results">No items found</div>
                                    {/if}
                                </div>

	                                <div class="equip-hint">
	                                    <kbd>Enter</kbd> {group.negated ? 'excludes' : 'equips'} • <kbd>Esc</kbd> closes
	                                </div>
	                            {/if}
	                        </div>
	                    {/if}
	                </div>
            {/each}

            {#each otherTokens as token (token)}
                <div class="chip {getChipType(token)}" class:excluded={parseToken(token)?.negated}>
                    <span>{getChipLabel(token)}</span>
                    <button on:click={() => removeToken(token)} aria-label="Remove filter">
                        ×
                    </button>
                </div>
            {/each}
            <button
                type="button"
                class="chip clear-all"
                on:click={clearTokens}
                aria-label="Clear all filters"
                title="Clear all filters"
            >
                <span>Clear all ({$selectedTokens.length})</span>
                <span class="clear-all-x" aria-hidden="true">×</span>
            </button>
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
        cursor: pointer;
        font-family: inherit;
    }

    .chip.clear-all::before {
        background: var(--error);
    }

    .chip.clear-all:hover {
        border-color: rgba(255, 68, 68, 0.6);
        background: rgba(255, 68, 68, 0.11);
        color: rgba(255, 68, 68, 0.98);
    }

    .chip.excluded {
        border-style: dashed;
        border-color: rgba(255, 68, 68, 0.4);
        background: rgba(255, 68, 68, 0.07);
    }

    .chip.excluded:hover {
        border-color: rgba(255, 68, 68, 0.6);
        background: rgba(255, 68, 68, 0.11);
    }

    .chip > button {
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

    .chip > button:hover {
        color: var(--text-primary);
        background: var(--bg-tertiary);
    }

    .chip.clear-all:focus-visible {
        outline: 2px solid rgba(255, 68, 68, 0.65);
        outline-offset: 2px;
    }

    .clear-all-x {
        width: 16px;
        height: 16px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        border-radius: 3px;
        font-size: 16px;
        line-height: 1;
    }

    .chip.clear-all:hover .clear-all-x,
    .chip.clear-all:focus-visible .clear-all-x {
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

    .champion-chip {
        gap: 10px;
        padding: 6px 8px 6px 10px;
        border-radius: 12px;
        flex-wrap: wrap;
    }

    .champion-header {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
    }

    .champion-name {
        font-weight: 800;
        font-size: 12px;
        letter-spacing: -0.01em;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .champion-stars {
        display: inline-flex;
        position: relative;
        align-items: center;
        gap: 4px;
        flex: 0 0 auto;
    }

    .star-trigger {
        border: 1px solid var(--border);
        background: linear-gradient(
            180deg,
            rgba(255, 255, 255, 0.06),
            rgba(255, 255, 255, 0.02)
        );
        color: var(--text-secondary);
        font-size: 10px;
        font-weight: 800;
        padding: 3px 8px;
        border-radius: 999px;
        cursor: pointer;
        line-height: 1.2;
        opacity: 0.95;
        transition: transform 0.12s ease, background 0.12s ease, border-color 0.12s ease,
            color 0.12s ease, opacity 0.12s ease;
        user-select: none;
        display: inline-flex;
        align-items: center;
        gap: 6px;
        white-space: nowrap;
    }

    .star-trigger:hover {
        opacity: 1;
        color: var(--text-primary);
        border-color: var(--border-hover);
        background: linear-gradient(
            180deg,
            rgba(255, 255, 255, 0.08),
            rgba(255, 255, 255, 0.03)
        );
        transform: translateY(-1px);
    }

    .star-trigger:active {
        transform: translateY(0);
    }

    .star-trigger:focus-visible {
        outline: 2px solid color-mix(in oklab, var(--unit) 55%, transparent 45%);
        outline-offset: 2px;
    }

    .star-trigger.open {
        border-color: color-mix(in oklab, var(--unit) 38%, var(--border) 62%);
        color: var(--text-primary);
    }

    .star-value {
        letter-spacing: -0.01em;
    }

    .star-chevron {
        font-size: 11px;
        opacity: 0.75;
        transition: transform 0.12s ease, opacity 0.12s ease;
    }

    .star-trigger.open .star-chevron {
        transform: rotate(180deg);
        opacity: 1;
    }

    .star-popover {
        position: absolute;
        top: calc(100% + 6px);
        left: 0;
        padding: 6px;
        min-width: 116px;
        background: rgba(17, 17, 17, 0.92);
        border: 1px solid var(--border);
        border-radius: 12px;
        box-shadow: 0 16px 48px rgba(0, 0, 0, 0.6);
        backdrop-filter: blur(10px);
        z-index: 60;
        display: grid;
        gap: 4px;
        transform-origin: top left;
        animation: star-pop 110ms ease-out;
    }

    @keyframes star-pop {
        from {
            opacity: 0;
            transform: translateY(-6px) scale(0.98);
        }
        to {
            opacity: 1;
            transform: translateY(0) scale(1);
        }
    }

    .star-option {
        width: 100%;
        border: 1px solid rgba(255, 255, 255, 0.08);
        background: rgba(255, 255, 255, 0.01);
        color: var(--text-secondary);
        padding: 6px 8px;
        border-radius: 10px;
        cursor: pointer;
        line-height: 1.2;
        transition: transform 0.12s ease, background 0.12s ease, border-color 0.12s ease,
            color 0.12s ease;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
        text-align: left;
        font-size: 11px;
        font-weight: 650;
    }

    .star-option:hover,
    .star-option:focus-visible {
        color: var(--text-primary);
        background: rgba(255, 255, 255, 0.04);
        border-color: rgba(255, 255, 255, 0.12);
        transform: translateY(-1px);
        outline: none;
    }

    .star-option:active {
        transform: translateY(0);
    }

    .star-option.selected {
        background: rgba(255, 107, 157, 0.12);
        border-color: rgba(255, 107, 157, 0.22);
        color: var(--text-primary);
    }

    .star-option-check {
        color: var(--unit);
        font-weight: 900;
        opacity: 0.95;
    }

    .champion-items {
        display: inline-flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px;
        border-left: 1px solid var(--border);
        padding-left: 8px;
        margin-left: 2px;
        min-height: 28px;
    }

    .champion-item {
        position: relative;
        width: 28px;
        height: 28px;
        border-radius: 9px;
        border: 1px solid rgba(245, 166, 35, 0.25);
        background: rgba(245, 166, 35, 0.08);
        padding: 0;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
        transition: transform 0.12s ease, background 0.12s ease, border-color 0.12s ease;
    }

    .champion-item:hover,
    .champion-item:focus-visible {
        transform: translateY(-1px);
        border-color: rgba(245, 166, 35, 0.45);
        background: rgba(245, 166, 35, 0.13);
        outline: none;
    }

    .champion-item-icon {
        width: 18px;
        height: 18px;
        border-radius: 5px;
    }

    .champion-item-fallback {
        font-size: 10px;
        font-weight: 900;
        letter-spacing: 0.03em;
        color: rgba(255, 255, 255, 0.82);
        text-transform: uppercase;
    }

    .champion-item-x {
        position: absolute;
        top: -6px;
        right: -6px;
        width: 16px;
        height: 16px;
        border-radius: 999px;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        background: rgba(0, 0, 0, 0.58);
        border: 1px solid rgba(255, 255, 255, 0.12);
        color: rgba(255, 255, 255, 0.86);
        font-size: 12px;
        line-height: 1;
        opacity: 0;
        transform: scale(0.9);
        transition: opacity 0.12s ease, transform 0.12s ease;
        pointer-events: none;
    }

    .champion-item:hover .champion-item-x,
    .champion-item:focus-visible .champion-item-x {
        opacity: 1;
        transform: scale(1);
    }

    .champion-item-add {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 28px;
        height: 28px;
        padding: 0;
        border-radius: 9px;
        border: 1px dashed rgba(245, 166, 35, 0.38);
        background: rgba(245, 166, 35, 0.06);
        color: rgba(245, 166, 35, 0.95);
        cursor: pointer;
        transition: all 0.15s ease;
    }

    .champion-item-add:hover,
    .champion-item-add:focus-visible {
        border-style: solid;
        border-color: rgba(245, 166, 35, 0.55);
        background: rgba(245, 166, 35, 0.12);
        color: var(--text-primary);
        outline: none;
    }

    .champion-item-add-plus {
        font-size: 18px;
        font-weight: 950;
        line-height: 1;
    }

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
        border-radius: 6px;
        transition: all 0.15s ease;
        flex-shrink: 0;
        margin-left: auto;
    }

    .remove-group-btn:hover {
        color: var(--text-primary);
        background: var(--bg-tertiary);
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
