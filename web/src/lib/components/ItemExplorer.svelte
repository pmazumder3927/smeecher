<script>
    import { fetchItemNecessity, fetchItemUnits, fetchUnitItems, fetchUnitBuild } from '../api.js';
    import {
        selectedTokens,
        lastAction,
        addToken,
        addTokens,
        itemExplorerOpen,
        itemExplorerTab,
        itemExplorerSortMode,
        itemExplorerFocus,
        itemExplorerItem,
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

    let necessityOpenToken = null;
    let necessityLoadingToken = null;
    let necessityErrorByToken = {};
    let necessityByToken = {};
    let necessityContextKey = '';

    let lastBuildQueryKey = '';
    let lastItemsQueryKey = '';
    let staleBuild = false;
    let staleItems = false;
    let stale = false;
    let fetchVersion = 0;

    let lastAnchorKey = '';
    let sortHelpOpen = false;
    let lastFocusTs = 0;

    const FOCUS_SOURCES = new Set(['graph', 'search', 'voice']);

    // Units and items available from the current filter selection (positive tokens only)
    $: availableUnits = (() => {
        const seen = new Set();
        const units = [];
        for (const token of $selectedTokens) {
            const parsed = parseToken(token);
            if (parsed.negated) continue;
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

    $: availableItems = (() => {
        const seen = new Set();
        const items = [];
        for (const token of $selectedTokens) {
            const parsed = parseToken(token);
            if (parsed.negated) continue;
            if (parsed.type === 'item' && parsed.item && !seen.has(parsed.item)) {
                seen.add(parsed.item);
                items.push(parsed.item);
            }
        }
        return items;
    })();

    // If the user clicks an item/unit in the graph, switch the explorer focus to match.
    $: if (
        $lastAction?.type === 'token_added' &&
        $lastAction?.timestamp &&
        $lastAction.timestamp !== lastFocusTs &&
        FOCUS_SOURCES.has($lastAction?.source)
    ) {
        lastFocusTs = $lastAction.timestamp;
        const token = $lastAction?.token;
        if (typeof token === 'string') {
            const parsed = parseToken(token);
            if (!parsed.negated) {
                if (parsed.type === 'item') itemExplorerFocus.set('item');
                if (parsed.type === 'unit' || parsed.type === 'equipped') itemExplorerFocus.set('unit');
            }
        }
    }

    // Keep unit/item selection valid as filters change
    $: if (availableUnits.length === 0) {
        if ($itemExplorerUnit !== null) itemExplorerUnit.set(null);
    }
    $: if (availableItems.length === 0) {
        if ($itemExplorerItem !== null) itemExplorerItem.set(null);
    }

    $: effectiveFocus = (() => {
        let focus = $itemExplorerFocus;
        if (focus === 'unit' && availableUnits.length === 0 && availableItems.length > 0) focus = 'item';
        if (focus === 'item' && availableItems.length === 0 && availableUnits.length > 0) focus = 'unit';
        return focus;
    })();

    $: if (effectiveFocus === 'unit' && availableUnits.length > 0) {
        if (!$itemExplorerUnit || !availableUnits.includes($itemExplorerUnit)) {
            itemExplorerUnit.set(availableUnits[0]);
        }
    }

    $: if (effectiveFocus === 'item' && availableItems.length > 0) {
        if (!$itemExplorerItem || !availableItems.includes($itemExplorerItem)) {
            itemExplorerItem.set(availableItems[0]);
        }
    }

    $: selectedUnit = effectiveFocus === 'unit' ? $itemExplorerUnit : null;
    $: selectedItem = effectiveFocus === 'item' ? $itemExplorerItem : null;

    $: exploreMode = selectedUnit ? 'unit' : selectedItem ? 'item' : 'none';

    // Item explorer is only meaningful in the Items tab when exploring an item.
    $: if (exploreMode === 'item' && $itemExplorerTab !== 'items') itemExplorerTab.set('items');

    // Keep all selected filters except the explored unit token (so other unit filters still apply).
    // Star-level tokens like U:Unit:2 are preserved, enabling users to opt into star-specific analysis.
    $: contextTokens =
        exploreMode === 'unit'
            ? $selectedTokens.filter(t => t !== `U:${selectedUnit}`)
            : exploreMode === 'item'
                ? $selectedTokens.filter(t => t !== `I:${selectedItem}`)
                : $selectedTokens;

    // Clear stale results when switching the explored anchor (unit or item)
    $: anchorKey = exploreMode === 'unit' ? `U:${selectedUnit}` : exploreMode === 'item' ? `I:${selectedItem}` : '';
    $: if (anchorKey !== lastAnchorKey) {
        data = null;
        buildData = null;
        error = null;
        staleBuild = false;
        staleItems = false;
        stale = false;
        lastBuildQueryKey = '';
        lastItemsQueryKey = '';
        lastAnchorKey = anchorKey;
    }

    $: activeItemTypes = [...$itemTypeFilters];
    $: activeItemPrefixes = [...$itemPrefixFilters];
    $: showNecessity = $itemExplorerSortMode === 'necessity';

    $: baseQueryKey = `${anchorKey}|${activeItemTypes.slice().sort().join('|')}|${activeItemPrefixes.slice().sort().join('|')}|${contextTokens.slice().sort().join(',')}`;
    $: buildQueryKey = exploreMode === 'unit' ? baseQueryKey : '';
    $: itemsQueryKey = `${baseQueryKey}|${$itemExplorerSortMode}`;

    $: if ($itemExplorerOpen && exploreMode === 'unit' && lastBuildQueryKey && buildQueryKey !== lastBuildQueryKey) staleBuild = true;
    $: if ($itemExplorerOpen && lastItemsQueryKey && itemsQueryKey !== lastItemsQueryKey) staleItems = true;
    $: stale = $itemExplorerTab === 'items' ? staleItems : exploreMode === 'unit' ? staleBuild : false;

    $: if (itemsQueryKey !== necessityContextKey) {
        necessityContextKey = itemsQueryKey;
        necessityOpenToken = null;
        necessityLoadingToken = null;
        necessityErrorByToken = {};
        necessityByToken = {};
    }

    $: items = exploreMode === 'unit' ? data?.items ?? [] : [];
    $: holders = exploreMode === 'item' ? data?.units ?? [] : [];
    $: builds = buildData?.builds ?? [];
    $: sidebarWidth = $itemExplorerOpen ? EXPANDED_WIDTH_PX : COLLAPSED_WIDTH_PX;

    // Auto-fetch when opening with a selected anchor
    $: if ($itemExplorerOpen && exploreMode !== 'none') {
        const needBuild = exploreMode === 'unit' && (!buildData || staleBuild);
        const needItems = $itemExplorerTab === 'items' && (!data || staleItems);
        if (needBuild || needItems) run();
    }

    // Close help popover when leaving the items tab
    $: if ($itemExplorerTab !== 'items' && sortHelpOpen) sortHelpOpen = false;

    function toggleOpen() {
        const nextOpen = !$itemExplorerOpen;
        itemExplorerOpen.set(nextOpen);
        if (!nextOpen) {
            posthog.capture('item_explorer_closed');
            return;
        }
        posthog.capture('item_explorer_opened');
        if (exploreMode === 'unit' && selectedUnit && (!buildData || ($itemExplorerTab === 'items' && !data))) run();
        if (exploreMode === 'item' && selectedItem && $itemExplorerTab === 'items' && !data) run();
    }

    async function run() {
        if (exploreMode === 'none') return;

        const version = ++fetchVersion;
        loading = true;
        error = null;
        stale = false;

        const needBuild = exploreMode === 'unit' && (!buildData || staleBuild);
        const needItems = $itemExplorerTab === 'items' && (!data || staleItems);

        try {
            const [buildResult, itemsResult] =
                exploreMode === 'unit'
                    ? await Promise.all([
                        needBuild
                            ? fetchUnitBuild(selectedUnit, contextTokens, {
                                slots: 3,
                                itemTypes: activeItemTypes,
                                itemPrefixes: activeItemPrefixes,
                            })
                            : Promise.resolve(buildData),
                        needItems
                            ? fetchUnitItems(selectedUnit, contextTokens, {
                                sortMode: $itemExplorerSortMode,
                                itemTypes: activeItemTypes,
                                itemPrefixes: activeItemPrefixes,
                            })
                            : Promise.resolve(data),
                    ])
                    : await Promise.all([
                        Promise.resolve(buildData),
                        needItems
                            ? fetchItemUnits(selectedItem, contextTokens, { sortMode: $itemExplorerSortMode })
                            : Promise.resolve(data),
                    ]);
            if (version !== fetchVersion) return;

            if (needBuild) {
                buildData = buildResult;
                staleBuild = false;
                lastBuildQueryKey = buildQueryKey;
            }
            if (needItems) {
                data = itemsResult;
                staleItems = false;
                lastItemsQueryKey = itemsQueryKey;
            }

            posthog.capture('item_explorer_run', {
                anchor_type: exploreMode,
                unit: exploreMode === 'unit' ? selectedUnit : null,
                item: exploreMode === 'item' ? selectedItem : null,
                filter_count: contextTokens.length,
                build_items: buildResult?.builds?.length ?? 0,
                result_count: itemsResult?.items?.length ?? itemsResult?.units?.length ?? 0,
                tab: $itemExplorerTab,
                sort_mode: $itemExplorerSortMode,
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

    function addEquipped(row) {
        const token = row?.token;
        if (!token) return;
        addToken(token, 'item_explorer');

        const parsed = parseToken(token);
        posthog.capture('item_added_from_explorer', {
            unit: parsed?.unit ?? null,
            item: parsed?.item ?? null,
            token,
            delta: row?.delta ?? null,
            anchor_type: exploreMode,
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

    function toggleSortHelp() {
        sortHelpOpen = !sortHelpOpen;
    }

    function closeSortHelp() {
        sortHelpOpen = false;
    }

    async function toggleNecessity(row) {
        const eqToken = row?.token;
        if (!eqToken) return;
        const parsed = parseToken(eqToken);
        if (parsed.type !== 'equipped' || !parsed.unit || !parsed.item) return;

        if (necessityOpenToken === eqToken) {
            necessityOpenToken = null;
            return;
        }

        necessityOpenToken = eqToken;
        const hasExtraContext = contextTokens.length > 0;

        // If we already computed an estimate for this context, don't refetch.
        if (necessityByToken[eqToken]) return;

        // In the default (unfiltered) view, necessity is precomputed and already present on the row.
        if (!hasExtraContext && row?.necessity) return;

        necessityLoadingToken = eqToken;
        necessityErrorByToken = { ...necessityErrorByToken, [eqToken]: null };

        try {
            const res = await fetchItemNecessity(parsed.unit, parsed.item, contextTokens, { outcome: 'top4' });
            necessityByToken = { ...necessityByToken, [eqToken]: res };
        } catch (e) {
            necessityErrorByToken = { ...necessityErrorByToken, [eqToken]: e?.message ?? String(e) };
        } finally {
            if (necessityLoadingToken === eqToken) necessityLoadingToken = null;
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
            {#if exploreMode === 'none'}
                <div class="empty-state">
                    <div class="empty-icon">
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                            <circle cx="11" cy="11" r="8"/>
                            <path d="m21 21-4.35-4.35"/>
                        </svg>
                    </div>
                    <div class="empty-text">Select a unit or item to explore</div>
                    <div class="empty-hint">Search for a champion like "Miss Fortune" or an item like "Guinsoo"</div>
                </div>
            {:else if exploreMode === 'unit'}
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
                    {#if availableUnits.length > 1 || availableItems.length > 0}
                        <div class="unit-select-row">
                            {#if availableItems.length > 0}
                                <label class="select">
                                    <span>View</span>
                                    <select bind:value={$itemExplorerFocus} disabled={loading}>
                                        <option value="unit">Champion</option>
                                        <option value="item">Item</option>
                                    </select>
                                </label>
                            {/if}
                            {#if availableUnits.length > 1}
                                <label class="select">
                                    <span>Champion</span>
                                    <select bind:value={$itemExplorerUnit} disabled={loading}>
                                        {#each availableUnits as unit (unit)}
                                            <option value={unit}>{unitDisplayName(unit)}</option>
                                        {/each}
                                    </select>
                                </label>
                            {/if}
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
                            <span class="sort-label">
                                Sort
                                <button
                                    type="button"
                                    class="sort-help-btn"
                                    title="How sorting works"
                                    aria-label="How sorting works"
                                    aria-expanded={sortHelpOpen}
                                    on:click|stopPropagation={toggleSortHelp}
                                >
                                    ?
                                </button>
                            </span>
                            <select bind:value={$itemExplorerSortMode} on:change={run}>
                                <option value="helpful">Best first</option>
                                <option value="harmful">Worst first</option>
                                <option value="impact">Most impact</option>
                                <option value="necessity">Most necessary (AIPW)</option>
                            </select>
                        </label>
                        {#if sortHelpOpen}
                            <div class="sort-help" role="dialog" aria-label="Sort mode help">
                                <div class="sort-help-header">
                                    <div class="sort-help-title">How sorting works</div>
                                    <button
                                        type="button"
                                        class="sort-help-close"
                                        on:click|stopPropagation={closeSortHelp}
                                        aria-label="Close sort help"
                                    >
                                        ×
                                    </button>
                                </div>

                                <div class="sort-help-section">
                                    <div class="sort-help-mode">Most necessary (AIPW)</div>
                                    <div class="sort-help-text">
                                        Estimates how much the item <em>causally</em> increases your chance to Top4 when equipped on the selected unit.
                                        It answers: “If two endgame boards look similar, what’s the expected Top4 difference if this unit has this item vs not?”
                                    </div>
                                    <div class="sort-help-text">
                                        Inspired by <a href="https://tftable.cc/" target="_blank" rel="noopener noreferrer">TFTable</a>’s “Necessity” idea (“what do you lose when the item is missing?”),
                                        but implemented as a doubly‑robust causal estimate instead of a baseline‑adjusted placement comparison. This helps reduce bias from strong boards being more likely to
                                        have the components, tempo, and shops to build the item in the first place.
                                    </div>
                                    <div class="sort-help-text">
                                        Under the hood:
                                        <ul>
                                            <li><span class="k">Treatment (T)</span>: this item is equipped on this unit (<code>E:Unit|Item</code>).</li>
                                            <li><span class="k">Outcome (Y)</span>: Top4 (1 if placement ≤ 4, else 0).</li>
                                            <li><span class="k">Context (X)</span>: the rest of the board (units + traits) and strength proxies (unit count, 2★/3★ count, gold-value proxy, and <em>rest-of-board</em> item counts).</li>
                                        </ul>
                                    </div>
                                    <div class="sort-help-text">
                                        We fit two models and combine them using a doubly‑robust estimator (AIPW):
                                        a propensity model <code>P(T=1|X)</code> (how “buildable” the item is in that context) and outcome models for <code>E[Y|T=1,X]</code> and <code>E[Y|T=0,X]</code>.
                                        If either the propensity model or the outcome models are reasonably accurate, AIPW still gives a good estimate.
                                        We use cross‑fitting to reduce overfitting bias.
                                    </div>
                                    <div class="sort-help-text">
                                        Reliability guardrails:
                                        <ul>
                                            <li><span class="k">Overlap trimming</span>: boards where the model says the item is almost never/always built are trimmed (positivity). High “trimmed %” means the estimate relies on a narrow slice of data.</li>
                                            <li><span class="k">Auto 2★+ scope</span>: if most samples are 2★+, we automatically scope to 2★+ to avoid mixing “1★ desperation” boards. Add a star filter (e.g. <code>U:Unit:1</code>) to override.</li>
                                        </ul>
                                    </div>
                                    <div class="sort-help-text">
                                        Performance note: in the default view, necessity values are precomputed and load fast. If you add extra filters, we compute a fast context-specific estimate for ranking; expanding a row runs the full causal estimate for your exact context (and can take longer).
                                    </div>
                                    <div class="sort-help-text">
                                        Interpreting the number: <span class="k">+3.0pp</span> means <em>+3 percentage points</em> Top4 chance (e.g. 52% → 55%), not “+3%”.
                                    </div>
                                </div>

                                <div class="sort-help-section">
                                    <div class="sort-help-mode">Best first</div>
                                    <div class="sort-help-text">
                                        Sorts by the item’s average placement when equipped on the selected unit (lower is better), with small‑sample shrinkage so rare items don’t jump to the top from noise.
                                    </div>
                                </div>

                                <div class="sort-help-section">
                                    <div class="sort-help-mode">Worst first</div>
                                    <div class="sort-help-text">
                                        The reverse of “Best first” (highest average placement at the top).
                                    </div>
                                </div>

                                <div class="sort-help-section">
                                    <div class="sort-help-mode">Most impact</div>
                                    <div class="sort-help-text">
                                        Sorts by absolute change in (shrunk) average placement versus the current filtered baseline — shows the most polarizing items first (good or bad).
                                    </div>
                                </div>
                            </div>
                        {/if}
                    </div>
                    {#if showNecessity && data?.scope}
                        <div class="scope-note">
                            Necessity scope: {data.scope.unit_stars_min}★+{data.scope.auto ? ' (auto)' : ''}
                        </div>
                    {/if}
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
                                {@const tau = necessityByToken[item.token]?.effect?.tau ?? item.necessity?.tau}
                                <div
                                    class="row item-row"
                                    role="button"
                                    tabindex="0"
                                    on:click={() => addEquipped(item)}
                                    on:keydown={(e) => {
                                        if (e.key === 'Enter' || e.key === ' ') {
                                            e.preventDefault();
                                            addEquipped(item);
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
                                            class="row-delta {showNecessity ? necessityClass(tau ?? null) : deltaClass(item.delta)}"
                                        >
                                            {#if showNecessity}
                                                {#if tau !== null && tau !== undefined}
                                                    {fmtPp(tau)}
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
                                        disabled={necessityLoadingToken === item.token}
                                    >
                                        N
                                    </button>
                                    <div class="add-icon">+</div>
                                </div>
                                {#if necessityOpenToken === item.token}
                                    <div class="necessity-panel">
                                        {#if necessityLoadingToken === item.token}
                                            <div class="necessity-row">Estimating…</div>
                                        {:else if necessityErrorByToken[item.token]}
                                            <div class="necessity-row error">{necessityErrorByToken[item.token]}</div>
                                        {:else if necessityByToken[item.token]}
                                            {@const r = necessityByToken[item.token]}
                                            {#if r.effect}
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
                                            {:else if r.warning}
                                                <div class="necessity-row warn">{r.warning}</div>
                                                {#if r.overlap}
                                                    <div class="necessity-row meta">
                                                        <span>{r.treatment?.n_treated?.toLocaleString?.() ?? '—'} with</span>
                                                        <span class="dot">&bull;</span>
                                                        <span>{r.treatment?.n_control?.toLocaleString?.() ?? '—'} without</span>
                                                        <span class="dot">&bull;</span>
                                                        <span>{r.overlap?.n_used?.toLocaleString?.() ?? '—'} used</span>
                                                        <span class="dot">&bull;</span>
                                                        <span>{r.overlap?.frac_trimmed !== undefined ? Math.round(r.overlap.frac_trimmed * 100) : '—'}% trimmed</span>
                                                    </div>
                                                {/if}
                                            {/if}
                                            {#if r.warnings?.length}
                                                {#each r.warnings as w (w)}
                                                    <div class="necessity-row warn">{w}</div>
                                                {/each}
                                            {/if}
                                            {#if !r.effect}
                                                <div class="necessity-row meta">No estimate available.</div>
                                            {/if}
                                        {:else if item.necessity}
                                            {@const r = item.necessity}
                                            <div class="necessity-row">
                                                <span class="k">{r.method === 'aipw' ? 'AIPW ΔTop4' : 'Fast ΔTop4'}</span>
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
                                        {:else}
                                            <div class="necessity-row meta">No estimate available.</div>
                                        {/if}
                                    </div>
                                {/if}
                            {/each}
                        {/if}
                    {/if}
                </div>
            {:else if exploreMode === 'item'}
                <div class="panel-header">
                    <div class="unit-info">
                        {#if itemIcon(selectedItem) && !hasIconFailed('item', selectedItem)}
                            <img
                                class="unit-icon"
                                src={itemIcon(selectedItem)}
                                alt=""
                                on:error={() => markIconFailed('item', selectedItem)}
                            />
                        {:else}
                            <div class="unit-fallback"></div>
                        {/if}
                        <div class="unit-details">
                            <div class="unit-name">{itemDisplayName(selectedItem)}</div>
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
                    {#if availableItems.length > 1 || availableUnits.length > 0}
                        <div class="unit-select-row">
                            {#if availableUnits.length > 0}
                                <label class="select">
                                    <span>View</span>
                                    <select bind:value={$itemExplorerFocus} disabled={loading}>
                                        <option value="unit">Champion</option>
                                        <option value="item">Item</option>
                                    </select>
                                </label>
                            {/if}
                            {#if availableItems.length > 1}
                                <label class="select">
                                    <span>Item</span>
                                    <select bind:value={$itemExplorerItem} disabled={loading}>
                                        {#each availableItems as it (it)}
                                            <option value={it}>{itemDisplayName(it)}</option>
                                        {/each}
                                    </select>
                                </label>
                            {/if}
                        </div>
                    {/if}
                </div>

                <div class="tabs">
                    <button
                        class="tab"
                        class:active={$itemExplorerTab === 'items'}
                        on:click={() => itemExplorerTab.set('items')}
                    >
                        Holders
                        {#if holders.length > 0}
                            <span class="tab-count">{holders.length}</span>
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

                <div class="sort-bar">
                    <label class="select">
                        <span class="sort-label">
                            Sort
                            <button
                                type="button"
                                class="sort-help-btn"
                                title="How sorting works"
                                aria-label="How sorting works"
                                aria-expanded={sortHelpOpen}
                                on:click|stopPropagation={toggleSortHelp}
                            >
                                ?
                            </button>
                        </span>
                        <select bind:value={$itemExplorerSortMode} on:change={run}>
                            <option value="helpful">Best first</option>
                            <option value="harmful">Worst first</option>
                            <option value="impact">Most impact</option>
                            <option value="necessity">Most necessary (AIPW)</option>
                        </select>
                    </label>
                    {#if sortHelpOpen}
                        <div class="sort-help" role="dialog" aria-label="Sort mode help">
                            <div class="sort-help-header">
                                <div class="sort-help-title">How sorting works</div>
                                <button
                                    type="button"
                                    class="sort-help-close"
                                    on:click|stopPropagation={closeSortHelp}
                                    aria-label="Close sort help"
                                >
                                    ×
                                </button>
                            </div>

                            <div class="sort-help-section">
                                <div class="sort-help-mode">Most necessary (AIPW)</div>
                                <div class="sort-help-text">
                                    Estimates how much the item <em>causally</em> increases your chance to Top4 when equipped on a unit.
                                    It answers: “If two endgame boards look similar, what’s the expected Top4 difference if that unit has this item vs not?”
                                </div>
                                <div class="sort-help-text">
                                    Inspired by <a href="https://tftable.cc/" target="_blank" rel="noopener noreferrer">TFTable</a>’s “Necessity” idea (“what do you lose when the item is missing?”),
                                    but implemented as a doubly‑robust causal estimate instead of a baseline‑adjusted placement comparison. This helps reduce bias from strong boards being more likely to
                                    have the components, tempo, and shops to build the item in the first place.
                                </div>
                                <div class="sort-help-text">
                                    Under the hood:
                                    <ul>
                                        <li><span class="k">Treatment (T)</span>: this item is equipped on this unit (<code>E:Unit|Item</code>).</li>
                                        <li><span class="k">Outcome (Y)</span>: Top4 (1 if placement ≤ 4, else 0).</li>
                                        <li><span class="k">Context (X)</span>: the rest of the board (units + traits) and strength proxies (unit count, 2★/3★ count, gold-value proxy, and <em>rest-of-board</em> item counts).</li>
                                    </ul>
                                </div>
                                <div class="sort-help-text">
                                    We fit two models and combine them using a doubly‑robust estimator (AIPW):
                                    a propensity model <code>P(T=1|X)</code> (how “buildable” the item is in that context) and outcome models for <code>E[Y|T=1,X]</code> and <code>E[Y|T=0,X]</code>.
                                    If either the propensity model or the outcome models are reasonably accurate, AIPW still gives a good estimate.
                                    We use cross‑fitting to reduce overfitting bias.
                                </div>
                                <div class="sort-help-text">
                                    Reliability guardrails:
                                    <ul>
                                        <li><span class="k">Overlap trimming</span>: boards where the model says the item is almost never/always built are trimmed (positivity). High “trimmed %” means the estimate relies on a narrow slice of data.</li>
                                        <li><span class="k">Auto 2★+ scope</span>: if most samples are 2★+, we automatically scope to 2★+ to avoid mixing “1★ desperation” boards. Add a star filter (e.g. <code>U:Unit:1</code>) to override.</li>
                                    </ul>
                                </div>
                                <div class="sort-help-text">
                                    Performance note: in the default view, necessity values are precomputed and load fast. If you add extra filters, we compute a fast context-specific estimate for ranking; expanding a row runs the full causal estimate for your exact context (and can take longer).
                                </div>
                                <div class="sort-help-text">
                                    Interpreting the number: <span class="k">+3.0pp</span> means <em>+3 percentage points</em> Top4 chance (e.g. 52% → 55%), not “+3%”.
                                </div>
                            </div>

                            <div class="sort-help-section">
                                <div class="sort-help-mode">Best first</div>
                                <div class="sort-help-text">
                                    Sorts by the unit’s (shrunk) average placement when this item is equipped (lower is better).
                                </div>
                            </div>

                            <div class="sort-help-section">
                                <div class="sort-help-mode">Worst first</div>
                                <div class="sort-help-text">
                                    The reverse of “Best first” (highest average placement at the top).
                                </div>
                            </div>

                            <div class="sort-help-section">
                                <div class="sort-help-mode">Most impact</div>
                                <div class="sort-help-text">
                                    Sorts by absolute change in (shrunk) average placement versus the current filtered baseline — shows the most polarizing holders first (good or bad).
                                </div>
                            </div>
                        </div>
                    {/if}
                </div>

                <div class="list-container">
                    {#if loading && holders.length === 0}
                        <div class="loading-skeleton">
                            {#each Array(6) as _, i}
                                <div class="skeleton-row"></div>
                            {/each}
                        </div>
                    {:else if holders.length === 0 && !loading}
                        <div class="no-items">
                            No unit holders found with sufficient data.
                            Try adjusting filters or lowering the sample threshold.
                        </div>
                    {:else}
                        {#each holders as h, idx (h.unit)}
                            {@const tau = necessityByToken[h.token]?.effect?.tau ?? h.necessity?.tau}
                            <div
                                class="row item-row"
                                role="button"
                                tabindex="0"
                                on:click={() => addEquipped(h)}
                                on:keydown={(e) => {
                                    if (e.key === 'Enter' || e.key === ' ') {
                                        e.preventDefault();
                                        addEquipped(h);
                                    }
                                }}
                            >
                                <div class="row-rank">#{idx + 1}</div>
                                <div class="item-icon-wrapper">
                                    {#if unitIcon(h.unit) && !hasIconFailed('unit', h.unit)}
                                        <img
                                            class="item-icon"
                                            src={unitIcon(h.unit)}
                                            alt=""
                                            loading="lazy"
                                            on:error={() => markIconFailed('unit', h.unit)}
                                        />
                                    {:else}
                                        <div class="item-fallback"></div>
                                    {/if}
                                </div>
                                <div class="item-info">
                                    <div class="item-name">{unitDisplayName(h.unit)}</div>
                                    <div class="item-stats">
                                        <span class="stat-n">{h.n.toLocaleString()}</span>
                                        <span class="stat-sep">&bull;</span>
                                        <span class="stat-pct">{fmtPct(h.pct_of_base)} hold</span>
                                    </div>
                                </div>
                                <div class="row-metrics">
                                    <div class="row-avg"><AvgPlacement value={h.avg_placement} /></div>
                                    <div
                                        class="row-delta {showNecessity ? necessityClass(tau ?? null) : deltaClass(h.delta)}"
                                    >
                                        {#if showNecessity}
                                            {#if tau !== null && tau !== undefined}
                                                {fmtPp(tau)}
                                            {:else}
                                                —
                                            {/if}
                                        {:else}
                                            {fmtDelta(h.delta)}
                                        {/if}
                                    </div>
                                </div>
                                <button
                                    class="necessity-btn"
                                    title="Estimate necessity (AIPW ΔTop4)"
                                    aria-label="Estimate necessity"
                                    on:click|stopPropagation={() => toggleNecessity(h)}
                                    disabled={necessityLoadingToken === h.token}
                                >
                                    N
                                </button>
                                <div class="add-icon">+</div>
                            </div>

                            {#if necessityOpenToken === h.token}
                                <div class="necessity-panel">
                                    {#if necessityLoadingToken === h.token}
                                        <div class="necessity-row">Estimating…</div>
                                    {:else if necessityErrorByToken[h.token]}
                                        <div class="necessity-row error">{necessityErrorByToken[h.token]}</div>
                                    {:else if necessityByToken[h.token]}
                                        {@const r = necessityByToken[h.token]}
                                        {#if r.effect}
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
                                        {:else if r.warning}
                                            <div class="necessity-row warn">{r.warning}</div>
                                            {#if r.overlap}
                                                <div class="necessity-row meta">
                                                    <span>{r.treatment?.n_treated?.toLocaleString?.() ?? '—'} with</span>
                                                    <span class="dot">&bull;</span>
                                                    <span>{r.treatment?.n_control?.toLocaleString?.() ?? '—'} without</span>
                                                    <span class="dot">&bull;</span>
                                                    <span>{r.overlap?.n_used?.toLocaleString?.() ?? '—'} used</span>
                                                    <span class="dot">&bull;</span>
                                                    <span>{r.overlap?.frac_trimmed !== undefined ? Math.round(r.overlap.frac_trimmed * 100) : '—'}% trimmed</span>
                                                </div>
                                            {/if}
                                        {/if}
                                        {#if r.warnings?.length}
                                            {#each r.warnings as w (w)}
                                                <div class="necessity-row warn">{w}</div>
                                            {/each}
                                        {/if}
                                        {#if !r.effect}
                                            <div class="necessity-row meta">No estimate available.</div>
                                        {/if}
                                    {:else if h.necessity}
                                        {@const r = h.necessity}
                                        <div class="necessity-row">
                                            <span class="k">{r.method === 'aipw' ? 'AIPW ΔTop4' : 'Fast ΔTop4'}</span>
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
                                    {:else}
                                        <div class="necessity-row meta">No estimate available.</div>
                                    {/if}
                                </div>
                            {/if}
                        {/each}
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
        border-right: 1px solid var(--border);
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
        position: relative;
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

    .sort-label {
        display: inline-flex;
        align-items: center;
        gap: 8px;
    }

    .sort-help-btn {
        width: 18px;
        height: 18px;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: var(--bg-secondary);
        color: var(--text-muted);
        font-weight: 700;
        font-size: 12px;
        line-height: 16px;
        padding: 0;
        display: inline-flex;
        align-items: center;
        justify-content: center;
        cursor: pointer;
    }

    .sort-help-btn:hover {
        color: var(--text);
        border-color: var(--accent);
    }

    .sort-help {
        position: absolute;
        top: 48px;
        left: 0;
        right: 0;
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 12px;
        box-shadow: 0 12px 32px rgba(0, 0, 0, 0.35);
        padding: 12px 12px 6px;
        z-index: 50;
        max-height: 60vh;
        overflow: auto;
    }

    .sort-help-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 12px;
        margin-bottom: 8px;
    }

    .sort-help-title {
        font-weight: 700;
        color: var(--text);
    }

    .sort-help-close {
        border: 0;
        background: transparent;
        color: var(--text-muted);
        font-size: 18px;
        cursor: pointer;
        padding: 4px 6px;
        line-height: 1;
        border-radius: 8px;
    }

    .sort-help-close:hover {
        background: rgba(255, 255, 255, 0.06);
        color: var(--text);
    }

    .sort-help-section {
        padding: 10px 8px;
        border-top: 1px solid var(--border);
    }

    .sort-help-section:first-of-type {
        border-top: 0;
        padding-top: 8px;
    }

    .sort-help-mode {
        font-weight: 700;
        margin-bottom: 6px;
    }

    .sort-help-text {
        color: var(--text-muted);
        font-size: 13px;
        line-height: 1.4;
        margin-bottom: 8px;
    }

    .sort-help-text ul {
        margin: 6px 0 0 18px;
        padding: 0;
    }

    .sort-help-text li {
        margin: 4px 0;
    }

    .sort-help-text .k {
        color: var(--text);
        font-weight: 600;
    }

    .scope-note {
        margin: 8px 12px 0;
        font-size: 12px;
        color: var(--text-muted);
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
            border-right: none;
            border-bottom: 1px solid var(--border);
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
            height: min(400px, 50vh);
        }

        .list-container {
            max-height: 45vh;
        }
    }
</style>
