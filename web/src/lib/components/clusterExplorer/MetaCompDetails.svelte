<script>
    import AvgPlacement from '../AvgPlacement.svelte';
    import { addToken, addTokens, setTokens } from '../../stores/state.js';
    import posthog from '../../client/posthog';
    import { fmtPct } from './formatting.js';
    import CompSummarySection from './CompSummarySection.svelte';
    import PlaybookSection from './PlaybookSection.svelte';
    import TopTokensSection from './TopTokensSection.svelte';

    const TFTACADEMY_TIERLIST_URL = 'https://tftacademy.com/tierlist/comps';

    export let metaComp = null;
    export let effectiveTokens = null;
    export let tokenMeta = null;
    export let clusterSummary = null;
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
        if (!clusterSummary) return [];
        const all = [];
        if (activeTypes?.has?.('unit')) {
            for (const tok of clusterSummary.top_units ?? []) {
                all.push({ ...tok, type: 'unit' });
            }
        }
        if (activeTypes?.has?.('trait')) {
            for (const tok of clusterSummary.top_traits ?? []) {
                all.push({ ...tok, type: 'trait' });
            }
        }
        if (activeTypes?.has?.('item')) {
            for (const tok of clusterSummary.top_items ?? []) {
                all.push({ ...tok, type: 'item' });
            }
        }
        all.sort((a, b) => (b.lift ?? 0) - (a.lift ?? 0));
        return all;
    })();

    function metaCompTitle(comp) {
        return comp?.metaTitle || comp?.title || comp?.slug || 'Meta comp';
    }

    function metaCompUrl(comp) {
        const slug = String(comp?.slug ?? '').trim();
        if (!slug) return null;
        if (slug.startsWith('http://') || slug.startsWith('https://')) return slug;
        if (slug.startsWith('/')) return `https://tftacademy.com${slug}`;
        return `${TFTACADEMY_TIERLIST_URL}/${encodeURIComponent(slug)}`;
    }

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
        addToken(token, 'meta_comp');
    }

    function excludeOne(token) {
        if (!token) return;
        const base = token.startsWith('-') || token.startsWith('!') ? token.slice(1) : token;
        addToken(`-${base}`, 'meta_comp_playbook');
    }

    function tokensForExplore() {
        const tokens = Array.isArray(effectiveTokens) && effectiveTokens.length
            ? effectiveTokens
            : (metaComp?.unitTokens ?? []);
        return Array.isArray(tokens) ? tokens.filter((t) => typeof t === 'string' && t && !t.startsWith('-') && !t.startsWith('!')) : [];
    }

    function exploreReplace() {
        const tokens = tokensForExplore();
        if (tokens.length === 0) return;
        posthog.capture('meta_comp_explored', { slug: metaComp?.slug ?? null, action: 'replace' });
        setTokens(tokens, 'meta_comp');
    }

    function exploreAdd() {
        const tokens = tokensForExplore();
        if (tokens.length === 0) return;
        posthog.capture('meta_comp_explored', { slug: metaComp?.slug ?? null, action: 'add' });
        addTokens(tokens, 'meta_comp');
    }
</script>

