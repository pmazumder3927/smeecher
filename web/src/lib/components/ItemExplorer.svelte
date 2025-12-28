<script>
    import { fetchItemNecessity, fetchUnitItems, fetchUnitBuild } from '../api.js';
    import {
        selectedTokens,
        addToken,
        addTokens,
        itemExplorerOpen,
        itemExplorerTab,
        itemExplorerSortMode,
        itemExplorerUnit,
        itemTypeFilters,
        itemPrefixFilters
    } from '../stores/state.js';
    import { parseToken } from '../utils/tokens.js';
    import { getDisplayName, getIconUrl, hasIconFailed, markIconFailed } from '../stores/assets.js';
    import AvgPlacement from './AvgPlacement.svelte';
    import posthog from '../client/posthog';

    const COLLAPSED_WIDTH_PX = 46;
    const EXPANDED_WIDTH_PX = 340;

    let loading = false;
    let error = null;
    let data = null;
    let buildData = null;

    let necessityOpenItem = null;
    let necessityLoadingItem = null;
    let necessityErrorByItem = {};
    let necessityByItem = {};
    let necessityContextKey = '';

    let lastQueryKey = '';
    let stale = false;
    let fetchVersion = 0;

    let lastSelectedUnit = null;

    // Units available from the current filter selection
    $: availableUnits = (() => {
        const seen = new Set();
        const units = [];
        for (const token of $selectedTokens) {
            const parsed = parseToken(token);
            if (parsed.type === 'unit' && parsed.unit && !seen.has(parsed.unit)) {
                seen.add(parsed.unit);
                units.push(parsed.unit);
            } else if (parsed.type === 'equipped' && parsed.unit && !seen.has(parsed.unit)) {
                seen.add(parsed.unit);
                units.push(parsed.unit);
            }
        }
        return units;
    })();

    // Keep activeUnit valid as filters change
    $: if (availableUnits.length === 0) {
        if ($itemExplorerUnit !== null) itemExplorerUnit.set(null);
    } else if (!$itemExplorerUnit || !availableUnits.includes($itemExplorerUnit)) {
        itemExplorerUnit.set(availableUnits[0]);
    }

    $: selectedUnit = $itemExplorerUnit;

    // Keep all selected filters except *this unit's* own unit tokens (including star-level),
    // since the explorer already conditions on unit presence and should default to "any star level".
    $: contextTokens = selectedUnit
        ? $selectedTokens.filter(t => {
            const parsed = parseToken(t);
            return !(parsed.type === 'unit' && parsed.unit === selectedUnit);
        })
        : $selectedTokens;

    // Clear stale results when switching the explored unit
    $: if (selectedUnit !== lastSelectedUnit) {
        data = null;
        buildData = null;
        error = null;
        lastSelectedUnit = selectedUnit;
    }

    $: activeItemTypes = [...$itemTypeFilters];
    $: activeItemPrefixes = [...$itemPrefixFilters];
    $: showNecessity = $itemExplorerSortMode === 'necessity';

    $: queryKey = `${selectedUnit ?? ''}|${$itemExplorerSortMode}|${activeItemTypes.slice().sort().join('|')}|${activeItemPrefixes.slice().sort().join('|')}|${contextTokens.slice().sort().join(',')}`;
    $: if ($itemExplorerOpen && lastQueryKey && queryKey !== lastQueryKey) stale = true;
    $: if (queryKey !== necessityContextKey) {
        necessityContextKey = queryKey;
        necessityOpenItem = null;
        necessityLoadingItem = null;
        necessityErrorByItem = {};
        necessityByItem = {};
    }

    $: items = data?.items ?? [];
    $: builds = buildData?.builds ?? [];
    $: sidebarWidth = $itemExplorerOpen ? EXPANDED_WIDTH_PX : COLLAPSED_WIDTH_PX;

    // Auto-fetch when opening with a selected unit
    $: if ($itemExplorerOpen && selectedUnit && (!data || stale)) {
        run();
    }

    function toggleOpen() {
        const nextOpen = !$itemExplorerOpen;
        itemExplorerOpen.set(nextOpen);
        if (!nextOpen) {
            posthog.capture('item_explorer_closed');
            return;
        }
        posthog.capture('item_explorer_opened');
        if (selectedUnit && !data) run();
    }

    async function run() {
        if (!selectedUnit) return;

        const version = ++fetchVersion;
        loading = true;
        error = null;
        stale = false;
        lastQueryKey = queryKey;

        try {
            // Fetch both build recommendation and all items in parallel
            const [buildResult, itemsResult] = await Promise.all([
                fetchUnitBuild(selectedUnit, contextTokens, { slots: 3, itemTypes: activeItemTypes, itemPrefixes: activeItemPrefixes }),
                fetchUnitItems(selectedUnit, contextTokens, { sortMode: $itemExplorerSortMode, itemTypes: activeItemTypes, itemPrefixes: activeItemPrefixes })
            ]);
            if (version !== fetchVersion) return;
            buildData = buildResult;
            data = itemsResult;
            posthog.capture('item_explorer_run', {
                unit: selectedUnit,
                filter_count: contextTokens.length,
                build_items: buildResult?.builds?.length ?? 0,
                result_count: itemsResult?.items?.length ?? 0
            });
        } catch (e) {
            if (version !== fetchVersion) return;
            error = e?.message ?? String(e);
        } finally {
            if (version === fetchVersion) loading = false;
        }
    }

    function applyBuild(build) {
        if (!build?.items?.length) return;
        const tokens = build.items.map(b => b.token);
        addTokens(tokens, 'item_explorer_build');
        posthog.capture('build_applied', {
            unit: selectedUnit,
            items: build.items.map(b => b.item),
            final_avg: build.final_avg
        });
    }

    function addItem(item) {
        const token = `E:${selectedUnit}|${item.item}`;
        addToken(token, 'item_explorer');
        posthog.capture('item_added_from_explorer', {
            unit: selectedUnit,
            item: item.item,
            delta: item.delta
        });
    }

    function itemIcon(itemName) {
        return getIconUrl('item', itemName);
    }

    function itemDisplayName(itemName) {
        return getDisplayName('item', itemName);
    }

    function unitDisplayName(unitName) {
        return getDisplayName('unit', unitName);
    }

    function unitIcon(unitName) {
        return getIconUrl('unit', unitName);
    }

    function fmtDelta(delta) {
        if (delta === null || delta === undefined) return '';
        const sign = delta > 0 ? '+' : '';
        return `${sign}${delta.toFixed(2)}`;
    }

    function fmtPct(pct) {
        return `${pct.toFixed(1)}%`;
    }

    function fmtPp(effect) {
        if (effect === null || effect === undefined) return '';
        const v = effect * 100;
        const sign = v > 0 ? '+' : '';
        return `${sign}${v.toFixed(1)}pp`;
    }

    function fmtCi(low, high) {
        if (low === null || low === undefined || high === null || high === undefined) return '';
        return `[${fmtPp(low)}, ${fmtPp(high)}]`;
    }

    function deltaClass(delta) {
        if (delta < -0.1) return 'positive';
        if (delta > 0.1) return 'negative';
        return 'neutral';
    }

    function necessityClass(tau) {
        if (tau > 0.01) return 'positive';
        if (tau < -0.01) return 'negative';
        return 'neutral';
    }

    async function toggleNecessity(itemRow) {
        const itemName = itemRow?.item;
        if (!selectedUnit || !itemName) return;

        if (necessityOpenItem === itemName) {
            necessityOpenItem = null;
            return;
        }

        necessityOpenItem = itemName;
        if (itemRow?.necessity) return;
        if (necessityByItem[itemName]) return;

        necessityLoadingItem = itemName;
        necessityErrorByItem = { ...necessityErrorByItem, [itemName]: null };

        try {
            const res = await fetchItemNecessity(selectedUnit, itemName, contextTokens, { outcome: 'top4' });
            necessityByItem = { ...necessityByItem, [itemName]: res };
        } catch (e) {
            necessityErrorByItem = { ...necessityErrorByItem, [itemName]: e?.message ?? String(e) };
        } finally {
            if (necessityLoadingItem === itemName) necessityLoadingItem = null;
        }
    }
