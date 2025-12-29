<script>
    import { onMount } from 'svelte';
    import { fetchClusters, fetchClusterPlaybook } from '../api.js';
    import {
        selectedTokens,
        activeTypes,
        addToken,
        equipItemOnUnit,
        addTokens,
        setTokens,
        clearTokens,
        recordUiAction,
        setHighlightedTokens,
        clearHighlightedTokens,
        clusterExplorerOpen,
        clusterExplorerRunRequest
    } from '../stores/state.js';
    import { getTokenType, getTokenLabel } from '../utils/tokens.js';
    import { getSearchIndex } from '../utils/searchIndexCache.js';
    import { getDisplayName, getIconUrl, hasIconFailed, markIconFailed } from '../stores/assets.js';
    import { getPlacementColor } from '../utils/colors.js';
    import AvgPlacement from './AvgPlacement.svelte';
    import posthog from '../client/posthog';

    const COLLAPSED_WIDTH_PX = 46;
    const MIN_WIDTH_PX = 340;
    const MAX_WIDTH_PX = 720;
    const SPLIT_THRESHOLD_PX = 640;
    const TRAIT_PILL_LIMIT = 8;

    let loading = false;
    let error = null;
    let data = null;
    let selectedClusterId = null;
    let sortBy = 'avg'; // avg | size | top4
    let tokenLabelIndex = new Map();

    let playbookLoading = false;
    let playbookError = null;
    let playbook = null;
    let playbookFetchVersion = 0;
    let hoveredItemId = null;
    let traitsExpanded = false;

    let rootEl;
    let measuredWidth = 0;
    let widthPx = 440;
    let view = 'list'; // list | details (only used in narrow mode)
    let resizing = false;
    let resizeStartX = 0;
    let resizeStartWidth = 0;

    let params = {
        n_clusters: 15,
        min_token_freq: 100,
        min_cluster_size: 50,
        top_k_tokens: 10,
        random_state: 42
    };

    let lastTokensKey = '';
    let stale = false;
    let lastRunRequest = 0;

    $: tokensKey = $selectedTokens.slice().sort().join(',');
    $: if ($clusterExplorerOpen && lastTokensKey && tokensKey !== lastTokensKey) stale = true;

    // Derive API params from global activeTypes store
    $: apiParams = {
        ...params,
        use_units: $activeTypes.has('unit'),
        use_traits: $activeTypes.has('trait'),
        use_items: $activeTypes.has('item')
    };

    // Track activeTypes changes for stale detection
    let lastActiveTypesKey = '';
    $: activeTypesKey = [...$activeTypes].sort().join(',');
    $: if ($clusterExplorerOpen && data && lastActiveTypesKey && activeTypesKey !== lastActiveTypesKey) stale = true;

    $: clusters = data?.clusters ?? [];
    $: warning = data?.meta?.warning ?? null;

    $: sortedClusters = (() => {
        const list = [...clusters];
        if (sortBy === 'size') list.sort((a, b) => b.size - a.size);
        else if (sortBy === 'top4') list.sort((a, b) => b.top4_rate - a.top4_rate);
        else list.sort((a, b) => a.avg_placement - b.avg_placement);
        return list;
    })();

    $: selectedCluster = sortedClusters.find(c => c.cluster_id === selectedClusterId) ?? null;
    $: if (view === 'details' && !selectedCluster) view = 'list';

    $: drivers = playbook?.drivers ?? [];
    $: killers = playbook?.killers ?? [];

    // Unified token list sorted by lift
    $: unifiedTokens = (() => {
        if (!selectedCluster) return [];
        const all = [];
        if ($activeTypes.has('unit')) {
            for (const tok of (selectedCluster.top_units ?? [])) {
                all.push({ ...tok, type: 'unit' });
            }
        }
        if ($activeTypes.has('trait')) {
            for (const tok of (selectedCluster.top_traits ?? [])) {
                all.push({ ...tok, type: 'trait' });
            }
        }
        if ($activeTypes.has('item')) {
            for (const tok of (selectedCluster.top_items ?? [])) {
                all.push({ ...tok, type: 'item' });
            }
        }
        all.sort((a, b) => (b.lift ?? 0) - (a.lift ?? 0));
        return all;
    })();

    $: signatureTokens = selectedCluster?.signature_tokens ?? [];
    $: signatureUnits = signatureTokens.filter((t) => getTokenType(t) === 'unit');
    $: signatureTraits = signatureTokens.filter((t) => getTokenType(t) === 'trait');
    $: signatureItems = signatureTokens.filter((t) => getTokenType(t) === 'item');

    $: compView = playbook?.comp_view ?? null;
    $: compTraits = compView?.traits ?? [];
    $: compItems = compView?.items ?? [];
    $: bestItemsByUnit = compView?.best_items_by_unit ?? {};

    $: traitPills = (() => {
        if (compTraits.length > 0) {
            const list = compTraits
                .map((tr) => {
                    const tok = tr?.active_token ?? tr?.token;
                    const label = tr?.active_label ?? (tok ? tokenText(tok) : '');
                    const pct = tr?.active_pct_at_least ?? tr?.pct ?? 0;
                    return { tok, label, pct };
                })
                .filter((t) => !!t.tok)
                .sort((a, b) => (b.pct ?? 0) - (a.pct ?? 0));
            return list;
        }
        return signatureTraits.map((t) => ({ tok: t, label: tokenText(t), pct: 0 }));
    })();

    $: visibleTraitPills = traitsExpanded ? traitPills : traitPills.slice(0, TRAIT_PILL_LIMIT);
    $: hiddenTraitCount = Math.max(0, traitPills.length - TRAIT_PILL_LIMIT);

    $: itemTiles = (() => {
        if (compItems.length > 0) {
            return compItems.map((row) => {
                const itemId = row?.item;
                const label = row?.label ?? (itemId ? tokenText(`I:${itemId}`) : '');
                const holder = row?.best_holder?.unit ?? null;
                return { itemId, label, holder };
            }).filter((r) => !!r.itemId);
        }
        return signatureItems
            .map((t) => ({ itemId: typeof t === 'string' ? t.slice(2) : null, label: tokenText(t), holder: null }))
            .filter((r) => !!r.itemId);
    })();

    $: hoveredHolderUnitId = (() => {
        if (!hoveredItemId) return null;
        const row = (compItems ?? []).find((r) => r?.item === hoveredItemId);
        return row?.best_holder?.unit ?? null;
    })();

    $: holderCountByUnit = (() => {
        const out = {};
        for (const row of (compItems ?? [])) {
            const unitId = row?.best_holder?.unit;
            if (typeof unitId !== 'string' || !unitId) continue;
            out[unitId] = (out[unitId] || 0) + 1;
        }
        return out;
    })();

    $: compUnits = (() => {
        if (!selectedCluster) return [];
        const signatureSet = new Set(signatureUnits);
        const rawUnits = (selectedCluster.top_units ?? []).filter((u) => typeof u?.token === 'string');
        const sorted = rawUnits.slice().sort((a, b) => (b.pct ?? 0) - (a.pct ?? 0));

        const byToken = new Map(sorted.map((u) => [u.token, u]));
        const holderTokens = (() => {
            const out = new Set();
            for (const row of (compItems ?? [])) {
                const unitId = row?.best_holder?.unit;
                if (typeof unitId === 'string' && unitId) out.add(`U:${unitId}`);
            }
            return out;
        })();

        const out = [];
        const seen = new Set();

        for (const row of sorted) {
            if (!signatureSet.has(row.token)) continue;
            if (seen.has(row.token)) continue;
            seen.add(row.token);
            out.push({ ...row, core: true, holder: holderTokens.has(row.token) });
        }

        for (const tok of holderTokens) {
            if (seen.has(tok)) continue;
            seen.add(tok);
            const row = byToken.get(tok);
            out.push({ ...(row ?? { token: tok, pct: null, base_pct: null, lift: null }), core: signatureSet.has(tok), holder: true });
        }

        for (const row of sorted) {
            if (signatureSet.has(row.token)) continue;
            if (seen.has(row.token)) continue;
            seen.add(row.token);
            out.push({ ...row, core: false, holder: holderTokens.has(row.token) });
        }

        for (const t of signatureUnits) {
            if (seen.has(t)) continue;
            seen.add(t);
            out.push({ token: t, pct: null, base_pct: null, lift: null, core: true, holder: holderTokens.has(t) });
        }

        return out.slice(0, 9);
    })();

    $: sidebarWidth = $clusterExplorerOpen ? widthPx : COLLAPSED_WIDTH_PX;
    $: isNarrow = $clusterExplorerOpen && (measuredWidth || sidebarWidth) < SPLIT_THRESHOLD_PX;

    function clamp(n, min, max) {
        return Math.max(min, Math.min(max, n));
    }

    onMount(() => {
        try {
            const saved = parseInt(localStorage.getItem('clusterExplorerWidth') || '', 10);
            if (Number.isFinite(saved)) {
                widthPx = clamp(saved, MIN_WIDTH_PX, MAX_WIDTH_PX);
            }
        } catch {
            // ignore
        }

        (async () => {
            try {
                const index = await getSearchIndex();
                tokenLabelIndex = new Map(index.map((e) => [e.token, e.label]));
            } catch {
                // ignore
            }
        })();

        const ro = new ResizeObserver(entries => {
            const entry = entries[0];
            if (entry) measuredWidth = entry.contentRect.width;
        });
        if (rootEl) ro.observe(rootEl);
        return () => ro.disconnect();
    });

    function toggleOpen() {
        const nextOpen = !$clusterExplorerOpen;
        clusterExplorerOpen.set(nextOpen);
        if (!nextOpen) {
            posthog.capture('explorer_closed');
            selectedClusterId = null;
            view = 'list';
            stale = false;
            clearHighlightedTokens();
            return;
        }
        posthog.capture('explorer_opened');
        view = 'list';
        if (!data) run();
    }

    $: if (
        $clusterExplorerOpen &&
        $clusterExplorerRunRequest !== lastRunRequest &&
        !loading
    ) {
        lastRunRequest = $clusterExplorerRunRequest;
        run();
    }

    async function run() {
        recordUiAction('clusters_run', 'cluster', { tokens: $selectedTokens });
        loading = true;
        error = null;
        stale = false;
        selectedClusterId = null;
        view = 'list';
        clearHighlightedTokens();
        lastTokensKey = tokensKey;
        lastActiveTypesKey = activeTypesKey;

        try {
            data = await fetchClusters($selectedTokens, apiParams);
            posthog.capture('clustering_run', {
                n_clusters: apiParams.n_clusters,
                use_units: apiParams.use_units,
                use_traits: apiParams.use_traits,
                use_items: apiParams.use_items,
                filter_count: $selectedTokens.length,
                result_count: data?.clusters?.length ?? 0
            });
        } catch (e) {
            error = e?.message ?? String(e);
        } finally {
            loading = false;
        }
    }

    function selectCluster(c) {
        selectedClusterId = c.cluster_id;
        setHighlightedTokens(c.signature_tokens ?? []);
        playbook = null;
        playbookError = null;
        posthog.capture('cluster_selected', {
            cluster_id: c.cluster_id,
            cluster_size: c.size,
            avg_placement: c.avg_placement
        });
        if (isNarrow) view = 'details';

        // Playbook requires fresh clustering results for the current filters.
        if (!stale) {
            loadPlaybook(c.cluster_id);
        }
    }

    async function loadPlaybook(clusterId) {
        const version = ++playbookFetchVersion;
        playbookLoading = true;
        playbookError = null;

        try {
            const res = await fetchClusterPlaybook($selectedTokens, clusterId, apiParams);
            if (version !== playbookFetchVersion) return;
            playbook = res;
            posthog.capture('cluster_playbook_loaded', {
                cluster_id: clusterId,
                candidates_scored: res?.meta?.candidates_scored ?? 0,
            });
        } catch (e) {
            if (version !== playbookFetchVersion) return;
            playbookError = e?.message ?? String(e);
        } finally {
            if (version === playbookFetchVersion) playbookLoading = false;
        }
    }

    function tokenText(token) {
        return tokenLabelIndex.get(token) ?? getTokenLabel(token, getDisplayName);
    }

    function tokenIcon(token) {
        const type = getTokenType(token);
        if (type === 'unknown' || type === 'equipped') return null;
        return getIconUrl(type, token.slice(2));
    }

    function tokenTypeClass(token) {
        const t = getTokenType(token);
        return t === 'equipped' ? 'item' : t;
    }

    function exploreReplace() {
        if (!selectedCluster?.signature_tokens?.length) return;
        posthog.capture('cluster_explored', { cluster_id: selectedCluster.cluster_id, action: 'replace' });
        setTokens(selectedCluster.signature_tokens, 'cluster');
    }

    function exploreAdd() {
        if (!selectedCluster?.signature_tokens?.length) return;
        posthog.capture('cluster_explored', { cluster_id: selectedCluster.cluster_id, action: 'add' });
        addTokens(selectedCluster.signature_tokens, 'cluster');
    }

    function addOne(token) {
        addToken(token, 'cluster');
    }

    function excludeOne(token) {
        if (!token) return;
        const base = token.startsWith('-') || token.startsWith('!') ? token.slice(1) : token;
        addToken(`-${base}`, 'cluster_playbook');
    }

    function goBack() {
        selectedClusterId = null;
        clearHighlightedTokens();
        if (isNarrow) view = 'list';
    }

    function maxHist(hist) {
        return Math.max(1, ...(hist ?? [1]));
    }

    function fmtPct(x) {
        return `${Math.round((x ?? 0) * 100)}%`;
    }

    function fmtLift(x) {
        if (x === null || x === undefined) return '—';
        if (x >= 9.95) return '×10+';
        return `×${x.toFixed(2)}`;
    }

    function fmtSignedPct(x, digits = 1) {
        if (x === null || x === undefined) return '—';
        const v = (x ?? 0) * 100;
        const sign = v > 0 ? '+' : '';
        return `${sign}${v.toFixed(digits)}%`;
    }

    function fmtSigned(x, digits = 2) {
        if (x === null || x === undefined) return '—';
        const v = x ?? 0;
        const sign = v > 0 ? '+' : '';
        return `${sign}${v.toFixed(digits)}`;
    }

    function trailingNumber(label) {
        const m = String(label ?? '').trim().match(/(\d+)\s*$/);
        return m ? m[1] : null;
    }

    function stripTrailingNumber(label) {
        return String(label ?? '').trim().replace(/\s*\d+\s*$/, '');
    }

    function deltaClass(metric, value) {
        if (value === null || value === undefined) return 'neutral';
        const v = Number(value);
        if (!Number.isFinite(v)) return 'neutral';

        const eps = 0.005;
        if (metric === 'avg') {
            if (v < -0.05) return 'pos';
            if (v > 0.05) return 'neg';
            return 'neutral';
        }

        if (metric === 'eighth') {
            if (v > eps) return 'neg';
            if (v < -eps) return 'pos';
            return 'neutral';
        }

        // win/top4: higher is better
        if (v > eps) return 'pos';
        if (v < -eps) return 'neg';
        return 'neutral';
    }

    function signaturePreview(cluster, maxIcons = 8) {
        const sig = cluster?.signature_tokens ?? [];
        const tokens = sig.slice(0, maxIcons);
        const more = Math.max(0, sig.length - tokens.length);
        return { tokens, more };
    }

    function clusterShortName(cluster) {
        const sig = cluster?.signature_tokens ?? [];
        const units = sig.filter(t => t.startsWith('U:'));
        const traits = sig.filter(t => t.startsWith('T:'));
        const items = sig.filter(t => t.startsWith('I:'));

        if (units.length) {
            const u = units.slice(0, 2).map(tokenText);
            let name = u.join(' + ');
            if (traits.length) name += ` — ${tokenText(traits[0])}`;
            return name;
        }

        if (traits.length) {
            return traits.slice(0, 2).map(tokenText).join(' + ');
        }

        if (items.length) {
            return items.slice(0, 2).map(tokenText).join(' + ');
        }

        return `Cluster #${cluster?.cluster_id ?? ''}`.trim();
    }

    function clusterKeyToken(cluster) {
        const defs = cluster?.defining_units ?? [];
        if (defs.length) return defs[0];

        const topTraits = cluster?.top_traits ?? [];
        const trait = topTraits.find(t => (t.lift ?? 0) >= 1.6 && (t.pct ?? 0) >= 0.25);
        if (trait) return trait;

        const topUnits = cluster?.top_units ?? [];
        const unit = topUnits.find(t => (t.lift ?? 0) >= 1.6 && (t.pct ?? 0) >= 0.25);
        if (unit) return unit;

        return null;
    }

    function clusterBlurb(cluster) {
        const parts = [];
        const key = clusterKeyToken(cluster);
        if (key?.token) {
            parts.push(`${tokenText(key.token)} ${fmtLift(key.lift)}`);
        }
        parts.push(`Top4 ${fmtPct(cluster?.top4_rate)}`);
        parts.push(`Win ${fmtPct(cluster?.win_rate)}`);
        parts.push(`${Math.round((cluster?.share ?? 0) * 100)}% share`);
        return parts.join(' • ');
    }

    function onResizeStart(event) {
        resizing = true;
        resizeStartX = event.clientX;
        resizeStartWidth = widthPx;
        event.currentTarget.setPointerCapture(event.pointerId);
        event.preventDefault();
    }

    function onResizeMove(event) {
        if (!resizing) return;
        const next = resizeStartWidth + (resizeStartX - event.clientX);
        widthPx = clamp(next, MIN_WIDTH_PX, MAX_WIDTH_PX);
        event.preventDefault();
    }

    function onResizeEnd(event) {
        if (!resizing) return;
        resizing = false;
        try {
            localStorage.setItem('clusterExplorerWidth', String(Math.round(widthPx)));
        } catch {
            // ignore
        }
        event.preventDefault();
    }