<div class="details" bind:this={detailsEl}>
    {#if metaComp}
        {@const title = metaCompTitle(metaComp)}
        {@const tier = String(metaComp?.tier ?? '').trim()}
        {@const guideUrl = metaCompUrl(metaComp)}

        <div class="details-header">
            <button class="back-btn" on:click={onBack} aria-label="Back">
                ← Back
            </button>
            <div class="details-title">
                <span class="title-main">
                    {#if tier}<span class="tier-pill">{tier}</span>{/if}
                    <span class="title-text" title={title}>{title}</span>
                </span>
                <span class="details-sub">
                    {#if metaComp?.style}{metaComp.style}{/if}
                    {#if clusterSummary?.size}
                        <span class="dot">•</span>
                        {clusterSummary.size.toLocaleString()} games
                    {/if}
                    {#if tokenMeta?.dropped_include_tokens?.length}
                        <span class="dot">•</span>
                        <span
                            class="meta-note"
                            title={`Ignored unsupported units: ${tokenMeta.dropped_include_tokens.join(', ')}`}
                        >
                            ignored {tokenMeta.dropped_include_tokens.length}
                        </span>
                    {/if}
                </span>
            </div>
            {#if guideUrl}
                <a class="guide-link" href={guideUrl} target="_blank" rel="noopener noreferrer" title="Open TFTAcademy guide">
                    ↗
                </a>
            {/if}
        </div>

        <div class="details-nav" bind:this={detailsNavEl} aria-label="Meta comp sections">
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

        <button class="explore-hero" on:click={exploreReplace} disabled={tokensForExplore().length === 0}>
            Explore This Comp
        </button>

        <button class="add-secondary" on:click={exploreAdd} disabled={tokensForExplore().length === 0}>
            + Add to current filters
        </button>

        {#if clusterSummary}
            <div class="details-grid">
                <div class="metric">
                    <div class="k">Avg</div>
                    <div class="v"><AvgPlacement value={clusterSummary.avg_placement} /></div>
                </div>
                <div class="metric">
                    <div class="k">Δ vs base</div>
                    <div class="v" class:pos={clusterSummary.delta_vs_base < 0} class:neg={clusterSummary.delta_vs_base > 0}>
                        {clusterSummary.delta_vs_base > 0 ? '+' : ''}{clusterSummary.delta_vs_base.toFixed(2)}
                    </div>
                </div>
                <div class="metric">
                    <div class="k">Top4</div>
                    <div class="v">{fmtPct(clusterSummary.top4_rate)}</div>
                </div>
                <div class="metric">
                    <div class="k">Win</div>
                    <div class="v">{fmtPct(clusterSummary.win_rate)}</div>
                </div>
            </div>
        {/if}

        <CompSummarySection
            bind:sectionEl={compSectionEl}
            selectedCluster={clusterSummary}
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
            clusterId={metaComp?.slug ?? 'meta'}
            {playbook}
            {playbookLoading}
            {playbookError}
            {stale}
            {tokenText}
            {tokenIcon}
            {tokenTypeClass}
            onCompute={() => onComputePlaybook(metaComp)}
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
            Select a meta comp to inspect stats and playbook.
        </div>
    {/if}
</div>

<style>
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
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 4px;
    }

    .title-main {
        display: flex;
        align-items: center;
        gap: 10px;
        min-width: 0;
    }

    .tier-pill {
        width: 26px;
        height: 26px;
        border-radius: 10px;
        display: grid;
        place-items: center;
        font-size: 11px;
        font-weight: 900;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.03);
        color: var(--text-secondary);
        flex: 0 0 auto;
    }

    .title-text {
        font-size: 16px;
        font-weight: 900;
        letter-spacing: -0.02em;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        min-width: 0;
    }

    .details-sub {
        font-size: 12px;
        color: var(--text-tertiary);
        font-weight: 600;
        display: flex;
        gap: 8px;
        align-items: center;
        flex-wrap: wrap;
    }

    .meta-note {
        font-size: 11px;
        color: rgba(255, 193, 7, 0.9);
        font-weight: 800;
    }

    .dot {
        opacity: 0.6;
    }

    .guide-link {
        width: 32px;
        height: 32px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        border-radius: 10px;
        color: var(--text-tertiary);
        font-weight: 900;
        cursor: pointer;
        padding: 0;
        display: grid;
        place-items: center;
        text-decoration: none;
        flex: 0 0 auto;
    }

    .guide-link:hover {
        border-color: rgba(45, 212, 191, 0.35);
        color: rgba(45, 212, 191, 0.95);
        background: rgba(45, 212, 191, 0.06);
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
