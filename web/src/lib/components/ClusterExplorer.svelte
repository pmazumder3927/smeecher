<script>
    import { onMount } from 'svelte';
    import { fetchClusters, fetchClusterPlaybook, fetchTokenPlaybook } from '../api.js';
    import {
        selectedTokens,
        activeTypes,
        clearTokens,
        recordUiAction,
        setHighlightedTokens,
        clearHighlightedTokens,
        setHoveredTokens,
        clearHoveredTokens,
        clusterExplorerOpen,
        clusterExplorerRunRequest
    } from '../stores/state.js';
    import { getTokenType, getTokenLabel, parseToken } from '../utils/tokens.js';
    import { getSearchIndex } from '../utils/searchIndexCache.js';
    import { getDisplayName, getIconUrl } from '../stores/assets.js';
    import AvgPlacement from './AvgPlacement.svelte';
    import ClusterList from './clusterExplorer/ClusterList.svelte';
    import ClusterDetails from './clusterExplorer/ClusterDetails.svelte';
    import MetaCompsTab from './clusterExplorer/MetaCompsTab.svelte';
    import MetaCompDetails from './clusterExplorer/MetaCompDetails.svelte';
    import posthog from '../client/posthog';

    const COLLAPSED_WIDTH_PX = 46;
    const MIN_WIDTH_PX = 340;
    const MAX_WIDTH_PX = 720;
    const SPLIT_THRESHOLD_PX = 640;

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

    let selectedMetaComp = null;
    let metaTokens = [];
    let metaTokenMeta = null;
    let metaClusterSummary = null;
    let metaPlaybookLoading = false;
    let metaPlaybookError = null;
    let metaPlaybook = null;
    let metaPlaybookFetchVersion = 0;

    let rootEl;
    let measuredWidth = 0;
    let widthPx = 440;
    let view = 'list'; // list | details (only used in narrow mode)
    let leftTab = 'clusters'; // clusters | meta
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

    let lastActiveTypesKey = '';
    $: activeTypesKey = [...$activeTypes].sort().join(',');
    $: if ($clusterExplorerOpen && data && lastActiveTypesKey && activeTypesKey !== lastActiveTypesKey) stale = true;

    $: apiParams = {
        ...params,
        use_units: $activeTypes.has('unit'),
        use_traits: $activeTypes.has('trait'),
        use_items: $activeTypes.has('item')
    };

    $: clusters = data?.clusters ?? [];
    $: warning = data?.meta?.warning ?? null;

    $: sortedClusters = (() => {
        const list = [...clusters];
        if (sortBy === 'size') list.sort((a, b) => b.size - a.size);
        else if (sortBy === 'top4') list.sort((a, b) => b.top4_rate - a.top4_rate);
        else list.sort((a, b) => a.avg_placement - b.avg_placement);
        return list;
    })();

    $: selectedCluster = sortedClusters.find((c) => c.cluster_id === selectedClusterId) ?? null;
    $: if (view === 'details' && !selectedCluster) view = 'list';

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

        const ro = new ResizeObserver((entries) => {
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
            selectedMetaComp = null;
            metaTokens = [];
            metaTokenMeta = null;
            metaClusterSummary = null;
            metaPlaybook = null;
            metaPlaybookError = null;
            metaPlaybookLoading = false;
            view = 'list';
            stale = false;
            clearHighlightedTokens();
            clearHoveredTokens();
            return;
        }
        posthog.capture('explorer_opened');
        view = 'list';
        if (!data) run();
    }

    $: if ($clusterExplorerOpen && $clusterExplorerRunRequest !== lastRunRequest && !loading) {
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
        clearHoveredTokens();
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
                candidates_scored: res?.meta?.candidates_scored ?? 0
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

    function goBack() {
        selectedClusterId = null;
        leftTab = 'clusters';
        clearHighlightedTokens();
        clearHoveredTokens();
        if (isNarrow) view = 'list';
    }

    function goBackMeta() {
        selectedMetaComp = null;
        metaTokens = [];
        metaTokenMeta = null;
        metaClusterSummary = null;
        metaPlaybook = null;
        metaPlaybookError = null;
        metaPlaybookLoading = false;
        clearHighlightedTokens();
        clearHoveredTokens();
        if (isNarrow) view = 'list';
    }

    function showClusters() {
        leftTab = 'clusters';
        if (isNarrow) view = 'list';
    }

    function showMetaComps() {
        leftTab = 'meta';
        if (isNarrow) view = 'list';
    }

    function showDetails() {
        if (leftTab === 'clusters') {
            if (selectedCluster) view = 'details';
            return;
        }
        if (selectedMetaComp) view = 'details';
    }

    function selectMetaComp(comp) {
        selectedMetaComp = comp;
        metaTokens = Array.isArray(comp?.unitTokens) ? comp.unitTokens : [];
        metaTokenMeta = null;
        metaClusterSummary = null;
        metaPlaybook = null;
        metaPlaybookError = null;

        const tokens = comp?.unitTokens ?? [];
        setHighlightedTokens(tokens);
        clearHoveredTokens();

        posthog.capture('meta_comp_selected', {
            slug: comp?.slug ?? null,
            tier: comp?.tier ?? null,
            title: comp?.metaTitle ?? comp?.title ?? comp?.slug ?? null,
        });

        if (isNarrow) view = 'details';
        loadMetaPlaybook(comp);
    }

    async function loadMetaPlaybook(comp = selectedMetaComp) {
        if (!comp) return;

        const version = ++metaPlaybookFetchVersion;
        metaPlaybookLoading = true;
        metaPlaybookError = null;

        try {
            const res = await fetchTokenPlaybook(comp?.unitTokens ?? [], {
                use_units: true,
                use_traits: true,
                use_items: true,
                min_token_freq: params.min_token_freq,
                top_k_tokens: params.top_k_tokens,
            });
            if (version !== metaPlaybookFetchVersion) return;
            metaClusterSummary = res?.cluster ?? null;
            metaPlaybook = res?.playbook ?? null;
            metaTokenMeta = res?.meta ?? null;

            const effectiveTokens = (res?.tokens ?? []).filter(
                (t) => typeof t === 'string' && t && !t.startsWith('-') && !t.startsWith('!')
            );
            const fallbackTokens = effectiveTokens.length ? effectiveTokens : (comp?.unitTokens ?? []);
            metaTokens = fallbackTokens;

            const highlight = res?.cluster?.signature_tokens?.length ? res.cluster.signature_tokens : fallbackTokens;
            setHighlightedTokens(highlight);

            posthog.capture('meta_comp_playbook_loaded', {
                slug: comp?.slug ?? null,
                candidates_scored: res?.playbook?.meta?.candidates_scored ?? 0,
            });
        } catch (e) {
            if (version !== metaPlaybookFetchVersion) return;
            metaPlaybookError = e?.message ?? String(e);
        } finally {
            if (version === metaPlaybookFetchVersion) metaPlaybookLoading = false;
        }
    }

    function nodeIdsForToken(token) {
        if (typeof token !== 'string') return [];
        const parsed = parseToken(token);
        if (parsed.type === 'equipped') {
            const ids = [];
            if (parsed.unit) ids.push(`U:${parsed.unit}`);
            if (parsed.item) ids.push(`I:${parsed.item}`);
            return ids;
        }
        if (parsed.type === 'unit') return parsed.unit ? [`U:${parsed.unit}`] : [];
        if (parsed.type === 'item') return parsed.item ? [`I:${parsed.item}`] : [];
        if (parsed.type === 'trait') {
            if (!parsed.trait) return [];
            if (Number.isFinite(parsed.tier) && parsed.tier !== null) return [`T:${parsed.trait}:${parsed.tier}`];
            return [`T:${parsed.trait}`];
        }
        // Fallback: if it's already a graph node id, pass it through.
        if (token.startsWith('U:') || token.startsWith('I:') || token.startsWith('T:')) return [token];
        return [];
    }

    function setHoverForTokens(tokens) {
        const ids = new Set();
        for (const t of tokens ?? []) {
            for (const id of nodeIdsForToken(t)) ids.add(id);
        }
        setHoveredTokens(ids);
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

            <div class="tabs" role="tablist" aria-label="Explorer tabs">
                <button
                    type="button"
                    class="tab"
                    role="tab"
                    class:active={leftTab === 'clusters' && (!isNarrow || view === 'list')}
                    aria-selected={leftTab === 'clusters' && (!isNarrow || view === 'list')}
                    tabindex={leftTab === 'clusters' && (!isNarrow || view === 'list') ? 0 : -1}
                    on:click={showClusters}
                >
                    Clusters
                </button>
                <button
                    type="button"
                    class="tab"
                    role="tab"
                    class:active={leftTab === 'meta' && (!isNarrow || view === 'list')}
                    aria-selected={leftTab === 'meta' && (!isNarrow || view === 'list')}
                    tabindex={leftTab === 'meta' && (!isNarrow || view === 'list') ? 0 : -1}
                    on:click={showMetaComps}
                >
                    Meta comps
                </button>
                {#if isNarrow && leftTab === 'clusters' && selectedCluster}
                    <button
                        type="button"
                        class="tab"
                        role="tab"
                        class:active={view === 'details'}
                        aria-selected={view === 'details'}
                        tabindex={view === 'details' ? 0 : -1}
                        on:click={showDetails}
                    >
                        Details
                    </button>
                {/if}
                {#if isNarrow && leftTab === 'meta' && selectedMetaComp}
                    <button
                        type="button"
                        class="tab"
                        role="tab"
                        class:active={view === 'details'}
                        aria-selected={view === 'details'}
                        tabindex={view === 'details' ? 0 : -1}
                        on:click={showDetails}
                    >
                        Details
                    </button>
                {/if}
            </div>

            <div class="content" class:narrow={isNarrow} class:showDetails={isNarrow && view === 'details'}>
                {#if leftTab === 'meta'}
                    <div class="meta-pane">
                        <MetaCompsTab
                            {tokenText}
                            {tokenIcon}
                            {tokenTypeClass}
                            selectedSlug={selectedMetaComp?.slug ?? null}
                            onSelectComp={selectMetaComp}
                            onHoverTokens={setHoverForTokens}
                            onClearHover={clearHoveredTokens}
                        />
                    </div>

                    <MetaCompDetails
                        metaComp={selectedMetaComp}
                        effectiveTokens={metaTokens}
                        tokenMeta={metaTokenMeta}
                        clusterSummary={metaClusterSummary}
                        activeTypes={new Set(['unit', 'trait', 'item'])}
                        playbook={metaPlaybook}
                        playbookLoading={metaPlaybookLoading}
                        playbookError={metaPlaybookError}
                        stale={false}
                        {tokenText}
                        {tokenIcon}
                        {tokenTypeClass}
                        onBack={goBackMeta}
                        onComputePlaybook={loadMetaPlaybook}
                        onHoverTokens={setHoverForTokens}
                        onClearHover={clearHoveredTokens}
                    />
                {:else}
                    <ClusterList
                        clusters={sortedClusters}
                        {selectedClusterId}
                        {tokenText}
                        {tokenIcon}
                        {tokenTypeClass}
                        onSelectCluster={selectCluster}
                        onHoverTokens={setHoverForTokens}
                        onClearHover={clearHoveredTokens}
                    />
                    <ClusterDetails
                        {selectedCluster}
                        activeTypes={$activeTypes}
                        {playbook}
                        {playbookLoading}
                        {playbookError}
                        {stale}
                        {tokenText}
                        {tokenIcon}
                        {tokenTypeClass}
                        onBack={goBack}
                        onComputePlaybook={loadPlaybook}
                        onHoverTokens={setHoverForTokens}
                        onClearHover={clearHoveredTokens}
                    />
                {/if}
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
        border-left: 1px solid var(--border);
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
        background: rgba(255, 255, 255, 0.1);
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

    .tabs {
        display: flex;
        align-items: center;
        padding: 0 14px;
        border-bottom: 1px solid var(--border);
    }

    .tab {
        flex: 1;
        background: transparent;
        border: none;
        border-bottom: 2px solid transparent;
        margin-bottom: -1px;
        color: var(--text-tertiary);
        padding: 10px 12px;
        font-size: 11px;
        font-weight: 800;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        cursor: pointer;
        transition: color 0.15s ease, border-color 0.15s ease;
        font-family: inherit;
        white-space: nowrap;
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

    .meta-pane {
        border-right: 1px solid var(--border);
        overflow: auto;
        min-height: 0;
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .content {
        display: grid;
        grid-template-columns: 280px 1fr;
        flex: 1;
        min-height: 0;
    }

    .content.narrow {
        grid-template-columns: 1fr;
    }

    .content.narrow :global(.cluster-list) {
        border-right: none;
    }

    .content.narrow .meta-pane {
        border-right: none;
    }

    .content.narrow.showDetails :global(.cluster-list) {
        display: none;
    }

    .content.narrow.showDetails .meta-pane {
        display: none;
    }

    .content.narrow:not(.showDetails) :global(.details) {
        display: none;
    }

    @media (max-width: 768px) {
        .cluster-explorer {
            width: 100% !important;
            height: auto;
            border-left: none;
            border-top: 1px solid var(--border);
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
    }
</style>
