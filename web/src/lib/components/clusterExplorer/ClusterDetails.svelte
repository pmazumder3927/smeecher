<script>
    import AvgPlacement from '../AvgPlacement.svelte';
    import { addToken, addTokens, setTokens } from '../../stores/state.js';
    import posthog from '../../client/posthog';
    import { fmtPct } from './formatting.js';
    import CompSummarySection from './CompSummarySection.svelte';
    import PlaybookSection from './PlaybookSection.svelte';
    import TopTokensSection from './TopTokensSection.svelte';

    export let selectedCluster = null;
    export let activeTypes = new Set(['unit', 'item', 'trait']);
    export let playbook = null;
    export let playbookLoading = false;
    export let playbookError = null;
    export let stale = false;
    export let tokenText = (t) => String(t ?? '');
    export let tokenIcon = () => null;
    export let tokenTypeClass = () => 'unknown';
    export let onBack = () => {};
    export let onComputePlaybook = () => {};
    export let onHoverTokens = () => {};
    export let onClearHover = () => {};

    let detailsEl;
    let detailsNavEl;
    let compSectionEl;
    let playbookSectionEl;
    let tokensSectionEl;

    $: unifiedTokens = (() => {
        if (!selectedCluster) return [];
        const all = [];
        if (activeTypes?.has?.('unit')) {
            for (const tok of selectedCluster.top_units ?? []) {
                all.push({ ...tok, type: 'unit' });
            }
        }
        if (activeTypes?.has?.('trait')) {
            for (const tok of selectedCluster.top_traits ?? []) {
                all.push({ ...tok, type: 'trait' });
            }
        }
        if (activeTypes?.has?.('item')) {
            for (const tok of selectedCluster.top_items ?? []) {
                all.push({ ...tok, type: 'item' });
            }
        }
        all.sort((a, b) => (b.lift ?? 0) - (a.lift ?? 0));
        return all;
    })();

    function scrollDetailsTo(el) {
        if (!detailsEl || !el) return;
        const navH = detailsNavEl?.getBoundingClientRect?.().height ?? 0;
        const top = el.offsetTop - navH - 10;
        detailsEl.scrollTo({ top: Math.max(0, top), behavior: 'smooth' });
    }

    function scrollDetailsTop() {
        if (!detailsEl) return;
        detailsEl.scrollTo({ top: 0, behavior: 'smooth' });
    }

    function addOne(token) {
        addToken(token, 'cluster');
    }

    function excludeOne(token) {
        if (!token) return;
        const base = token.startsWith('-') || token.startsWith('!') ? token.slice(1) : token;
        addToken(`-${base}`, 'cluster_playbook');
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
</script>

<div class="details" bind:this={detailsEl}>
    {#if selectedCluster}
        <div class="details-header">
            <button class="back-btn" on:click={onBack} aria-label="Back to clusters">
                ← Back
            </button>
            <div class="details-title">
                Cluster #{selectedCluster.cluster_id}
                <span class="details-sub">{selectedCluster.size.toLocaleString()} games</span>
            </div>
        </div>

        <div class="details-nav" bind:this={detailsNavEl} aria-label="Cluster sections">
            <button type="button" class="details-nav-btn" on:click={scrollDetailsTop}>Overview</button>
            <button type="button" class="details-nav-btn" on:click={() => scrollDetailsTo(compSectionEl)}>
                Comp
            </button>
            <button type="button" class="details-nav-btn" on:click={() => scrollDetailsTo(playbookSectionEl)}>
                Playbook
            </button>
            <button
                type="button"
                class="details-nav-btn"
                disabled={unifiedTokens.length === 0}
                on:click={() => scrollDetailsTo(tokensSectionEl)}
            >
                Tokens
            </button>
        </div>

        <button class="explore-hero" on:click={exploreReplace} disabled={!selectedCluster.signature_tokens?.length}>
            Explore This Cluster
        </button>

        <button class="add-secondary" on:click={exploreAdd} disabled={!selectedCluster.signature_tokens?.length}>
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

        <CompSummarySection
            bind:sectionEl={compSectionEl}
            {selectedCluster}
            {playbook}
            {playbookLoading}
            {tokenText}
            {tokenIcon}
            onAddToken={addOne}
            onHoverTokens={onHoverTokens}
            onClearHover={onClearHover}
        />

        <PlaybookSection
            bind:sectionEl={playbookSectionEl}
            clusterId={selectedCluster.cluster_id}
            {playbook}
            {playbookLoading}
            {playbookError}
            {stale}
            {tokenText}
            {tokenIcon}
            {tokenTypeClass}
            onCompute={onComputePlaybook}
            onAddToken={addOne}
            onExcludeToken={excludeOne}
            onHoverTokens={onHoverTokens}
            onClearHover={onClearHover}
        />

        {#if unifiedTokens.length > 0}
            <TopTokensSection
                bind:sectionEl={tokensSectionEl}
                {unifiedTokens}
                {tokenText}
                {tokenIcon}
                onAddToken={addOne}
                onHoverTokens={onHoverTokens}
                onClearHover={onClearHover}
            />
        {/if}
    {:else}
        <div class="details-empty">
            Select a cluster to inspect defining units, traits, and item prevalence.
        </div>
    {/if}
</div>

<style>
    .details {
        overflow: auto;
        min-height: 0;
        padding: 12px 14px 16px;
    }

    .details-nav {
        position: sticky;
        top: 0;
        z-index: 10;
        display: flex;
        align-items: center;
        gap: 6px;
        margin: 0 -14px 12px;
        padding: 10px 14px;
        background: var(--bg-secondary);
        border-bottom: 1px solid var(--border);
    }

    .details-nav-btn {
        height: 28px;
        padding: 0 10px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        color: var(--text-secondary);
        font-size: 10px;
        font-weight: 750;
        letter-spacing: 0.03em;
        text-transform: uppercase;
        cursor: pointer;
        transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
        font-family: inherit;
        white-space: nowrap;
    }

    .details-nav-btn:hover:not(:disabled) {
        background: rgba(255, 255, 255, 0.06);
        border-color: var(--border-hover);
        color: var(--text-primary);
    }

    .details-nav-btn:disabled {
        opacity: 0.45;
        cursor: default;
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

    @media (max-width: 768px) {
        .explore-hero {
            padding: 16px 20px;
            font-size: 15px;
        }

        .details-grid {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }
</style>