</script>

<div class="item-explorer" data-walkthrough="itemExplorer" class:open={$itemExplorerOpen} style={`width: ${sidebarWidth}px;`}>
    <button class="toggle" on:click={toggleOpen} aria-label="Toggle item explorer" aria-expanded={$itemExplorerOpen}>
        <span class="toggle-title">Items</span>
        {#if $itemExplorerOpen}
            <span class="toggle-sub">close</span>
        {:else}
            <span class="toggle-sub">best builds</span>
        {/if}
    </button>

    {#if $itemExplorerOpen}
        <div class="panel">
            {#if !selectedUnit}
                <div class="empty-state">
                    <div class="empty-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <circle cx="11" cy="11" r="8"/>
                            <path d="m21 21-4.35-4.35"/>
                        </svg>
                    </div>
                    <div class="empty-text">Select a unit to see best items</div>
                    <div class="empty-hint">Search for a champion like "Miss Fortune" or "Lux"</div>
                </div>
            {:else}
                <div class="panel-header">
                    <div class="unit-info">
                        {#if unitIcon(selectedUnit) && !hasIconFailed('unit', selectedUnit)}
                            <img
                                class="unit-icon"
                                src={unitIcon(selectedUnit)}
                                alt=""
                                on:error={() => markIconFailed('unit', selectedUnit)}
                            />
                        {:else}
                            <div class="unit-fallback"></div>
                        {/if}
                        <div class="unit-details">
                            <div class="unit-name">{unitDisplayName(selectedUnit)}</div>
                            <div class="unit-meta">
                                {#if data?.base}
                                    <span>{data.base.n.toLocaleString()} games</span>
                                    <span class="dot">&bull;</span>
                                    <AvgPlacement value={data.base.avg_placement} suffix=" avg" />
                                {:else if loading}
                                    <span>Loading...</span>
                                {:else}
                                    <span>Click Run to analyze</span>
                                {/if}
                            </div>
                        </div>
                    </div>
                    {#if availableUnits.length > 1}
                        <div class="unit-select-row">
                            <label class="select">
                                <span>Champion</span>
                                <select bind:value={$itemExplorerUnit} disabled={loading}>
                                    {#each availableUnits as unit (unit)}
                                        <option value={unit}>{unitDisplayName(unit)}</option>
                                    {/each}
                                </select>
                            </label>
                        </div>
                    {/if}
                </div>

                <div class="tabs">
                    <button
                        class="tab"
                        class:active={$itemExplorerTab === 'builds'}
                        on:click={() => itemExplorerTab.set('builds')}
                    >
                        Builds
                        {#if builds.length > 0}
                            <span class="tab-count">{builds.length}</span>
                        {/if}
                    </button>
                    <button
                        class="tab"
                        class:active={$itemExplorerTab === 'items'}
                        on:click={() => itemExplorerTab.set('items')}
                    >
                        Items
                        {#if items.length > 0}
                            <span class="tab-count">{items.length}</span>
                        {/if}
                    </button>
                    <button class="refresh-btn" disabled={loading} on:click={run} title="Refresh data">
                        {#if loading}
                            <span class="spinner"></span>
                        {:else if stale}
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M1 4v6h6M23 20v-6h-6"/>
                                <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/>
                            </svg>
                        {:else}
                            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                                <path d="M1 4v6h6M23 20v-6h-6"/>
                                <path d="M20.49 9A9 9 0 0 0 5.64 5.64L1 10m22 4l-4.64 4.36A9 9 0 0 1 3.51 15"/>
                            </svg>
                        {/if}
                    </button>
                </div>

                {#if error}
                    <div class="callout error">{error}</div>
                {/if}

                {#if contextTokens.length > 0}
                    <div class="context-info">
                        Filtered by {contextTokens.length} additional condition{contextTokens.length > 1 ? 's' : ''}
                    </div>
                {/if}

                {#if $itemExplorerTab === 'items'}
                    <div class="sort-bar">
                        <label class="select">
                            <span>Sort</span>
                            <select bind:value={$itemExplorerSortMode} on:change={run}>
                                <option value="helpful">Best first</option>
                                <option value="harmful">Worst first</option>
                                <option value="impact">Most impact</option>
                                <option value="necessity">Most necessary (AIPW)</option>
                            </select>
                        </label>
                    </div>
                {/if}

                <div class="list-container">
                    {#if loading && ($itemExplorerTab === 'builds' ? builds.length === 0 : items.length === 0)}
                        <div class="loading-skeleton">
                            {#each Array(6) as _, i}
                                <div class="skeleton-row"></div>
                            {/each}
                        </div>
                    {:else if $itemExplorerTab === 'builds'}
                        {#if builds.length === 0 && !loading}
                            <div class="no-items">
                                No builds found with sufficient data.
                                Try removing filters or lowering the sample threshold.
                            </div>
                        {:else}
                            {#each builds as build, idx (build.items?.map(i => i?.item ?? '').join('|'))}
                                <button class="row build-row" on:click={() => applyBuild(build)}>
                                    <div class="row-rank">#{idx + 1}</div>
                                    <div class="build-icons">
                                        {#each [0, 1, 2] as slotIdx}
                                            {@const item = build.items[slotIdx]}
                                            {#if item}
                                                <div class="build-icon-wrap" title={itemDisplayName(item.item)}>
                                                    {#if itemIcon(item.item) && !hasIconFailed('item', item.item)}
                                                        <img
                                                            class="build-icon"
                                                            src={itemIcon(item.item)}
                                                            alt=""
                                                            on:error={() => markIconFailed('item', item.item)}
                                                        />
                                                    {:else}
                                                        <div class="build-icon-fallback"></div>
                                                    {/if}
                                                </div>
                                            {:else}
                                                <div class="build-icon-wrap empty" title="Not enough data">
                                                    <div class="build-icon-empty"></div>
                                                </div>
                                            {/if}
                                        {/each}
                                    </div>
                                    <div class="row-metrics">
                                        <div class="row-avg"><AvgPlacement value={build.final_avg} /></div>
                                        <div class="row-delta {deltaClass(build.total_delta)}">
                                            {fmtDelta(build.total_delta)}
                                        </div>
                                    </div>
                                    <div class="row-n">{build.final_n.toLocaleString()}</div>
                                    <div class="add-icon">+</div>
                                </button>
                            {/each}
                        {/if}
                    {:else}
                        {#if items.length === 0 && !loading}
                            <div class="no-items">
                                No items found with sufficient data.
                                Try adjusting filters or lowering the sample threshold.
                            </div>
                        {:else}
                            {#each items as item, idx (item.item)}
                                <div
                                    class="row item-row"
                                    role="button"
                                    tabindex="0"
                                    on:click={() => addItem(item)}
                                    on:keydown={(e) => {
                                        if (e.key === 'Enter' || e.key === ' ') {
                                            e.preventDefault();
                                            addItem(item);
                                        }
                                    }}
                                >
                                    <div class="row-rank">#{idx + 1}</div>
                                    <div class="item-icon-wrapper">
                                        {#if itemIcon(item.item) && !hasIconFailed('item', item.item)}
                                            <img
                                                class="item-icon"
                                                src={itemIcon(item.item)}
                                                alt=""
                                                loading="lazy"
                                                on:error={() => markIconFailed('item', item.item)}
                                            />
                                        {:else}
                                            <div class="item-fallback"></div>
                                        {/if}
                                    </div>
                                    <div class="item-info">
                                        <div class="item-name">{itemDisplayName(item.item)}</div>
                                        <div class="item-stats">
                                            <span class="stat-n">{item.n.toLocaleString()}</span>
                                            <span class="stat-sep">&bull;</span>
                                            <span class="stat-pct">{fmtPct(item.pct_of_base)} use</span>
                                        </div>
                                    </div>
                                    <div class="row-metrics">
                                        <div class="row-avg"><AvgPlacement value={item.avg_placement} /></div>
                                        <div
                                            class="row-delta {showNecessity ? necessityClass(item.necessity?.tau ?? null) : deltaClass(item.delta)}"
                                        >
                                            {#if showNecessity}
                                                {#if item.necessity?.tau !== null && item.necessity?.tau !== undefined}
                                                    {fmtPp(item.necessity.tau)}
                                                {:else}
                                                    —
                                                {/if}
                                            {:else}
                                                {fmtDelta(item.delta)}
                                            {/if}
                                        </div>
                                    </div>
                                    <button
                                        class="necessity-btn"
                                        title="Estimate necessity (AIPW ΔTop4)"
                                        aria-label="Estimate necessity"
                                        on:click|stopPropagation={() => toggleNecessity(item)}
                                        disabled={necessityLoadingItem === item.item}
                                    >
                                        N
                                    </button>
                                    <div class="add-icon">+</div>
                                </div>
                                {#if necessityOpenItem === item.item}
                                    <div class="necessity-panel">
                                        {#if necessityLoadingItem === item.item}
                                            <div class="necessity-row">Estimating…</div>
                                        {:else if necessityErrorByItem[item.item]}
                                            <div class="necessity-row error">{necessityErrorByItem[item.item]}</div>
                                        {:else if item.necessity}
                                            {@const r = item.necessity}
                                            <div class="necessity-row">
                                                <span class="k">AIPW ΔTop4</span>
                                                <span class="v">{fmtPp(r.tau)}</span>
                                                <span class="ci">{fmtCi(r.ci95_low, r.ci95_high)}</span>
                                            </div>
                                            <div class="necessity-row meta">
                                                <span>{r.n_treated.toLocaleString()} with</span>
                                                <span class="dot">&bull;</span>
                                                <span>{r.n_control.toLocaleString()} without</span>
                                                <span class="dot">&bull;</span>
                                                <span>{r.n_used.toLocaleString()} used</span>
                                                <span class="dot">&bull;</span>
                                                <span>{Math.round(r.frac_trimmed * 100)}% trimmed</span>
                                            </div>
                                            {#if r.warnings?.length}
                                                {#each r.warnings as w (w)}
                                                    <div class="necessity-row warn">{w}</div>
                                                {/each}
                                            {/if}
                                        {:else if necessityByItem[item.item]?.effect}
                                            {@const r = necessityByItem[item.item]}
                                            <div class="necessity-row">
                                                <span class="k">AIPW ΔTop4</span>
                                                <span class="v">{fmtPp(r.effect.tau)}</span>
                                                <span class="ci">{fmtCi(r.effect.ci95_low, r.effect.ci95_high)}</span>
                                            </div>
                                            <div class="necessity-row meta">
                                                <span>{r.treatment.n_treated.toLocaleString()} with</span>
                                                <span class="dot">&bull;</span>
                                                <span>{r.treatment.n_control.toLocaleString()} without</span>
                                                <span class="dot">&bull;</span>
                                                <span>{r.overlap.n_used.toLocaleString()} used</span>
                                                <span class="dot">&bull;</span>
                                                <span>{Math.round(r.overlap.frac_trimmed * 100)}% trimmed</span>
                                            </div>
                                            {#if r.warnings?.length}
                                                {#each r.warnings as w (w)}
                                                    <div class="necessity-row warn">{w}</div>
                                                {/each}
                                            {/if}
                                        {:else}
                                            <div class="necessity-row meta">No estimate available.</div>
                                        {/if}
                                    </div>
                                {/if}
                            {/each}
                        {/if}
                    {/if}
                </div>
            {/if}
        </div>
    {/if}
</div>

<style>
    .item-explorer {
        position: relative;
        height: 100%;
        min-height: 0;
        flex: 0 0 auto;
        display: flex;
        flex-direction: column;
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 12px;
        overflow: hidden;
        min-width: 46px;
        transition: width 0.18s ease;
    }

    .toggle {
        width: 100%;
        background: transparent;
        border: none;
        color: var(--text-primary);
        padding: 10px 12px;
        cursor: pointer;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        transition: border-color 0.2s ease, background 0.2s ease;
        font-family: inherit;
        border-bottom: 1px solid var(--border);
    }

    .toggle:hover {
        background: rgba(255, 255, 255, 0.03);
    }

    .toggle-title {
        font-size: 11px;
        font-weight: 900;
        letter-spacing: 0.02em;
        text-transform: uppercase;
    }

    .toggle-sub {
        font-size: 10px;
        color: var(--text-tertiary);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-weight: 700;
    }

    .item-explorer:not(.open) .toggle {
        flex: 1;
        border-bottom: none;
        padding: 12px 0;
        writing-mode: vertical-rl;
        text-orientation: mixed;
        justify-content: center;
        align-items: center;
        gap: 10px;
    }

    .item-explorer:not(.open) .toggle-sub {
        display: none;
    }

    .panel {
        width: 100%;
        flex: 1;
        min-height: 0;
        background: transparent;
        overflow: hidden;
        display: flex;
        flex-direction: column;
    }

    .empty-state {
        flex: 1;
        display: flex;
        flex-direction: column;
        align-items: center;
        justify-content: center;
        padding: 24px;
        text-align: center;
        gap: 12px;
    }

    .empty-icon {
        width: 48px;
        height: 48px;
        color: var(--text-tertiary);
        opacity: 0.5;
    }

    .empty-icon svg {
        width: 100%;
        height: 100%;
    }

    .empty-text {
        font-size: 13px;
        font-weight: 700;
        color: var(--text-secondary);
    }

    .empty-hint {
        font-size: 11px;
        color: var(--text-tertiary);
        line-height: 1.5;
    }

    .panel-header {
        padding: 12px 14px;
        border-bottom: 1px solid var(--border);
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .unit-info {
        display: flex;
        align-items: center;
        gap: 10px;
    }

    .unit-icon {
        width: 40px;
        height: 40px;
        border-radius: 10px;
        border: 1px solid var(--border);
        object-fit: cover;
        flex-shrink: 0;
    }

    .unit-fallback {
        width: 40px;
        height: 40px;
        border-radius: 10px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        flex-shrink: 0;
        position: relative;
    }

    .unit-fallback::after {
        content: '';
        position: absolute;
        inset: 8px;
        border-radius: 6px;
        background: rgba(255, 107, 157, 0.4);
    }

    .unit-details {
        flex: 1;
        min-width: 0;
    }

    .unit-name {
        font-size: 14px;
        font-weight: 900;
        letter-spacing: -0.01em;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .unit-meta {
        font-size: 11px;
        color: var(--text-tertiary);
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 2px;
    }

    .unit-select-row {
        display: flex;
        align-items: center;
        width: 100%;
    }

    .dot {
        opacity: 0.6;
    }

    /* Tabs */
    .tabs {
        display: flex;
        align-items: center;
        border-bottom: 1px solid var(--border);
        padding: 0 4px;
    }

    .tab {
        flex: 1;
        background: transparent;
        border: none;
        color: var(--text-tertiary);
        padding: 10px 12px;
        font-size: 11px;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        cursor: pointer;
        transition: color 0.15s ease, border-color 0.15s ease;
        border-bottom: 2px solid transparent;
        margin-bottom: -1px;
        font-family: inherit;
        display: flex;
        align-items: center;
        justify-content: center;
        gap: 6px;
    }

    .tab:hover {
        color: var(--text-secondary);
    }

    .tab.active {
        color: var(--text-primary);
        border-bottom-color: var(--accent);
    }

    .tab-count {
        font-size: 9px;
        background: rgba(255, 255, 255, 0.08);
        padding: 2px 5px;
        border-radius: 4px;
        font-weight: 700;
    }

    .tab.active .tab-count {
        background: rgba(0, 217, 255, 0.15);
        color: var(--accent);
    }

    .refresh-btn {
        width: 32px;
        height: 32px;
        background: transparent;
        border: none;
        color: var(--text-tertiary);
        cursor: pointer;
        display: grid;
        place-items: center;
        border-radius: 6px;
        transition: color 0.15s ease, background 0.15s ease;
        flex-shrink: 0;
    }

    .refresh-btn:hover:not(:disabled) {
        color: var(--text-primary);
        background: rgba(255, 255, 255, 0.05);
    }

    .refresh-btn:disabled {
        opacity: 0.5;
        cursor: default;
    }

    .refresh-btn svg {
        width: 16px;
        height: 16px;
    }

    .spinner {
        width: 14px;
        height: 14px;
        border: 2px solid var(--border);
        border-top-color: var(--accent);
        border-radius: 50%;
        animation: spin 0.8s linear infinite;
    }

    @keyframes spin {
        to { transform: rotate(360deg); }
    }

    /* Sort bar */
    .sort-bar {
        display: flex;
        gap: 10px;
        padding: 8px 14px;
        border-bottom: 1px solid var(--border);
        align-items: center;
    }

    .select {
        display: flex;
        align-items: center;
        gap: 6px;
        flex: 1;
    }

    .select span {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-weight: 700;
        color: var(--text-tertiary);
    }

    .select select {
        flex: 1;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        color: var(--text-primary);
        border-radius: 6px;
        padding: 5px 8px;
        font-size: 11px;
        font-weight: 700;
        font-family: inherit;
    }

    /* Callouts */
    .callout {
        padding: 10px 14px;
        font-size: 12px;
        color: var(--text-secondary);
        border-bottom: 1px solid var(--border);
    }

    .callout.error {
        color: rgba(255, 68, 68, 0.95);
        background: rgba(255, 68, 68, 0.08);
    }

    .context-info {
        padding: 6px 14px;
        font-size: 10px;
        color: var(--text-tertiary);
        border-bottom: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.02);
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-weight: 700;
    }

    /* List container */
    .list-container {
        flex: 1;
        overflow-y: auto;
        min-height: 0;
    }

    .loading-skeleton {
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .skeleton-row {
        height: 48px;
        background: linear-gradient(90deg,
            var(--bg-tertiary) 25%,
            rgba(255, 255, 255, 0.05) 50%,
            var(--bg-tertiary) 75%
        );
        background-size: 200% 100%;
        animation: shimmer 1.5s infinite;
        border-radius: 8px;
    }

    @keyframes shimmer {
        0% { background-position: 200% 0; }
        100% { background-position: -200% 0; }
    }

    .no-items {
        padding: 20px 14px;
        font-size: 12px;
        color: var(--text-tertiary);
        text-align: center;
        line-height: 1.6;
    }

    /* Shared row styles */
    .row {
        display: flex;
        align-items: center;
        gap: 10px;
        width: 100%;
        padding: 10px 14px;
        background: transparent;
        border: none;
        border-bottom: 1px solid var(--border);
        cursor: pointer;
        transition: background 0.15s ease;
        color: var(--text-primary);
        text-align: left;
        font-family: inherit;
    }

    .row:last-child {
        border-bottom: none;
    }

    .row:hover {
        background: rgba(255, 255, 255, 0.03);
    }

    .row-rank {
        width: 24px;
        font-size: 10px;
        font-weight: 900;
        color: var(--text-tertiary);
        text-align: center;
        flex-shrink: 0;
    }

    .row-metrics {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 2px;
        flex-shrink: 0;
    }

    .row-avg {
        font-size: 14px;
        font-weight: 900;
        letter-spacing: -0.01em;
    }

    .row-delta {
        font-size: 11px;
        font-weight: 800;
    }

    .row-delta.positive {
        color: var(--success);
    }

    .row-delta.negative {
        color: var(--error);
    }

    .row-delta.neutral {
        color: var(--text-tertiary);
    }

    .row-n {
        font-size: 10px;
        color: var(--text-tertiary);
        font-weight: 600;
        min-width: 40px;
        text-align: right;
    }

    .add-icon {
        width: 22px;
        height: 22px;
        border-radius: 6px;
        display: grid;
        place-items: center;
        background: rgba(255, 255, 255, 0.05);
        border: 1px solid var(--border);
        color: var(--text-tertiary);
        font-weight: 900;
        font-size: 14px;
        flex-shrink: 0;
        opacity: 0;
        transition: opacity 0.15s ease;
    }

    .row:hover .add-icon {
        opacity: 1;
    }

    /* Build row specifics */
    .build-icons {
        display: flex;
        align-items: center;
        gap: 4px;
        flex: 1;
        min-width: 0;
    }

    .build-icon-wrap {
        flex-shrink: 0;
    }

    .build-icon {
        width: 28px;
        height: 28px;
        border-radius: 6px;
        border: 1px solid var(--border);
        object-fit: cover;
    }

    .build-icon-fallback {
        width: 28px;
        height: 28px;
        border-radius: 6px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
    }

    .build-icon-empty {
        width: 28px;
        height: 28px;
        border-radius: 6px;
        border: 1px dashed var(--border);
        background: transparent;
        opacity: 0.4;
    }

    /* Item row specifics */
    .item-icon-wrapper {
        flex-shrink: 0;
    }

    .item-icon {
        width: 32px;
        height: 32px;
        border-radius: 8px;
        border: 1px solid var(--border);
        object-fit: cover;
    }

    .item-fallback {
        width: 32px;
        height: 32px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        position: relative;
    }

    .item-fallback::after {
        content: '';
        position: absolute;
        inset: 6px;
        border-radius: 6px;
        background: rgba(0, 217, 255, 0.4);
    }

    .item-info {
        flex: 1;
        min-width: 0;
    }

    .item-name {
        font-size: 12px;
        font-weight: 700;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .item-stats {
        font-size: 10px;
        color: var(--text-tertiary);
        display: flex;
        align-items: center;
        gap: 4px;
        margin-top: 2px;
    }

    .stat-sep {
        opacity: 0.5;
    }

    .necessity-btn {
        width: 26px;
        height: 22px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.03);
        color: var(--text-tertiary);
        font-weight: 900;
        font-size: 10px;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        cursor: pointer;
        flex-shrink: 0;
        transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
    }

    .necessity-btn:hover {
        background: rgba(255, 255, 255, 0.06);
        color: var(--text-secondary);
    }

    .necessity-btn:disabled {
        opacity: 0.5;
        cursor: default;
    }

    .necessity-panel {
        border-bottom: 1px solid var(--border);
        padding: 8px 14px 10px 58px;
        background: rgba(255, 255, 255, 0.02);
        display: flex;
        flex-direction: column;
        gap: 6px;
    }

    .necessity-row {
        font-size: 11px;
        color: var(--text-secondary);
        display: flex;
        gap: 8px;
        align-items: baseline;
        flex-wrap: wrap;
        line-height: 1.3;
    }

    .necessity-row .k {
        font-weight: 900;
        letter-spacing: 0.02em;
        text-transform: uppercase;
        font-size: 10px;
        color: var(--text-tertiary);
    }

    .necessity-row .v {
        font-weight: 900;
        font-size: 12px;
        color: var(--text-primary);
    }

    .necessity-row .ci {
        color: var(--text-tertiary);
        font-size: 10px;
    }

    .necessity-row.meta {
        color: var(--text-tertiary);
        font-size: 10px;
    }

    .necessity-row.warn {
        color: var(--warning);
        font-size: 10px;
    }

    .necessity-row.error {
        color: var(--error);
        font-size: 10px;
    }

    @media (max-width: 768px) {
        .item-explorer {
            width: 100% !important;
            height: auto;
        }

        .item-explorer:not(.open) .toggle {
            writing-mode: horizontal-tb;
            text-orientation: unset;
            justify-content: space-between;
            align-items: center;
            padding: 10px 12px;
            flex: 0 0 auto;
            border-bottom: none;
        }

        .item-explorer:not(.open) .toggle-sub {
            display: inline;
        }

        .panel {
            height: min(480px, 60vh);
        }

        .list-container {
            max-height: 50vh;
        }
    }
</style>
