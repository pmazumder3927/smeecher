<script>
    import { fetchClusters } from '../api.js';
    import {
        selectedTokens,
        addToken,
        addTokens,
        setTokens,
        setHighlightedTokens,
        clearHighlightedTokens
    } from '../stores/state.js';
    import { getTokenType, getTokenLabel } from '../utils/tokens.js';
    import { getDisplayName, getIconUrl, hasIconFailed, markIconFailed } from '../stores/assets.js';
    import { getPlacementColor } from '../utils/colors.js';

    let open = false;
    let loading = false;
    let error = null;
    let data = null;
    let selectedClusterId = null;
    let sortBy = 'avg'; // avg | size | top4

    let params = {
        n_clusters: 15,
        use_units: true,
        use_traits: true,
        use_items: true,
        min_token_freq: 100,
        min_cluster_size: 50,
        top_k_tokens: 10,
        random_state: 42
    };

    let lastTokensKey = '';
    let stale = false;

    $: tokensKey = $selectedTokens.slice().sort().join(',');
    $: if (open && lastTokensKey && tokensKey !== lastTokensKey) stale = true;

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

    function toggleOpen() {
        open = !open;
        if (!open) {
            selectedClusterId = null;
            stale = false;
            clearHighlightedTokens();
            return;
        }
        if (!data) run();
    }

    async function run() {
        loading = true;
        error = null;
        stale = false;
        selectedClusterId = null;
        clearHighlightedTokens();
        lastTokensKey = tokensKey;

        try {
            data = await fetchClusters($selectedTokens, params);
        } catch (e) {
            error = e?.message ?? String(e);
        } finally {
            loading = false;
        }
    }

    function selectCluster(c) {
        selectedClusterId = c.cluster_id;
        setHighlightedTokens(c.signature_tokens ?? []);
    }

    function tokenText(token) {
        return getTokenLabel(token, getDisplayName);
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
        setTokens(selectedCluster.signature_tokens);
    }

    function exploreAdd() {
        if (!selectedCluster?.signature_tokens?.length) return;
        addTokens(selectedCluster.signature_tokens);
    }

    function addOne(token) {
        addToken(token);
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
</script>

<div class="cluster-explorer" class:open>
    <button class="toggle" on:click={toggleOpen} aria-label="Toggle cluster explorer">
        <span class="toggle-title">Clusters</span>
        {#if open}
            <span class="toggle-sub">close</span>
        {:else}
            <span class="toggle-sub">explore comps</span>
        {/if}
    </button>

    {#if open}
        <div class="panel">
            <div class="panel-header">
                <div class="panel-title">Cluster exploration</div>
                <div class="panel-meta">
                    {#if data?.base}
                        <span>{data.base.n.toLocaleString()} games</span>
                        <span class="dot">•</span>
                        <span style="color: {getPlacementColor(data.base.avg_placement)}">
                            {data.base.avg_placement.toFixed(2)} avg
                        </span>
                    {:else}
                        <span>{$selectedTokens.length} filters</span>
                    {/if}
                </div>
            </div>

            <div class="controls">
                <label class="control">
                    <span>k</span>
                    <input type="number" min="2" max="50" step="1" bind:value={params.n_clusters} />
                </label>

                <label class="checkbox">
                    <input type="checkbox" bind:checked={params.use_traits} />
                    <span>traits</span>
                </label>

                <label class="checkbox">
                    <input type="checkbox" bind:checked={params.use_items} />
                    <span>items</span>
                </label>

                <label class="control">
                    <span>min</span>
                    <input type="number" min="1" max="5000" step="10" bind:value={params.min_token_freq} />
                </label>

                <button class="run" disabled={loading} on:click={run}>
                    {#if loading}Running…{:else if stale}Refresh{:else}Run{/if}
                </button>
            </div>

            <div class="subcontrols">
                <div class="subcontrols-left">
                    <label class="select">
                        <span>Sort</span>
                        <select bind:value={sortBy}>
                            <option value="avg">best avg</option>
                            <option value="top4">best top4</option>
                            <option value="size">most played</option>
                        </select>
                    </label>
                </div>
                <div class="subcontrols-right">
                    {#if data?.meta?.compute_ms}
                        <span class="tiny">{data.meta.compute_ms}ms</span>
                    {/if}
                    {#if data?.meta?.features_used}
                        <span class="tiny">{data.meta.features_used} features</span>
                    {/if}
                </div>
            </div>

            {#if warning}
                <div class="callout warning">{warning}</div>
            {/if}
            {#if error}
                <div class="callout error">Failed to load clusters: {error}</div>
            {/if}

            {#if !loading && data && sortedClusters.length === 0 && !warning}
                <div class="callout">
                    No clusters met the minimum size. Try lowering <span class="mono">min</span> or increasing the sample.
                </div>
            {/if}

            <div class="content">
                <div class="cluster-list">
                    {#each sortedClusters as c}
                        <button
                            class="cluster"
                            class:selected={c.cluster_id === selectedClusterId}
                            on:click={() => selectCluster(c)}
                        >
                            <div class="cluster-top">
                                <div class="cluster-id">#{c.cluster_id}</div>
                                <div class="cluster-size">{c.size.toLocaleString()}</div>
                            </div>

                            <div class="cluster-stats">
                                <span class="avg" style="color: {getPlacementColor(c.avg_placement)}">
                                    {c.avg_placement.toFixed(2)}
                                </span>
                                <span class="delta" class:pos={c.delta_vs_base < 0} class:neg={c.delta_vs_base > 0}>
                                    {c.delta_vs_base > 0 ? '+' : ''}{c.delta_vs_base.toFixed(2)}
                                </span>
                                <span class="share">{Math.round(c.share * 100)}%</span>
                                <span class="tiny">top4 {fmtPct(c.top4_rate)}</span>
                            </div>

                            <div class="hist">
                                {#each c.placement_hist as count, idx}
                                    <div
                                        class="bar"
                                        style="
                                            height: {Math.max(2, (count / maxHist(c.placement_hist)) * 100)}%;
                                            background: {getPlacementColor(idx + 1)};
                                        "
                                    ></div>
                                {/each}
                            </div>

                            {#if c.defining_units?.length}
                                <div class="defining">
                                    {#each c.defining_units as u, i}
                                        <span class="def">{tokenText(u.token)}{i < c.defining_units.length - 1 ? ', ' : ''}</span>
                                    {/each}
                                </div>
                            {:else}
                                <div class="defining muted">(mixed)</div>
                            {/if}
                        </button>
                    {/each}
                </div>

                <div class="details">
                    {#if selectedCluster}
                        <div class="details-header">
                            <div class="details-title">
                                Cluster #{selectedCluster.cluster_id}
                                <span class="details-sub">
                                    {selectedCluster.size.toLocaleString()} games
                                </span>
                            </div>
                            <div class="details-actions">
                                <button class="action" on:click={exploreAdd} disabled={!selectedCluster.signature_tokens?.length}>
                                    Add
                                </button>
                                <button class="action primary" on:click={exploreReplace} disabled={!selectedCluster.signature_tokens?.length}>
                                    Explore
                                </button>
                            </div>
                        </div>

                        <div class="details-grid">
                            <div class="metric">
                                <div class="k">Avg</div>
                                <div class="v" style="color: {getPlacementColor(selectedCluster.avg_placement)}">
                                    {selectedCluster.avg_placement.toFixed(2)}
                                </div>
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

                        {#if selectedCluster.signature_tokens?.length}
                            <div class="sig">
                                <div class="sig-title">Signature</div>
                                <div class="sig-chips">
                                    {#each selectedCluster.signature_tokens as t}
                                        <button class="sig-chip {tokenTypeClass(t)}" on:click={() => addOne(t)}>
                                            {tokenText(t)}
                                        </button>
                                    {/each}
                                </div>
                            </div>
                        {/if}

                        <div class="token-groups">
                            <div class="group">
                                <div class="group-title">Units</div>
                                <div class="tokens">
                                    {#each selectedCluster.top_units as tok}
                                        <button class="token" on:click={() => addOne(tok.token)}>
                                            {#if tokenIcon(tok.token) && !hasIconFailed(getTokenType(tok.token), tok.token.slice(2))}
                                                <img
                                                    class="icon"
                                                    src={tokenIcon(tok.token)}
                                                    alt=""
                                                    loading="lazy"
                                                    on:error={() => markIconFailed(getTokenType(tok.token), tok.token.slice(2))}
                                                />
                                            {:else}
                                                <div class="fallback {tokenTypeClass(tok.token)}"></div>
                                            {/if}
                                            <div class="token-main">
                                                <div class="name">{tokenText(tok.token)}</div>
                                                <div class="sub">
                                                    <span class="mono">{fmtPct(tok.pct)}</span>
                                                    <span class="dot">•</span>
                                                    <span class="mono">{fmtLift(tok.lift)}</span>
                                                    <span class="dot">•</span>
                                                    <span class="muted">base {fmtPct(tok.base_pct)}</span>
                                                </div>
                                            </div>
                                            <div class="plus">+</div>
                                        </button>
                                    {/each}
                                </div>
                            </div>

                            <div class="group">
                                <div class="group-title">Traits</div>
                                <div class="tokens">
                                    {#each selectedCluster.top_traits as tok}
                                        <button class="token" on:click={() => addOne(tok.token)}>
                                            {#if tokenIcon(tok.token) && !hasIconFailed(getTokenType(tok.token), tok.token.slice(2))}
                                                <img
                                                    class="icon"
                                                    src={tokenIcon(tok.token)}
                                                    alt=""
                                                    loading="lazy"
                                                    on:error={() => markIconFailed(getTokenType(tok.token), tok.token.slice(2))}
                                                />
                                            {:else}
                                                <div class="fallback {tokenTypeClass(tok.token)}"></div>
                                            {/if}
                                            <div class="token-main">
                                                <div class="name">{tokenText(tok.token)}</div>
                                                <div class="sub">
                                                    <span class="mono">{fmtPct(tok.pct)}</span>
                                                    <span class="dot">•</span>
                                                    <span class="mono">{fmtLift(tok.lift)}</span>
                                                    <span class="dot">•</span>
                                                    <span class="muted">base {fmtPct(tok.base_pct)}</span>
                                                </div>
                                            </div>
                                            <div class="plus">+</div>
                                        </button>
                                    {/each}
                                </div>
                            </div>

                            {#if params.use_items}
                                <div class="group">
                                    <div class="group-title">Items</div>
                                    <div class="tokens">
                                        {#each selectedCluster.top_items as tok}
                                            <button class="token" on:click={() => addOne(tok.token)}>
                                                {#if tokenIcon(tok.token) && !hasIconFailed(getTokenType(tok.token), tok.token.slice(2))}
                                                    <img
                                                        class="icon"
                                                        src={tokenIcon(tok.token)}
                                                        alt=""
                                                        loading="lazy"
                                                        on:error={() => markIconFailed(getTokenType(tok.token), tok.token.slice(2))}
                                                    />
                                                {:else}
                                                    <div class="fallback {tokenTypeClass(tok.token)}"></div>
                                                {/if}
                                                <div class="token-main">
                                                    <div class="name">{tokenText(tok.token)}</div>
                                                    <div class="sub">
                                                        <span class="mono">{fmtPct(tok.pct)}</span>
                                                        <span class="dot">•</span>
                                                        <span class="mono">{fmtLift(tok.lift)}</span>
                                                        <span class="dot">•</span>
                                                        <span class="muted">base {fmtPct(tok.base_pct)}</span>
                                                    </div>
                                                </div>
                                                <div class="plus">+</div>
                                            </button>
                                        {/each}
                                    </div>
                                </div>
                            {/if}
                        </div>
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
        margin-bottom: 16px;
        flex-shrink: 0;
    }

    .toggle {
        width: 100%;
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        color: var(--text-primary);
        border-radius: 10px;
        padding: 10px 12px;
        cursor: pointer;
        display: flex;
        align-items: baseline;
        justify-content: space-between;
        gap: 12px;
        transition: border-color 0.2s ease, background 0.2s ease;
        font-family: inherit;
    }

    .toggle:hover {
        border-color: var(--border-hover);
        background: var(--bg-tertiary);
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

    .panel {
        width: 100%;
        height: min(520px, 46vh);
        margin-top: 10px;
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 18px 60px rgba(0, 0, 0, 0.55);
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
        width: 64px;
        background: transparent;
        border: none;
        outline: none;
        color: var(--text-primary);
        font-weight: 700;
        font-size: 12px;
        font-family: inherit;
    }

    .checkbox {
        display: flex;
        align-items: center;
        gap: 6px;
        color: var(--text-secondary);
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.02em;
        text-transform: lowercase;
        user-select: none;
    }

    .checkbox input {
        accent-color: var(--accent);
    }

    .run {
        margin-left: auto;
        background: var(--accent);
        color: #000;
        border: none;
        border-radius: 8px;
        padding: 8px 10px;
        font-size: 11px;
        font-weight: 900;
        cursor: pointer;
        transition: background 0.2s ease;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-family: inherit;
    }

    .run:disabled {
        opacity: 0.6;
        cursor: default;
    }

    .run:hover:not(:disabled) {
        background: var(--accent-hover);
    }

    .subcontrols {
        display: flex;
        justify-content: space-between;
        align-items: center;
        padding: 8px 14px;
        border-bottom: 1px solid var(--border);
        gap: 10px;
    }

    .select {
        display: flex;
        align-items: center;
        gap: 8px;
        color: var(--text-tertiary);
        font-size: 10px;
        font-weight: 700;
        text-transform: uppercase;
        letter-spacing: 0.1em;
    }

    select {
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        color: var(--text-primary);
        border-radius: 8px;
        padding: 6px 8px;
        font-size: 11px;
        font-weight: 700;
        font-family: inherit;
    }

    .tiny {
        font-size: 10px;
        color: var(--text-tertiary);
        font-weight: 600;
        margin-left: 10px;
        white-space: nowrap;
    }

    .callout {
        padding: 10px 14px;
        font-size: 12px;
        color: var(--text-secondary);
        border-bottom: 1px solid var(--border);
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
        grid-template-columns: 320px 1fr;
        height: 100%;
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
        padding: 10px;
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

    .cluster-top {
        display: flex;
        justify-content: space-between;
        align-items: baseline;
        margin-bottom: 6px;
    }

    .cluster-id {
        font-weight: 900;
        letter-spacing: 0.03em;
        font-size: 11px;
        color: var(--text-secondary);
    }

    .cluster-size {
        font-weight: 800;
        font-size: 11px;
        color: var(--text-tertiary);
    }

    .cluster-stats {
        display: flex;
        align-items: baseline;
        gap: 8px;
        margin-bottom: 8px;
        flex-wrap: wrap;
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

    .delta.pos {
        color: var(--success);
    }

    .delta.neg {
        color: var(--error);
    }

    .share {
        font-size: 10px;
        font-weight: 800;
        color: var(--text-tertiary);
        text-transform: uppercase;
        letter-spacing: 0.08em;
    }

    .hist {
        display: grid;
        grid-template-columns: repeat(8, 1fr);
        gap: 2px;
        align-items: end;
        height: 22px;
        margin-bottom: 8px;
    }

    .bar {
        width: 100%;
        border-radius: 2px;
        opacity: 0.85;
    }

    .defining {
        font-size: 11px;
        color: var(--text-secondary);
        line-height: 1.35;
        min-height: 14px;
    }

    .defining.muted {
        color: var(--text-tertiary);
    }

    .details {
        overflow: auto;
        min-height: 0;
        padding: 12px 14px 16px;
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
        justify-content: space-between;
        gap: 10px;
        margin-bottom: 12px;
    }

    .details-title {
        font-size: 14px;
        font-weight: 900;
        letter-spacing: -0.01em;
    }

    .details-sub {
        font-size: 11px;
        color: var(--text-tertiary);
        font-weight: 700;
        margin-left: 8px;
    }

    .details-actions {
        display: flex;
        gap: 8px;
    }

    .action {
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        color: var(--text-primary);
        border-radius: 9px;
        padding: 8px 10px;
        font-size: 11px;
        font-weight: 900;
        cursor: pointer;
        transition: border-color 0.2s ease;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-family: inherit;
    }

    .action:hover:not(:disabled) {
        border-color: var(--border-hover);
    }

    .action.primary {
        border-color: rgba(0, 112, 243, 0.8);
        background: rgba(0, 112, 243, 0.12);
    }

    .action:disabled {
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
        background: rgba(17, 17, 17, 0.5);
    }

    .sig-title {
        font-size: 9px;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        font-weight: 900;
        margin-bottom: 8px;
    }

    .sig-chips {
        display: flex;
        flex-wrap: wrap;
        gap: 6px;
    }

    .sig-chip {
        border: 1px solid var(--border);
        border-radius: 999px;
        padding: 6px 10px;
        font-size: 11px;
        color: var(--text-primary);
        background: var(--bg-tertiary);
        cursor: pointer;
        transition: border-color 0.2s ease, background 0.2s ease;
        font-family: inherit;
    }

    .sig-chip:hover {
        border-color: var(--border-hover);
        background: rgba(17, 17, 17, 0.95);
    }

    .sig-chip.unit {
        box-shadow: inset 0 0 0 1px rgba(255, 107, 157, 0.25);
    }

    .sig-chip.item {
        box-shadow: inset 0 0 0 1px rgba(0, 217, 255, 0.25);
    }

    .sig-chip.trait {
        box-shadow: inset 0 0 0 1px rgba(168, 85, 247, 0.25);
    }

    .token-groups {
        display: flex;
        flex-direction: column;
        gap: 14px;
    }

    .group-title {
        font-size: 9px;
        letter-spacing: 0.12em;
        text-transform: uppercase;
        color: var(--text-tertiary);
        font-weight: 900;
        margin-bottom: 8px;
    }

    .tokens {
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .token {
        display: flex;
        align-items: center;
        gap: 10px;
        padding: 10px;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: rgba(17, 17, 17, 0.5);
        cursor: pointer;
        transition: border-color 0.2s ease, background 0.2s ease;
        color: var(--text-primary);
        text-align: left;
        font-family: inherit;
    }

    .token:hover {
        border-color: var(--border-hover);
        background: rgba(17, 17, 17, 0.85);
    }

    .icon {
        width: 30px;
        height: 30px;
        border-radius: 10px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        flex-shrink: 0;
        object-fit: cover;
    }

    .fallback {
        width: 30px;
        height: 30px;
        border-radius: 10px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        flex-shrink: 0;
        position: relative;
    }

    .fallback.unit::after,
    .fallback.item::after,
    .fallback.trait::after {
        content: '';
        position: absolute;
        inset: 5px;
        border-radius: 8px;
        opacity: 0.9;
    }

    .fallback.unit::after { background: rgba(255, 107, 157, 0.45); }
    .fallback.item::after { background: rgba(0, 217, 255, 0.45); }
    .fallback.trait::after { background: rgba(168, 85, 247, 0.45); }

    .token-main {
        flex: 1;
        min-width: 0;
    }

    .name {
        font-size: 13px;
        font-weight: 800;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
    }

    .sub {
        font-size: 11px;
        color: var(--text-tertiary);
        display: flex;
        align-items: center;
        gap: 6px;
        margin-top: 3px;
        flex-wrap: wrap;
    }

    .mono {
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .muted {
        color: var(--text-tertiary);
    }

    .plus {
        width: 24px;
        height: 24px;
        border-radius: 8px;
        display: grid;
        place-items: center;
        background: rgba(255, 255, 255, 0.06);
        border: 1px solid var(--border);
        color: var(--text-secondary);
        font-weight: 900;
        flex-shrink: 0;
    }

    @media (max-width: 940px) {
        .panel {
            height: min(560px, 52vh);
        }
        .content {
            grid-template-columns: 300px 1fr;
        }
    }

    @media (max-width: 780px) {
        .panel {
            height: min(720px, 70vh);
        }

        .content {
            grid-template-columns: 1fr;
        }

        .cluster-list {
            border-right: none;
            border-bottom: 1px solid var(--border);
            max-height: 260px;
        }
    }
</style>