</script>

<div
    class="cluster-explorer"
    data-walkthrough="clusterExplorer"
    class:open={$clusterExplorerOpen}
    class:resizing
    bind:this={rootEl}
    style={`width: ${sidebarWidth}px;`}
>
    <button class="toggle" on:click={toggleOpen} aria-label="Toggle explorer" aria-expanded={$clusterExplorerOpen}>
        <span class="toggle-title">Explorer</span>
        {#if $clusterExplorerOpen}
            <span class="toggle-sub">close</span>
        {:else}
            <span class="toggle-sub">explore comps</span>
        {/if}
    </button>

    {#if $clusterExplorerOpen}
        <div
            class="resizer"
            role="separator"
            aria-orientation="vertical"
            aria-label="Resize cluster explorer"
            on:pointerdown={onResizeStart}
            on:pointermove={onResizeMove}
            on:pointerup={onResizeEnd}
            on:pointercancel={onResizeEnd}
        ></div>

        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">Explorer</div>
                <div class="panel-meta">
                    {#if data?.base}
                        <span>{data.base.n.toLocaleString()} games</span>
                        <span class="dot">•</span>
                        <AvgPlacement value={data.base.avg_placement} suffix=" avg" />
                    {:else}
                        <span>{$selectedTokens.length} filters</span>
                    {/if}
                </div>
            </div>

            <div class="controls compact">
                <div class="controls-left">
                    <label class="control">
                        <span>k</span>
                        <input type="number" min="2" max="50" step="1" bind:value={params.n_clusters} />
                    </label>

                    <label class="control">
                        <span>min</span>
                        <input type="number" min="1" max="5000" step="10" bind:value={params.min_token_freq} />
                    </label>

                    <label class="select inline">
                        <span>Sort</span>
                        <select bind:value={sortBy}>
                            <option value="avg">best avg</option>
                            <option value="top4">best top4</option>
                            <option value="size">most played</option>
                        </select>
                    </label>

                    {#if data?.meta?.compute_ms}
                        <span class="tiny">{data.meta.compute_ms}ms</span>
                    {/if}
                </div>

                <button class="run" disabled={loading} on:click={run}>
                    {#if loading}Running…{:else if stale}Refresh{:else}Run{/if}
                </button>
            </div>

            {#if warning}
                <div class="callout warning">{warning}</div>
            {/if}
            {#if error}
                <div class="callout error">Failed to load clusters: {error}</div>
            {/if}

            {#if !loading && data && sortedClusters.length === 0 && !warning}
                <div class="callout">
                    No clusters met the minimum size. Try clearing filters, lowering <span class="mono">min</span>, or increasing the sample.
                    {#if $selectedTokens.length > 0}
                        <div class="callout-actions">
                            <button class="callout-btn" on:click={clearTokens}>Clear filters</button>
                        </div>
                    {/if}
                </div>
            {/if}

            <div class="content" class:narrow={isNarrow} class:showDetails={isNarrow && view === 'details'}>
                <div class="cluster-list">
                    {#each sortedClusters as c (c.cluster_id)}
                        {@const preview = signaturePreview(c)}
                        <button
                            class="cluster"
                            class:selected={c.cluster_id === selectedClusterId}
                            on:click={() => selectCluster(c)}
                        >
                            <div class="cluster-header">
                                <div class="cluster-left">
                                    <div class="cluster-name-row">
                                        <span class="cluster-tag">#{c.cluster_id}</span>
                                        <span class="cluster-name" title={clusterShortName(c)}>
                                            {clusterShortName(c)}
                                        </span>
                                    </div>
                                    <div class="cluster-blurb" title={clusterBlurb(c)}>
                                        {clusterBlurb(c)}
                                    </div>
                                </div>

                                <div class="cluster-metrics">
                                    <div class="metric-top">
                                        <span class="avg"><AvgPlacement value={c.avg_placement} /></span>
                                        <span class="delta" class:pos={c.delta_vs_base < 0} class:neg={c.delta_vs_base > 0}>
                                            {c.delta_vs_base > 0 ? '+' : ''}{c.delta_vs_base.toFixed(2)}
                                        </span>
                                    </div>
                                    <div class="metric-bottom">
                                        <span class="cluster-size">{c.size.toLocaleString()}</span>
                                        <span class="cluster-share">{Math.round(c.share * 100)}%</span>
                                    </div>
                                </div>
                            </div>

                            <div class="cluster-visual">
                                <div class="sig-icons" aria-label="Signature">
                                    {#each preview.tokens as t}
                                        <div class="sig-icon {tokenTypeClass(t)}" title={tokenText(t)}>
                                            {#if tokenIcon(t) && !hasIconFailed(getTokenType(t), t.slice(2))}
                                                <img
                                                    src={tokenIcon(t)}
                                                    alt=""
                                                    loading="lazy"
                                                    on:error={() => markIconFailed(getTokenType(t), t.slice(2))}
                                                />
                                            {:else}
                                                <div class="sig-fallback {tokenTypeClass(t)}"></div>
                                            {/if}
                                        </div>
                                    {/each}
                                    {#if preview.more > 0}
                                        <div class="sig-more" title={`+${preview.more} more`}>+{preview.more}</div>
                                    {/if}
                                </div>

                                <div class="hist" aria-label="Placement distribution">
                                    {#each c.placement_hist as count, idx}
                                        <div class="hist-col">
                                            <div class="bar-wrap">
                                                <div
                                                    class="bar"
                                                    style="
                                                        height: {Math.max(8, (count / maxHist(c.placement_hist)) * 100)}%;
                                                        background: {getPlacementColor(idx + 1)};
                                                    "
                                                ></div>
                                            </div>
                                            <span class="hist-label">{idx + 1}</span>
                                        </div>
                                    {/each}
                                </div>
                            </div>
                        </button>
                    {/each}
                </div>

                <div class="details">
                    {#if selectedCluster}
                        <div class="details-header">
                            <button class="back-btn" on:click={goBack} aria-label="Back to clusters">
                                ← Back
                            </button>
                            <div class="details-title">
                                Cluster #{selectedCluster.cluster_id}
                                <span class="details-sub">{selectedCluster.size.toLocaleString()} games</span>
                            </div>
                        </div>

                        <button
                            class="explore-hero"
                            on:click={exploreReplace}
                            disabled={!selectedCluster.signature_tokens?.length}
                        >
                            Explore This Cluster
                        </button>

                        <button
                            class="add-secondary"
                            on:click={exploreAdd}
                            disabled={!selectedCluster.signature_tokens?.length}
                        >
                            + Add to current filters
                        </button>

                        <div class="details-grid">
                            <div class="metric">
                                <div class="k">Avg</div>
                                <div class="v"><AvgPlacement value={selectedCluster.avg_placement} /></div>
                            </div>
                            <div class="metric">
                                <div class="k">Δ vs base</div>
                                <div class="v" class:pos={selectedCluster.delta_vs_base < 0} class:neg={selectedCluster.delta_vs_base > 0}>
                                    {selectedCluster.delta_vs_base > 0 ? '+' : ''}{selectedCluster.delta_vs_base.toFixed(2)}
                                </div>
                            </div>
                            <div class="metric">
                                <div class="k">Top4</div>
                                <div class="v">{fmtPct(selectedCluster.top4_rate)}</div>
                            </div>
                            <div class="metric">
                                <div class="k">Win</div>
                                <div class="v">{fmtPct(selectedCluster.win_rate)}</div>
                            </div>
                        </div>

                        <div class="sig comp">
                            <div class="sig-title-row">
                                <div class="sig-title">Comp</div>
                                <div class="sig-hint">
                                    {#if playbookLoading && !compView}
                                        Computing…
                                    {:else}
                                        Click to add
                                    {/if}
                                </div>
                            </div>

                            {#if traitPills.length === 0 && compUnits.length === 0 && itemTiles.length === 0}
                                <div class="comp-empty">No comp summary available for this cluster.</div>
                            {/if}

                            {#if traitPills.length > 0}
                                <div class="comp-section">
                                    <div class="comp-section-title">Traits</div>
                                    <div class="trait-pills" aria-label="Active traits">
                                        {#each visibleTraitPills as tr (tr.tok)}
                                            {@const tok = tr.tok}
                                            {@const label = tr.label ?? tokenText(tok)}
                                            {@const badge = trailingNumber(label)}
                                            {@const name = badge ? stripTrailingNumber(label) : label}
                                            <button
                                                class="trait-pill"
                                                on:click={() => addOne(tok)}
                                                title={`${label}${tr.pct ? ` • ${Math.round(tr.pct * 100)}%` : ''}`}
                                                aria-label={`Add ${label}`}
                                            >
                                                <span class="trait-icon" aria-hidden="true">
                                                    {#if tokenIcon(tok) && !hasIconFailed(getTokenType(tok), tok.slice(2))}
                                                        <img
                                                            src={tokenIcon(tok)}
                                                            alt=""
                                                            loading="lazy"
                                                            on:error={() => markIconFailed(getTokenType(tok), tok.slice(2))}
                                                        />
                                                    {:else}
                                                        <span class="trait-icon-fallback"></span>
                                                    {/if}
                                                </span>
                                                <span class="trait-name" title={name}>{name}</span>
                                                {#if badge}
                                                    <span class="trait-tier" aria-hidden="true">{badge}</span>
                                                {/if}
                                            </button>
                                        {/each}
                                        {#if traitPills.length > TRAIT_PILL_LIMIT}
                                            <button
                                                class="trait-more"
                                                on:click={() => (traitsExpanded = !traitsExpanded)}
                                                aria-label={traitsExpanded ? 'Show fewer traits' : `Show ${hiddenTraitCount} more traits`}
                                            >
                                                {#if traitsExpanded}Less{:else}+{hiddenTraitCount}{/if}
                                            </button>
                                        {/if}
                                    </div>
                                </div>
                            {/if}

                            {#if compUnits.length > 0}
                                <div class="comp-section">
                                    <div class="comp-section-title">Units</div>
                                    <div class="unit-grid" aria-label="Units">
                                        {#each compUnits as u (u.token)}
                                            {@const unitId = u.token.slice(2)}
                                            {@const label = tokenText(u.token)}
                                            {@const pct = u.pct}
                                            {@const holderCount = holderCountByUnit?.[unitId] ?? 0}
                                            {@const bestItems = bestItemsByUnit?.[unitId] ?? []}
                                            <div class="unit-tile" class:core={u.core} class:hovered={hoveredHolderUnitId === unitId}>
                                                <button
                                                    class="unit-main"
                                                    on:click={() => addOne(u.token)}
                                                    title={`${label}${pct !== null && pct !== undefined ? ` • ${fmtPct(pct)} in cluster` : ''}`}
                                                    aria-label={`Add ${label}`}
                                                >
                                                    <span class="unit-portrait" aria-hidden="true">
                                                        {#if tokenIcon(u.token) && !hasIconFailed(getTokenType(u.token), unitId)}
                                                            <img
                                                                src={tokenIcon(u.token)}
                                                                alt=""
                                                                loading="lazy"
                                                                on:error={() => markIconFailed(getTokenType(u.token), unitId)}
                                                            />
                                                        {:else}
                                                            <span class="unit-portrait-fallback"></span>
                                                        {/if}
                                                        <span class="unit-plus" aria-hidden="true">+</span>
                                                        {#if holderCount > 0}
                                                            <span class="unit-badge" aria-label={`${holderCount} key items`}>{holderCount}</span>
                                                        {/if}
                                                    </span>
                                                    <span class="unit-name" title={label}>{label}</span>
                                                    {#if pct !== null && pct !== undefined}
                                                        <span class="unit-sub">{fmtPct(pct)}</span>
                                                    {/if}
                                                </button>
                                                {#if bestItems.length > 0}
                                                    <div class="unit-items" aria-label="Equip best items">
                                                        {#each bestItems as it (it.item)}
                                                            {@const itemId = it.item}
                                                            {@const itemLabel = tokenText(`I:${itemId}`)}
                                                            <button
                                                                class="unit-item"
                                                                title={`Equip ${itemLabel} on ${label}`}
                                                                aria-label={`Equip ${itemLabel} on ${label}`}
                                                                on:click={() => equipItemOnUnit(unitId, itemId, 'cluster_comp_view')}
                                                                on:mouseenter={() => (hoveredItemId = itemId)}
                                                                on:mouseleave={() => (hoveredItemId = null)}
                                                            >
                                                                {#if getIconUrl('item', itemId) && !hasIconFailed('item', itemId)}
                                                                    <img
                                                                        src={getIconUrl('item', itemId)}
                                                                        alt=""
                                                                        loading="lazy"
                                                                        on:error={() => markIconFailed('item', itemId)}
                                                                    />
                                                                {:else}
                                                                    <span class="unit-item-fallback"></span>
                                                                {/if}
                                                            </button>
                                                        {/each}
                                                    </div>
                                                {/if}
                                            </div>
                                        {/each}
                                    </div>
                                </div>
                            {/if}

                            {#if itemTiles.length > 0}
                                <div class="comp-section">
                                    <div class="comp-section-title">Items</div>
                                    <div class="item-grid" aria-label="Items">
                                        {#each itemTiles as it (it.itemId)}
                                            {@const itemId = it.itemId}
                                            {@const label = it.label}
                                            {@const holder = it.holder}
                                            {@const holderLabel = holder ? tokenText(`U:${holder}`) : null}
                                            <div
                                                class="item-tile"
                                                role="group"
                                                aria-label={label}
                                                on:mouseenter={() => (hoveredItemId = itemId)}
                                                on:mouseleave={() => (hoveredItemId = null)}
                                            >
                                                <button
                                                    class="item-main"
                                                    on:click={() => addOne(`I:${itemId}`)}
                                                    title={`${label}${holderLabel ? ` • best on ${holderLabel}` : ''}`}
                                                    aria-label={`Add ${label}`}
                                                >
                                                    {#if getIconUrl('item', itemId) && !hasIconFailed('item', itemId)}
                                                        <img
                                                            src={getIconUrl('item', itemId)}
                                                            alt=""
                                                            loading="lazy"
                                                            on:error={() => markIconFailed('item', itemId)}
                                                        />
                                                    {:else}
                                                        <span class="item-fallback"></span>
                                                    {/if}
                                                </button>

                                                {#if holder}
                                                    <button
                                                        class="item-equip"
                                                        on:click={() => equipItemOnUnit(holder, itemId, 'cluster_comp_view')}
                                                        title={`Equip ${label} on ${holderLabel}`}
                                                        aria-label={`Equip ${label} on ${holderLabel}`}
                                                    >
                                                        +
                                                    </button>
                                                    <div class="item-holder" title={`Best on ${holderLabel}`}>
                                                        {#if getIconUrl('unit', holder) && !hasIconFailed('unit', holder)}
                                                            <img
                                                                src={getIconUrl('unit', holder)}
                                                                alt=""
                                                                loading="lazy"
                                                                on:error={() => markIconFailed('unit', holder)}
                                                            />
                                                        {:else}
                                                            <span class="item-holder-fallback"></span>
                                                        {/if}
                                                    </div>
                                                {/if}

                                                <div class="item-label" title={label}>{label}</div>
                                            </div>
                                        {/each}
                                    </div>
                                </div>
                            {/if}

                            {#if !compView && !playbookLoading}
                                <div class="comp-note">
                                    Tip: click <span class="mono">Compute</span> under Playbook to populate active trait tiers and item holders.
                                </div>
                            {/if}
                        </div>

	                        <div class="playbook">
	                            <div class="playbook-header">
	                                <div class="playbook-title">Playbook</div>
	                                <button
	                                    class="playbook-run"
	                                    disabled={playbookLoading || stale}
	                                    on:click={() => loadPlaybook(selectedCluster.cluster_id)}
	                                >
	                                    {#if playbookLoading}
	                                        Computing…
	                                    {:else if stale}
	                                        Stale
	                                    {:else if playbook}
	                                        Refresh
	                                    {:else}
	                                        Compute
	                                    {/if}
	                                </button>
	                            </div>

	                            {#if stale}
	                                <div class="callout warning">
	                                    Filters changed — run clustering again to refresh this playbook.
	                                </div>
	                            {:else if playbookError}
	                                <div class="callout error">Failed to load playbook: {playbookError}</div>
	                            {:else if playbook?.meta?.warning}
	                                <div class="callout warning">{playbook.meta.warning}</div>
	                            {:else if playbookLoading && !playbook}
	                                <div class="playbook-skeleton">
	                                    <div class="skeleton-row"></div>
	                                    <div class="skeleton-row"></div>
	                                    <div class="skeleton-row"></div>
	                                </div>
	                            {:else if playbook}
	                                <div class="playbook-note">
	                                    Δs compare games <span class="mono">within this cluster</span> (with token vs without). Baseline 8th: {fmtPct(playbook.base.eighth_rate)}.
	                                </div>

	                                <div class="pb-section">
	                                    <div class="pb-title">Biggest win drivers</div>
	                                    {#if drivers.length === 0}
	                                        <div class="pb-empty">
	                                            Not enough data to rank drivers (try broadening filters).
	                                        </div>
	                                    {:else}
	                                        <div class="pb-list">
	                                            {#each drivers as row, idx}
	                                                <div class="pb-row">
	                                                    <button class="pb-main" on:click={() => addOne(row.token)}>
	                                                        <div class="pb-rank">#{idx + 1}</div>
	                                                        <div class="pb-icon">
	                                                            {#if row.token?.startsWith('E:')}
	                                                                {@const item = row.token.slice(2).split('|')[1]}
	                                                                {#if getIconUrl('item', item) && !hasIconFailed('item', item)}
	                                                                    <img
	                                                                        src={getIconUrl('item', item)}
	                                                                        alt=""
	                                                                        loading="lazy"
	                                                                        on:error={() => markIconFailed('item', item)}
	                                                                    />
	                                                                {:else}
	                                                                    <div class="pb-fallback item"></div>
	                                                                {/if}
	                                                            {:else if tokenIcon(row.token) && !hasIconFailed(getTokenType(row.token), row.token.slice(2))}
	                                                                <img
	                                                                    src={tokenIcon(row.token)}
	                                                                    alt=""
	                                                                    loading="lazy"
	                                                                    on:error={() => markIconFailed(getTokenType(row.token), row.token.slice(2))}
	                                                                />
	                                                            {:else}
	                                                                <div class="pb-fallback {tokenTypeClass(row.token)}"></div>
	                                                            {/if}
	                                                        </div>
	                                                        <div class="pb-info">
	                                                            <div class="pb-name" title={tokenText(row.token)}>{tokenText(row.token)}</div>
	                                                            <div class="pb-sub">
	                                                                {Math.round((row.pct_in_cluster ?? 0) * 100)}% use • {row.n_with?.toLocaleString?.() ?? row.n_with} games
	                                                            </div>
	                                                        </div>
	                                                        <div class="pb-metrics">
	                                                            <div class="m {deltaClass('win', row.delta_win)}" title="Δ win rate (with token vs without)">ΔW {fmtSignedPct(row.delta_win)}</div>
	                                                            <div class="m {deltaClass('top4', row.delta_top4)}" title="Δ top4 rate (with token vs without)">ΔT4 {fmtSignedPct(row.delta_top4)}</div>
	                                                        </div>
	                                                    </button>
	                                                    <button
	                                                        class="pb-action"
	                                                        title="Exclude"
	                                                        on:click|stopPropagation={() => excludeOne(row.token)}
	                                                    >
	                                                        −
	                                                    </button>
	                                                </div>
	                                            {/each}
	                                        </div>
	                                    {/if}
	                                </div>

	                                <div class="pb-section">
	                                    <div class="pb-title">Biggest win killers</div>
	                                    {#if killers.length === 0}
	                                        <div class="pb-empty">Not enough data to rank killers.</div>
	                                    {:else}
	                                        <div class="pb-list">
	                                            {#each killers as row, idx}
	                                                <div class="pb-row">
	                                                    <button class="pb-main" on:click={() => addOne(row.token)}>
	                                                        <div class="pb-rank">#{idx + 1}</div>
	                                                        <div class="pb-icon">
	                                                            {#if row.token?.startsWith('E:')}
	                                                                {@const item = row.token.slice(2).split('|')[1]}
	                                                                {#if getIconUrl('item', item) && !hasIconFailed('item', item)}
	                                                                    <img
	                                                                        src={getIconUrl('item', item)}
	                                                                        alt=""
	                                                                        loading="lazy"
	                                                                        on:error={() => markIconFailed('item', item)}
	                                                                    />
	                                                                {:else}
	                                                                    <div class="pb-fallback item"></div>
	                                                                {/if}
	                                                            {:else if tokenIcon(row.token) && !hasIconFailed(getTokenType(row.token), row.token.slice(2))}
	                                                                <img
	                                                                    src={tokenIcon(row.token)}
	                                                                    alt=""
	                                                                    loading="lazy"
	                                                                    on:error={() => markIconFailed(getTokenType(row.token), row.token.slice(2))}
	                                                                />
	                                                            {:else}
	                                                                <div class="pb-fallback {tokenTypeClass(row.token)}"></div>
	                                                            {/if}
	                                                        </div>
	                                                        <div class="pb-info">
	                                                            <div class="pb-name" title={tokenText(row.token)}>{tokenText(row.token)}</div>
	                                                            <div class="pb-sub">
	                                                                {Math.round((row.pct_in_cluster ?? 0) * 100)}% use • {row.n_with?.toLocaleString?.() ?? row.n_with} games
	                                                            </div>
	                                                        </div>
	                                                        <div class="pb-metrics">
	                                                            <div class="m {deltaClass('win', row.delta_win)}" title="Δ win rate (with token vs without)">ΔW {fmtSignedPct(row.delta_win)}</div>
	                                                            <div class="m {deltaClass('eighth', row.delta_eighth)}" title="Δ 8th rate (with token vs without)">Δ8 {fmtSignedPct(row.delta_eighth)}</div>
	                                                        </div>
	                                                    </button>
	                                                    <button
	                                                        class="pb-action"
	                                                        title="Exclude"
	                                                        on:click|stopPropagation={() => excludeOne(row.token)}
	                                                    >
	                                                        −
	                                                    </button>
	                                                </div>
	                                            {/each}
	                                        </div>
	                                    {/if}
	                                </div>
	                            {/if}
	                        </div>

	                        {#if unifiedTokens.length > 0}
	                            <div class="unified-tokens">
	                                <div class="tokens-header">
	                                    <span class="tokens-title">Top Tokens</span>
                                    <span class="tokens-count">{unifiedTokens.length} tokens</span>
                                </div>
                                <div class="tokens-list">
                                    {#each unifiedTokens as tok}
                                        <button class="token-row {tok.type}" on:click={() => addOne(tok.token)}>
                                            {#if tokenIcon(tok.token) && !hasIconFailed(getTokenType(tok.token), tok.token.slice(2))}
                                                <img
                                                    class="token-icon"
                                                    src={tokenIcon(tok.token)}
                                                    alt=""
                                                    loading="lazy"
                                                    on:error={() => markIconFailed(getTokenType(tok.token), tok.token.slice(2))}
                                                />
                                            {:else}
                                                <div class="token-fallback {tok.type}"></div>
                                            {/if}
                                            <div class="token-info">
                                                <div class="token-name">{tokenText(tok.token)}</div>
                                                <div class="token-stats">
                                                    <span class="lift">{fmtLift(tok.lift)}</span>
                                                    <span class="sep">|</span>
                                                    <span class="pct">{fmtPct(tok.pct)}</span>
                                                    <span class="base">vs {fmtPct(tok.base_pct)} base</span>
                                                </div>
                                            </div>
                                            <div class="token-type-badge {tok.type}">
                                                {tok.type.charAt(0).toUpperCase()}
                                            </div>
                                            <div class="plus-icon">+</div>
                                        </button>
                                    {/each}
                                </div>
                            </div>
                        {/if}
                    {:else}
                        <div class="details-empty">
                            Select a cluster to inspect defining units, traits, and item prevalence.
                        </div>
                    {/if}
                </div>
            </div>
        </div>
    {/if}
</div>

<style>
    .cluster-explorer {
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
    }

    .cluster-explorer:not(.resizing) {
        transition: width 0.18s ease;
    }

    .cluster-explorer.resizing {
        user-select: none;
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

    .cluster-explorer:not(.open) .toggle {
        flex: 1;
        border-bottom: none;
        padding: 12px 0;
        writing-mode: vertical-rl;
        text-orientation: mixed;
        justify-content: center;
        align-items: center;
        gap: 10px;
    }

    .cluster-explorer:not(.open) .toggle-sub {
        display: none;
    }

    .resizer {
        position: absolute;
        left: 0;
        top: 0;
        bottom: 0;
        width: 8px;
        cursor: col-resize;
        background: transparent;
        z-index: 5;
    }

    .resizer:hover {
        background: rgba(255, 255, 255, 0.04);
    }

    .resizer::after {
        content: '';
        position: absolute;
        left: 3px;
        top: 50%;
        transform: translateY(-50%);
        width: 2px;
        height: 54px;
        border-radius: 2px;
        background: rgba(255, 255, 255, 0.10);
        opacity: 0.75;
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

    .panel-header {
        padding: 12px 14px 8px;
        border-bottom: 1px solid var(--border);
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        gap: 12px;
    }

    .panel-title {
        font-size: 14px;
        font-weight: 800;
        letter-spacing: -0.01em;
    }

    .panel-meta {
        font-size: 11px;
        color: var(--text-tertiary);
        display: flex;
        align-items: center;
        gap: 6px;
        white-space: nowrap;
    }

    .dot {
        opacity: 0.6;
    }

    .controls {
        display: flex;
        gap: 10px;
        padding: 10px 14px;
        border-bottom: 1px solid var(--border);
        align-items: center;
        flex-wrap: wrap;
    }

    .control {
        display: flex;
        align-items: center;
        gap: 6px;
        padding: 6px 8px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        border-radius: 8px;
        color: var(--text-secondary);
        font-size: 11px;
    }

    .control span {
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-weight: 700;
        color: var(--text-tertiary);
    }

    .control input {
        width: 54px;
        background: transparent;
        border: none;
        outline: none;
        color: var(--text-primary);
        font-weight: 700;
        font-size: 12px;
        font-family: inherit;
    }

    .controls.compact {
        display: flex;
        justify-content: space-between;
        align-items: center;
        gap: 8px;
    }

    .controls-left {
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
        flex: 1;
        min-width: 0;
    }

    .run {
        background: var(--accent);
        color: #000;
        border: none;
        border-radius: 8px;
        padding: 8px 12px;
        font-size: 11px;
        font-weight: 900;
        cursor: pointer;
        transition: background 0.2s ease;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-family: inherit;
        flex-shrink: 0;
    }

    .run:disabled {
        opacity: 0.6;
        cursor: default;
    }

    .run:hover:not(:disabled) {
        background: var(--accent-hover);
    }

    .select {
        display: flex;
        align-items: center;
        gap: 6px;
        color: var(--text-tertiary);
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }

    .select.inline span {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-weight: 700;
        color: var(--text-tertiary);
    }

    select {
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        color: var(--text-primary);
        border-radius: 8px;
        padding: 5px 8px;
        font-size: 11px;
        font-weight: 700;
        font-family: inherit;
    }

    .tiny {
        font-size: 10px;
        color: var(--text-tertiary);
        font-weight: 600;
        white-space: nowrap;
    }

    .callout {
        padding: 10px 14px;
        font-size: 12px;
        color: var(--text-secondary);
        border-bottom: 1px solid var(--border);
    }

    .callout-actions {
        margin-top: 8px;
        display: flex;
        gap: 8px;
    }

    .callout-btn {
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        color: var(--text-primary);
        border-radius: 8px;
        padding: 6px 8px;
        font-size: 11px;
        font-weight: 700;
        cursor: pointer;
        font-family: inherit;
        transition: background 0.15s ease, border-color 0.15s ease;
    }

    .callout-btn:hover {
        border-color: var(--border-hover);
        background: rgba(255, 255, 255, 0.03);
    }

    .callout.warning {
        color: rgba(245, 166, 35, 0.95);
        background: rgba(245, 166, 35, 0.08);
    }

    .callout.error {
        color: rgba(255, 68, 68, 0.95);
        background: rgba(255, 68, 68, 0.08);
    }

    .content {
        display: grid;
        grid-template-columns: 280px 1fr;
        flex: 1;
        min-height: 0;
    }

    .cluster-list {
        border-right: 1px solid var(--border);
        overflow: auto;
        min-height: 0;
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .cluster {
        width: 100%;
        text-align: left;
        border: 1px solid var(--border);
        background: rgba(17, 17, 17, 0.5);
        border-radius: 10px;
        padding: 10px 10px 12px;
        cursor: pointer;
        transition: border-color 0.2s ease, background 0.2s ease;
        color: var(--text-primary);
        font-family: inherit;
    }

    .cluster:hover {
        border-color: var(--border-hover);
        background: rgba(17, 17, 17, 0.8);
    }

    .cluster.selected {
        border-color: rgba(0, 112, 243, 0.65);
        box-shadow: 0 0 0 1px rgba(0, 112, 243, 0.25);
    }

    .cluster-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 10px;
    }

    .cluster-left {
        flex: 1;
        min-width: 0;
    }

    .cluster-name-row {
        display: flex;
        align-items: baseline;
        gap: 8px;
        margin-bottom: 3px;
    }

    .cluster-tag {
        font-weight: 900;
        letter-spacing: 0.08em;
        font-size: 10px;
        color: var(--text-tertiary);
        text-transform: uppercase;
    }

    .cluster-name {
        font-size: 13px;
        font-weight: 900;
        letter-spacing: -0.01em;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .cluster-blurb {
        font-size: 11px;
        color: var(--text-tertiary);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .cluster-metrics {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 4px;
        flex-shrink: 0;
    }

    .metric-top {
        display: flex;
        align-items: baseline;
        gap: 8px;
    }

    .avg {
        font-size: 16px;
        font-weight: 900;
        letter-spacing: -0.01em;
    }

    .delta {
        font-size: 12px;
        font-weight: 900;
        color: var(--text-tertiary);
    }

    .delta.pos { color: var(--success); }
    .delta.neg { color: var(--error); }

    .metric-bottom {
        display: flex;
        align-items: baseline;
        gap: 8px;
    }

    .cluster-size {
        font-weight: 800;
        font-size: 11px;
        color: var(--text-tertiary);
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .cluster-share {
        font-size: 10px;
        font-weight: 800;
        color: var(--text-tertiary);
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }

    .cluster-visual {
        margin-top: 10px;
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .sig-icons {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
        align-items: center;
    }

    .sig-icon {
        width: 22px;
        height: 22px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.02);
        overflow: hidden;
        flex-shrink: 0;
        position: relative;
    }

    .sig-icon.unit { box-shadow: inset 0 0 0 1px rgba(255, 107, 157, 0.18); }
    .sig-icon.item { box-shadow: inset 0 0 0 1px rgba(0, 217, 255, 0.18); }
    .sig-icon.trait { box-shadow: inset 0 0 0 1px rgba(168, 85, 247, 0.18); }

    .sig-icon img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
    }

    .sig-fallback {
        width: 100%;
        height: 100%;
        position: relative;
        background: rgba(255, 255, 255, 0.04);
    }

    .sig-fallback.unit::after,
    .sig-fallback.item::after,
    .sig-fallback.trait::after {
        content: '';
        position: absolute;
        inset: 5px;
        border-radius: 6px;
        opacity: 0.9;
    }

    .sig-fallback.unit::after { background: rgba(255, 107, 157, 0.45); }
    .sig-fallback.item::after { background: rgba(0, 217, 255, 0.45); }
    .sig-fallback.trait::after { background: rgba(168, 85, 247, 0.45); }

    .sig-more {
        height: 22px;
        padding: 0 8px;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.03);
        color: var(--text-tertiary);
        font-size: 10px;
        font-weight: 900;
        display: inline-flex;
        align-items: center;
        letter-spacing: 0.06em;
        text-transform: uppercase;
    }

    .hist {
        display: grid;
        grid-template-columns: repeat(8, 1fr);
        gap: 2px;
    }

    .hist-col {
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 2px;
    }

    .bar-wrap {
        width: 100%;
        height: 18px;
        display: flex;
        align-items: flex-end;
    }

    .bar {
        width: 100%;
        border-radius: 2px;
        opacity: 0.85;
    }

    .hist-label {
        font-size: 8px;
        color: var(--text-tertiary);
        font-weight: 600;
        line-height: 1;
    }

    .details {
        overflow: auto;
        min-height: 0;
        padding: 12px 14px 16px;
    }

    .content.narrow {
        grid-template-columns: 1fr;
    }

    .content.narrow .cluster-list {
        border-right: none;
    }

    .content.narrow.showDetails .cluster-list {
        display: none;
    }

    .content.narrow:not(.showDetails) .details {
        display: none;
    }

    .details-empty {
        color: var(--text-tertiary);
        font-size: 12px;
        padding: 16px 6px;
        line-height: 1.6;
    }

    .details-header {
        display: flex;
        align-items: center;
        gap: 12px;
        margin-bottom: 14px;
    }

    .back-btn {
        display: flex;
        align-items: center;
        gap: 4px;
        padding: 8px 12px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        border-radius: 8px;
        color: var(--text-secondary);
        font-size: 11px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.04em;
        cursor: pointer;
        transition: all 0.2s ease;
        font-family: inherit;
        flex-shrink: 0;
    }

    .back-btn:hover {
        border-color: var(--border-hover);
        color: var(--text-primary);
    }

    .details-title {
        font-size: 16px;
        font-weight: 900;
        letter-spacing: -0.02em;
        flex: 1;
        min-width: 0;
    }

    .details-sub {
        font-size: 12px;
        color: var(--text-tertiary);
        font-weight: 600;
        margin-left: 10px;
    }

    .explore-hero {
        width: 100%;
        padding: 14px 20px;
        margin-bottom: 10px;
        background: var(--accent);
        color: #000;
        border: none;
        border-radius: 12px;
        font-size: 14px;
        font-weight: 900;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        cursor: pointer;
        transition: background 0.2s ease, transform 0.1s ease;
        font-family: inherit;
    }

    .explore-hero:hover:not(:disabled) {
        background: var(--accent-hover);
        transform: translateY(-1px);
    }

    .explore-hero:disabled {
        opacity: 0.5;
        cursor: default;
    }

    .add-secondary {
        width: 100%;
        padding: 10px 16px;
        margin-bottom: 14px;
        background: transparent;
        color: var(--text-secondary);
        border: 1px solid var(--border);
        border-radius: 10px;
        font-size: 12px;
        font-weight: 700;
        cursor: pointer;
        transition: border-color 0.2s ease, color 0.2s ease;
        font-family: inherit;
    }

    .add-secondary:hover:not(:disabled) {
        border-color: var(--border-hover);
        color: var(--text-primary);
    }

    .add-secondary:disabled {
        opacity: 0.5;
        cursor: default;
    }

    .details-grid {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 10px;
        margin-bottom: 14px;
    }

    .metric {
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 10px;
        background: rgba(17, 17, 17, 0.5);
    }

    .metric .k {
        font-size: 9px;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        font-weight: 800;
        margin-bottom: 6px;
    }

    .metric .v {
        font-weight: 900;
        font-size: 14px;
        color: var(--text-primary);
    }

    .metric .v.pos {
        color: var(--success);
    }

    .metric .v.neg {
        color: var(--error);
    }

    .sig {
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 10px;
        margin-bottom: 14px;
        background: rgba(17, 17, 17, 0.35);
    }

    .sig-title {
        font-size: 9px;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        font-weight: 900;
        margin-bottom: 0;
    }

    .sig-title-row {
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 10px;
        margin-bottom: 10px;
    }

    .sig-hint {
        font-size: 10px;
        color: var(--text-tertiary);
        font-weight: 700;
        opacity: 0.85;
        white-space: nowrap;
    }

    .comp-section {
        margin-bottom: 12px;
    }

    .comp-section:last-child {
        margin-bottom: 0;
    }

    .comp-section-title {
        font-size: 9px;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        font-weight: 900;
        margin-bottom: 6px;
    }

    .trait-pills {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
    }

    .trait-pill {
        display: inline-flex;
        align-items: center;
        gap: 6px;
        padding: 6px 8px;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: rgba(17, 17, 17, 0.22);
        cursor: pointer;
        font-family: inherit;
        color: var(--text-secondary);
        font-size: 11px;
        font-weight: 900;
        transition: border-color 0.15s ease, background 0.15s ease, color 0.15s ease;
    }

    .trait-pill:hover {
        border-color: var(--border-hover);
        background: rgba(17, 17, 17, 0.42);
        color: var(--text-primary);
    }

    .trait-pill:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
    }

    .trait-icon {
        width: 16px;
        height: 16px;
        border-radius: 6px;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.02);
        overflow: hidden;
        flex-shrink: 0;
        display: grid;
        place-items: center;
    }

    .trait-icon img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
    }

    .trait-icon-fallback {
        width: 10px;
        height: 10px;
        border-radius: 4px;
        background: rgba(168, 85, 247, 0.35);
    }

    .trait-name {
        max-width: 130px;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .trait-tier {
        height: 18px;
        padding: 0 6px;
        border-radius: 999px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        background: rgba(0, 0, 0, 0.32);
        color: rgba(255, 255, 255, 0.84);
        font-size: 10px;
        font-weight: 900;
        display: grid;
        place-items: center;
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .trait-more {
        padding: 6px 10px;
        border-radius: 999px;
        border: 1px dashed var(--border);
        background: transparent;
        color: var(--text-tertiary);
        cursor: pointer;
        font-family: inherit;
        font-size: 11px;
        font-weight: 900;
        transition: border-color 0.15s ease, color 0.15s ease, background 0.15s ease;
    }

    .trait-more:hover {
        border-color: var(--border-hover);
        color: var(--text-primary);
        background: rgba(17, 17, 17, 0.22);
    }

    .trait-more:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
    }

    .unit-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(96px, 1fr));
        gap: 8px;
    }

    .unit-tile {
        position: relative;
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 8px 6px;
        background: rgba(17, 17, 17, 0.22);
        transition: border-color 0.15s ease, background 0.15s ease;
    }

    .unit-tile.core {
        box-shadow: inset 0 0 0 1px rgba(255, 107, 157, 0.18);
    }

    .unit-tile.hovered {
        border-color: rgba(0, 217, 255, 0.28);
        box-shadow: inset 0 0 0 1px rgba(0, 217, 255, 0.1);
    }

    .unit-main {
        width: 100%;
        border: none;
        background: transparent;
        padding: 0;
        cursor: pointer;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 6px;
        font-family: inherit;
        color: var(--text-primary);
        text-align: center;
    }

    .unit-main:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
        border-radius: 10px;
    }

    .unit-portrait {
        width: 52px;
        height: 52px;
        border-radius: 14px;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.02);
        overflow: hidden;
        position: relative;
        flex-shrink: 0;
    }

    .unit-portrait img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
    }

    .unit-portrait-fallback {
        width: 100%;
        height: 100%;
        background: rgba(255, 107, 157, 0.25);
        display: block;
    }

    .unit-plus {
        position: absolute;
        top: 4px;
        right: 4px;
        width: 16px;
        height: 16px;
        border-radius: 999px;
        background: var(--accent);
        color: #000;
        font-size: 11px;
        font-weight: 900;
        display: grid;
        place-items: center;
        opacity: 0;
        transform: scale(0.92);
        transition: opacity 0.15s ease, transform 0.15s ease;
        pointer-events: none;
    }

    .unit-main:hover .unit-plus {
        opacity: 1;
        transform: scale(1);
    }

    .unit-badge {
        position: absolute;
        bottom: 4px;
        right: 4px;
        min-width: 18px;
        height: 18px;
        padding: 0 6px;
        border-radius: 999px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        background: rgba(0, 0, 0, 0.32);
        color: rgba(255, 255, 255, 0.84);
        font-size: 10px;
        font-weight: 900;
        display: grid;
        place-items: center;
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .unit-name {
        width: 100%;
        font-size: 11px;
        font-weight: 900;
        color: var(--text-primary);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .unit-sub {
        font-size: 10px;
        font-weight: 800;
        color: var(--text-tertiary);
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .unit-items {
        margin-top: 8px;
        display: flex;
        justify-content: center;
        gap: 4px;
        opacity: 0;
        transform: translateY(2px);
        pointer-events: none;
        transition: opacity 0.15s ease, transform 0.15s ease;
    }

    .unit-tile:hover .unit-items,
    .unit-tile:focus-within .unit-items {
        opacity: 1;
        transform: translateY(0);
        pointer-events: auto;
    }

    .unit-item {
        width: 20px;
        height: 20px;
        padding: 0;
        border-radius: 6px;
        border: 1px solid var(--border);
        background: rgba(0, 0, 0, 0.28);
        cursor: pointer;
        overflow: hidden;
        transition: border-color 0.15s ease, background 0.15s ease;
    }

    .unit-item:hover {
        border-color: var(--border-hover);
        background: rgba(0, 0, 0, 0.4);
    }

    .unit-item img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
    }

    .unit-item-fallback {
        width: 100%;
        height: 100%;
        background: rgba(0, 217, 255, 0.25);
        display: block;
    }

    .item-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(84px, 1fr));
        gap: 8px;
    }

    .item-tile {
        position: relative;
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 8px 6px;
        background: rgba(17, 17, 17, 0.22);
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 6px;
        transition: border-color 0.15s ease, background 0.15s ease;
    }

    .item-main {
        width: 44px;
        height: 44px;
        padding: 0;
        border-radius: 12px;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.02);
        overflow: hidden;
        cursor: pointer;
        transition: border-color 0.15s ease, background 0.15s ease;
    }

    .item-main:hover {
        border-color: var(--border-hover);
    }

    .item-main:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
    }

    .item-main img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
    }

    .item-fallback {
        width: 100%;
        height: 100%;
        background: rgba(0, 217, 255, 0.2);
        display: block;
    }

    .item-equip {
        position: absolute;
        top: 6px;
        right: 6px;
        width: 18px;
        height: 18px;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: rgba(0, 0, 0, 0.3);
        color: var(--text-primary);
        cursor: pointer;
        display: grid;
        place-items: center;
        font-size: 12px;
        font-weight: 900;
        opacity: 0;
        transform: scale(0.92);
        transition: opacity 0.15s ease, transform 0.15s ease, border-color 0.15s ease, background 0.15s ease;
    }

    .item-tile:hover .item-equip,
    .item-tile:focus-within .item-equip {
        opacity: 1;
        transform: scale(1);
    }

    .item-equip:hover {
        border-color: var(--border-hover);
        background: rgba(0, 0, 0, 0.42);
    }

    .item-equip:focus-visible {
        outline: 2px solid var(--accent);
        outline-offset: 2px;
    }

    .item-holder {
        position: absolute;
        bottom: 6px;
        right: 6px;
        width: 18px;
        height: 18px;
        border-radius: 6px;
        border: 1px solid rgba(255, 255, 255, 0.12);
        background: rgba(0, 0, 0, 0.32);
        overflow: hidden;
        display: grid;
        place-items: center;
    }

    .item-holder img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
    }

    .item-holder-fallback {
        width: 12px;
        height: 12px;
        border-radius: 4px;
        background: rgba(255, 107, 157, 0.25);
    }

    .item-label {
        width: 100%;
        font-size: 10px;
        font-weight: 900;
        color: var(--text-secondary);
        text-align: center;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .comp-empty,
    .comp-note {
        padding: 10px 12px;
        border: 1px dashed var(--border);
        border-radius: 12px;
        background: rgba(17, 17, 17, 0.2);
        color: var(--text-tertiary);
        font-size: 12px;
        text-align: center;
    }

    .comp-note {
        border-style: solid;
        opacity: 0.9;
    }

    .unified-tokens {
        border: 1px solid var(--border);
        border-radius: 12px;
        background: rgba(17, 17, 17, 0.4);
        overflow: hidden;
    }

    .tokens-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 10px 12px;
        border-bottom: 1px solid var(--border);
    }

    .tokens-title {
        font-size: 10px;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        font-weight: 900;
    }

    .tokens-count {
        font-size: 10px;
        color: var(--text-tertiary);
        font-weight: 600;
    }

    .tokens-list {
        max-height: 400px;
        overflow-y: auto;
    }

    .token-row {
        display: flex;
        align-items: center;
        gap: 10px;
        width: 100%;
        padding: 10px 12px;
        background: transparent;
        border: none;
        border-bottom: 1px solid var(--border);
        cursor: pointer;
        transition: background 0.15s ease;
        color: var(--text-primary);
        text-align: left;
        font-family: inherit;
    }

    .token-row:last-child {
        border-bottom: none;
    }

    .token-row:hover {
        background: rgba(255, 255, 255, 0.03);
    }

    .token-icon {
        width: 28px;
        height: 28px;
        border-radius: 8px;
        border: 1px solid var(--border);
        object-fit: cover;
        flex-shrink: 0;
    }

    .token-fallback {
        width: 28px;
        height: 28px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        flex-shrink: 0;
        position: relative;
    }

    .token-fallback::after {
        content: '';
        position: absolute;
        inset: 5px;
        border-radius: 6px;
        opacity: 0.8;
    }

    .token-fallback.unit::after { background: rgba(255, 107, 157, 0.5); }
    .token-fallback.trait::after { background: rgba(168, 85, 247, 0.5); }
    .token-fallback.item::after { background: rgba(0, 217, 255, 0.5); }

    .token-info {
        flex: 1;
        min-width: 0;
    }

    .token-name {
        font-size: 13px;
        font-weight: 700;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .token-stats {
        font-size: 11px;
        color: var(--text-tertiary);
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 2px;
    }

    .token-stats .lift {
        font-weight: 700;
        color: var(--text-secondary);
    }

    .token-stats .sep {
        opacity: 0.4;
    }

    .token-stats .base {
        opacity: 0.6;
    }

    .token-type-badge {
        width: 20px;
        height: 20px;
        border-radius: 6px;
        display: grid;
        place-items: center;
        font-size: 10px;
        font-weight: 900;
        flex-shrink: 0;
    }

    .token-type-badge.unit {
        background: rgba(255, 107, 157, 0.2);
        color: #ff6b9d;
    }

    .token-type-badge.trait {
        background: rgba(168, 85, 247, 0.2);
        color: #a855f7;
    }

    .token-type-badge.item {
        background: rgba(0, 217, 255, 0.2);
        color: #00d9ff;
    }

    .plus-icon {
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

    .token-row:hover .plus-icon {
        opacity: 1;
    }

    .playbook {
        margin-top: 14px;
        border-top: 1px solid var(--border);
        padding-top: 14px;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .playbook-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
    }

    .playbook-title {
        font-size: 12px;
        font-weight: 900;
        letter-spacing: 0.02em;
        text-transform: uppercase;
        color: var(--text-primary);
    }

    .playbook-run {
        background: var(--bg-tertiary);
        color: var(--text-primary);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 8px 10px;
        font-size: 11px;
        font-weight: 900;
        cursor: pointer;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-family: inherit;
    }

    .playbook-run:disabled {
        opacity: 0.55;
        cursor: not-allowed;
    }

    .playbook-note {
        font-size: 11px;
        color: var(--text-tertiary);
        line-height: 1.35;
    }

    .mono {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace;
    }

    .pb-section {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .pb-title {
        font-size: 12px;
        font-weight: 800;
        color: var(--text-secondary);
    }

    .pb-empty {
        font-size: 12px;
        color: var(--text-tertiary);
        padding: 4px 0;
    }

    .pb-list {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .pb-row {
        display: flex;
        gap: 8px;
        align-items: stretch;
    }

    .pb-main {
        flex: 1;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.02);
        border-radius: 12px;
        padding: 10px 12px;
        display: grid;
        grid-template-columns: 28px 26px minmax(0, 1fr);
        grid-template-rows: auto auto;
        column-gap: 10px;
        row-gap: 8px;
        align-items: center;
        cursor: pointer;
        text-align: left;
        color: var(--text-primary);
        font-family: inherit;
        min-width: 0;
    }

    .pb-main:hover {
        background: rgba(255, 255, 255, 0.04);
        border-color: rgba(45, 212, 191, 0.35);
    }

    .pb-rank {
        font-size: 10px;
        font-weight: 900;
        color: var(--text-tertiary);
        text-align: right;
        flex: 0 0 auto;
    }

    .pb-icon {
        width: 26px;
        height: 26px;
        border-radius: 8px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        display: flex;
        align-items: center;
        justify-content: center;
        flex: 0 0 auto;
        overflow: hidden;
    }

    .pb-icon img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }

    .pb-fallback {
        width: 100%;
        height: 100%;
        border-radius: 7px;
        background: rgba(255, 255, 255, 0.06);
    }

    .pb-fallback.unit {
        background: rgba(45, 212, 191, 0.25);
    }
    .pb-fallback.trait {
        background: rgba(251, 191, 36, 0.22);
    }
    .pb-fallback.item {
        background: rgba(96, 165, 250, 0.22);
    }

    .pb-info {
        grid-column: 3;
        grid-row: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 2px;
    }

    .pb-name {
        font-size: 12px;
        font-weight: 900;
        overflow: hidden;
        display: -webkit-box;
        -webkit-box-orient: vertical;
        -webkit-line-clamp: 2;
    }

    .pb-sub {
        font-size: 11px;
        color: var(--text-tertiary);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .pb-metrics {
        grid-column: 2 / 4;
        grid-row: 2;
        display: flex;
        flex-wrap: wrap;
        gap: 8px;
        align-items: center;
        justify-content: flex-start;
        text-align: left;
    }

    .pb-metrics .m {
        font-size: 11px;
        font-weight: 900;
        white-space: nowrap;
        padding: 4px 8px;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.03);
        color: var(--text-secondary);
    }

    .pb-metrics .m.pos {
        color: rgba(45, 212, 191, 0.95);
        border-color: rgba(45, 212, 191, 0.35);
        background: rgba(45, 212, 191, 0.08);
    }
    .pb-metrics .m.neg {
        color: rgba(248, 113, 113, 0.95);
        border-color: rgba(248, 113, 113, 0.35);
        background: rgba(248, 113, 113, 0.08);
    }
    .pb-metrics .m.neutral {
        color: var(--text-secondary);
    }

    .pb-action {
        width: 38px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        border-radius: 12px;
        color: var(--text-tertiary);
        font-weight: 900;
        font-size: 18px;
        cursor: pointer;
        flex: 0 0 auto;
        display: grid;
        place-items: center;
        padding: 0;
    }

    .pb-action:hover {
        border-color: rgba(248, 113, 113, 0.35);
        color: rgba(248, 113, 113, 0.95);
        background: rgba(248, 113, 113, 0.06);
    }

    .playbook-skeleton {
        display: flex;
        flex-direction: column;
        gap: 10px;
        padding: 6px 0;
    }

    .playbook-skeleton .skeleton-row {
        height: 44px;
        border-radius: 12px;
        background: rgba(255, 255, 255, 0.06);
        border: 1px solid rgba(255, 255, 255, 0.06);
        animation: pulse 1.4s ease-in-out infinite;
    }

    @keyframes pulse {
        0%, 100% { opacity: 0.65; }
        50% { opacity: 1; }
    }

    @media (max-width: 768px) {
        .cluster-explorer {
            width: 100% !important;
            height: auto;
        }

        .cluster-explorer:not(.open) .toggle {
            writing-mode: horizontal-tb;
            text-orientation: unset;
            justify-content: space-between;
            align-items: center;
            padding: 10px 12px;
            flex: 0 0 auto;
            border-bottom: none;
        }

        .cluster-explorer:not(.open) .toggle-sub {
            display: inline;
        }

        .resizer {
            display: none;
        }

        .panel {
            height: min(720px, 70vh);
        }

        .controls.compact {
            flex-direction: column;
            gap: 10px;
        }

        .controls-left {
            width: 100%;
            justify-content: flex-start;
        }

        .run {
            width: 100%;
        }

        .explore-hero {
            padding: 16px 20px;
            font-size: 15px;
        }

        .details-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }

        .tokens-list {
            max-height: 50vh;
        }
    }
</style>
