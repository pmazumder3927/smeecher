<script>
    import { onMount } from 'svelte';
    import AvgPlacement from '../AvgPlacement.svelte';
    import { getTokenType } from '../../utils/tokens.js';
    import { getPlacementColor } from '../../utils/colors.js';
    import { fetchTokenStats } from '../../api.js';
    import { fetchTFTAcademyMetaComps } from '../../utils/tftacademyMetaComps.js';
    import { hasIconFailed, markIconFailed } from '../../stores/assets.js';
    import posthog from '../../client/posthog';

    const TFTACADEMY_TIERLIST_URL = 'https://tftacademy.com/tierlist/comps';
    const META_COMP_PREVIEW_LIMIT = 12;

    export let tokenText = (t) => String(t ?? '');
    export let tokenIcon = () => null;
    export let tokenTypeClass = () => 'unknown';
    export let selectedSlug = null;
    export let onSelectComp = () => {};
    export let onHoverTokens = () => {};
    export let onClearHover = () => {};

    let metaCompsLoading = false;
    let metaCompsError = null;
    let metaComps = [];
    let metaCompsPatch = null;
    let metaCompsLastUpdated = null;
    let metaCompsExpanded = true;
    let metaCompsShowAll = false;
    let metaCompsFetchVersion = 0;

    let statsBySlug = {};

    $: metaCompsVisible = metaCompsShowAll ? metaComps : metaComps.slice(0, META_COMP_PREVIEW_LIMIT);

    function metaCompTitle(comp) {
        return comp?.metaTitle || comp?.title || comp?.slug || 'Meta comp';
    }

    function tierClass(tier) {
        const t = String(tier ?? '').trim().toUpperCase();
        if (t === 'S') return 's';
        if (t === 'A') return 'a';
        if (t === 'B') return 'b';
        if (t === 'C') return 'c';
        if (t === 'D') return 'd';
        return 'other';
    }

    function metaCompUrl(comp) {
        const slug = String(comp?.slug ?? '').trim();
        if (!slug) return null;
        if (slug.startsWith('http://') || slug.startsWith('https://')) return slug;
        if (slug.startsWith('/')) return `https://tftacademy.com${slug}`;
        return `${TFTACADEMY_TIERLIST_URL}/${encodeURIComponent(slug)}`;
    }

    function previewUnitTokens(comp, statsData = null, limit = 8) {
        const tokens = statsData?.tokens;
        if (Array.isArray(tokens) && tokens.length) {
            const out = [];
            const seen = new Set();
            for (const t of tokens) {
                if (typeof t !== 'string' || !t.startsWith('U:')) continue;
                if (t.startsWith('-') || t.startsWith('!')) continue;
                if (seen.has(t)) continue;
                seen.add(t);
                out.push(t);
                if (out.length >= limit) break;
            }
            if (out.length > 0) return out;
        }

        const seen = new Set();
        const out = [];
        for (const u of comp?.units ?? []) {
            const unit = u?.unit;
            if (!unit || seen.has(unit)) continue;
            seen.add(unit);
            out.push(`U:${unit}`);
            if (out.length >= limit) break;
        }
        if (out.length > 0) return out;
        return Array.isArray(comp?.unitTokens) ? comp.unitTokens.slice(0, limit) : [];
    }

    function maxHist(hist) {
        return Math.max(1, ...(hist ?? [1]));
    }

    function fmtPct(x) {
        return `${Math.round((x ?? 0) * 100)}%`;
    }

    async function loadMetaComps(options = {}) {
        const { force = false } = options;
        const version = ++metaCompsFetchVersion;

        metaCompsLoading = true;
        metaCompsError = null;

        try {
            const res = await fetchTFTAcademyMetaComps({ force });
            if (version !== metaCompsFetchVersion) return;

            metaComps = res?.comps ?? [];
            metaCompsPatch = res?.patch ?? null;
            metaCompsLastUpdated = res?.lastUpdated ?? null;

            posthog.capture('meta_comps_loaded', {
                source: 'tftacademy',
                count: metaComps.length,
                patch: metaCompsPatch ?? undefined,
            });
        } catch (e) {
            if (version !== metaCompsFetchVersion) return;
            metaCompsError = e?.message ?? String(e);
        } finally {
            if (version === metaCompsFetchVersion) metaCompsLoading = false;
        }
    }

    async function loadStats(comp) {
        const slug = comp?.slug;
        if (!slug) return;
        const existing = statsBySlug?.[slug];
        if (existing?.loading || existing?.data) return;

        statsBySlug = { ...statsBySlug, [slug]: { loading: true, error: null, data: null } };
        try {
            const data = await fetchTokenStats(comp?.unitTokens ?? []);
            statsBySlug = { ...statsBySlug, [slug]: { loading: false, error: null, data } };
        } catch (e) {
            statsBySlug = { ...statsBySlug, [slug]: { loading: false, error: e?.message ?? String(e), data: null } };
        }
    }

    $: if (metaCompsVisible.length > 0) {
        for (const comp of metaCompsVisible) {
            const slug = comp?.slug;
            if (!slug) continue;
            if (!statsBySlug?.[slug]) {
                loadStats(comp);
            }
        }
    }

    onMount(() => {
        if (metaComps.length === 0 && !metaCompsLoading && !metaCompsError) {
            loadMetaComps();
        }
    });
</script>

<div class="meta-comps">
    <div class="meta-comps-header">
        <button
            type="button"
            class="meta-comps-toggle"
            aria-expanded={metaCompsExpanded}
            on:click={() => (metaCompsExpanded = !metaCompsExpanded)}
        >
            <span class="meta-comps-title">Meta comps</span>
            <span class="meta-comps-source">TFTAcademy</span>
            {#if metaCompsPatch}
                <span class="meta-comps-patch">Patch {metaCompsPatch}</span>
            {/if}
        </button>

        <button
            type="button"
            class="meta-comps-refresh"
            title="Refresh meta comps"
            disabled={metaCompsLoading}
            on:click={() => loadMetaComps({ force: true })}
        >
            ↻
        </button>

        <a
            class="meta-comps-open"
            href={TFTACADEMY_TIERLIST_URL}
            target="_blank"
            rel="noopener noreferrer"
            title="Open TFTAcademy tierlist"
        >
            ↗
        </a>
    </div>

    {#if metaCompsExpanded}
        {#if metaCompsError}
            <div class="meta-comps-state error">
                <span class="meta-comps-state-text">{metaCompsError}</span>
                <button class="meta-comps-retry" on:click={() => loadMetaComps({ force: true })}>Retry</button>
            </div>
        {:else if metaCompsLoading && metaComps.length === 0}
            <div class="meta-comps-state">Loading…</div>
        {:else if metaComps.length === 0}
            <div class="meta-comps-state">No meta comps found.</div>
        {:else}
            <div class="meta-comps-list">
                {#each metaCompsVisible as comp (comp.slug)}
                    {@const title = metaCompTitle(comp)}
                    {@const tier = (comp.tier || '').trim()}
                    {@const guideUrl = metaCompUrl(comp)}
                    {@const tokens = comp?.unitTokens ?? []}
                    {@const stats = statsBySlug?.[comp.slug] ?? null}
                    {@const statsData = stats?.data ?? null}
                    {@const effectiveTokens = Array.isArray(statsData?.tokens)
                        ? statsData.tokens.filter((t) => typeof t === 'string' && t && !t.startsWith('-') && !t.startsWith('!'))
                        : tokens}
                    {@const previewUnits = previewUnitTokens(comp, statsData, 8)}

                    <button
                        type="button"
                        class="meta-comp"
                        class:selected={comp.slug === selectedSlug}
                        on:click={() => onSelectComp({ ...comp, unitTokens: effectiveTokens })}
                        on:mouseenter={() => onHoverTokens(effectiveTokens)}
                        on:mouseleave={onClearHover}
                        title={title}
                    >
                        <div class="meta-comp-header">
                            <div class="meta-comp-left">
                                <div class="meta-comp-name-row">
                                    <span class={`meta-tier ${tierClass(tier)}`}>{tier || '—'}</span>
                                    <span class="meta-comp-name" title={title}>{title}</span>
                                    {#if comp.style}
                                        <span class="meta-comp-style">{comp.style}</span>
                                    {/if}
                                </div>
                                <div class="meta-comp-blurb">
                                    {#if statsData}
                                        {#if statsData.size > 0}
                                            Top4 {fmtPct(statsData.top4_rate)} • Win {fmtPct(statsData.win_rate)} • {statsData.size.toLocaleString()} games
                                        {:else}
                                            No matches
                                        {/if}
                                    {:else if stats?.error}
                                        Stats unavailable
                                    {:else}
                                        Loading stats…
                                    {/if}
                                </div>
                            </div>

                            <div class="meta-comp-actions">
                                {#if guideUrl}
                                    <a
                                        class="meta-comp-link"
                                        href={guideUrl}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        title="Open guide on TFTAcademy"
                                        on:click|stopPropagation
                                    >
                                        ↗
                                    </a>
                                {/if}
                            </div>
                        </div>

                        <div class="meta-comp-visual">
                            <div class="sig-icons" aria-label="Units">
                                {#each previewUnits as t}
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
                            </div>

                            {#if statsData?.placement_hist}
                                <div class="hist" aria-label="Placement distribution">
                                    {#each statsData.placement_hist as count, idx}
                                        <div class="hist-col">
                                            <div class="bar-wrap">
                                                <div
                                                    class="bar"
                                                    style="
                                                        height: {Math.max(8, (count / maxHist(statsData.placement_hist)) * 100)}%;
                                                        background: {getPlacementColor(idx + 1)};
                                                    "
                                                ></div>
                                            </div>
                                            <span class="hist-label">{idx + 1}</span>
                                        </div>
                                    {/each}
                                </div>
                            {:else}
                                <div class="hist skeleton" aria-hidden="true">
                                    {#each Array(8) as _, idx (idx)}
                                        <div class="hist-col">
                                            <div class="bar-wrap">
                                                <div class="bar sk"></div>
                                            </div>
                                            <span class="hist-label">{idx + 1}</span>
                                        </div>
                                    {/each}
                                </div>
                            {/if}

                            <div class="meta-comp-metrics">
                                {#if statsData}
                                    <div class="metric-top">
                                        <span class="avg"><AvgPlacement value={statsData.avg_placement} /></span>
                                        <span class="delta" class:pos={statsData.delta_vs_base < 0} class:neg={statsData.delta_vs_base > 0}>
                                            {statsData.delta_vs_base > 0 ? '+' : ''}{statsData.delta_vs_base.toFixed(2)}
                                        </span>
                                    </div>
                                    <div class="metric-bottom">
                                        <span class="cluster-size">{statsData.size.toLocaleString()}</span>
                                        <span class="cluster-share">{Math.round((statsData.share ?? 0) * 100)}%</span>
                                    </div>
                                {:else}
                                    <div class="metric-top">
                                        <span class="avg">—</span>
                                        <span class="delta">—</span>
                                    </div>
                                    <div class="metric-bottom">
                                        <span class="cluster-size">—</span>
                                        <span class="cluster-share">—</span>
                                    </div>
                                {/if}
                            </div>
                        </div>
                    </button>
                {/each}

                {#if metaComps.length > META_COMP_PREVIEW_LIMIT}
                    <button type="button" class="meta-comps-more" on:click={() => (metaCompsShowAll = !metaCompsShowAll)}>
                        {metaCompsShowAll ? 'Show less' : `Show all (${metaComps.length})`}
                    </button>
                {/if}

                {#if metaCompsLastUpdated}
                    <div class="meta-comps-updated">Updated {new Date(metaCompsLastUpdated).toLocaleString()}</div>
                {/if}
            </div>
        {/if}
    {/if}
</div>

<style>
    .meta-comps {
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .meta-comps-header {
        display: flex;
        align-items: center;
        gap: 8px;
    }

    .meta-comps-toggle {
        flex: 1;
        display: flex;
        align-items: center;
        gap: 8px;
        background: transparent;
        border: 0;
        color: var(--text-primary);
        padding: 0;
        cursor: pointer;
        font-family: inherit;
        text-align: left;
        min-width: 0;
    }

    .meta-comps-title {
        font-size: 12px;
        font-weight: 900;
        white-space: nowrap;
    }

    .meta-comps-source {
        font-size: 10px;
        font-weight: 900;
        color: var(--text-tertiary);
        padding: 3px 8px;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.03);
        white-space: nowrap;
    }

    .meta-comps-patch {
        margin-left: auto;
        font-size: 10px;
        font-weight: 800;
        color: var(--text-tertiary);
        white-space: nowrap;
    }

    .meta-comps-refresh,
    .meta-comps-open {
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
    }

    .meta-comps-refresh:hover,
    .meta-comps-open:hover {
        border-color: var(--border-hover);
        background: rgba(255, 255, 255, 0.04);
        color: var(--text-secondary);
    }

    .meta-comps-refresh:disabled {
        opacity: 0.55;
        cursor: not-allowed;
    }

    .meta-comps-state {
        padding: 10px;
        font-size: 12px;
        color: var(--text-tertiary);
        display: flex;
        gap: 10px;
        align-items: center;
        justify-content: space-between;
        border: 1px solid var(--border);
        border-radius: 12px;
        background: rgba(17, 17, 17, 0.45);
    }

    .meta-comps-state.error {
        color: rgba(255, 68, 68, 0.95);
        background: rgba(255, 68, 68, 0.06);
        border-color: rgba(255, 68, 68, 0.25);
    }

    .meta-comps-state-text {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        min-width: 0;
        flex: 1;
    }

    .meta-comps-retry {
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        border-radius: 10px;
        color: var(--text-primary);
        font-weight: 800;
        font-size: 11px;
        padding: 6px 10px;
        cursor: pointer;
        flex: 0 0 auto;
        font-family: inherit;
    }

    .meta-comps-list {
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .meta-comp {
        width: 100%;
        text-align: left;
        border: 1px solid var(--border);
        background: rgba(17, 17, 17, 0.5);
        border-radius: 12px;
        padding: 10px 10px 12px;
        cursor: pointer;
        transition: border-color 0.2s ease, background 0.2s ease;
        color: var(--text-primary);
        font-family: inherit;
        display: flex;
        flex-direction: column;
        gap: 10px;
    }

    .meta-comp:hover {
        border-color: var(--border-hover);
        background: rgba(17, 17, 17, 0.8);
    }

    .meta-comp.selected {
        border-color: rgba(0, 112, 243, 0.65);
        box-shadow: 0 0 0 1px rgba(0, 112, 243, 0.25);
    }

    .meta-comp-header {
        display: flex;
        justify-content: space-between;
        align-items: flex-start;
        gap: 10px;
    }

    .meta-comp-left {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
        gap: 4px;
    }

    .meta-comp-name-row {
        display: flex;
        align-items: center;
        gap: 8px;
        min-width: 0;
    }

    .meta-tier {
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

    .meta-tier.s {
        color: rgba(45, 212, 191, 0.95);
        border-color: rgba(45, 212, 191, 0.35);
        background: rgba(45, 212, 191, 0.08);
    }
    .meta-tier.a {
        color: rgba(96, 165, 250, 0.95);
        border-color: rgba(96, 165, 250, 0.35);
        background: rgba(96, 165, 250, 0.08);
    }
    .meta-tier.b {
        color: rgba(251, 191, 36, 0.95);
        border-color: rgba(251, 191, 36, 0.35);
        background: rgba(251, 191, 36, 0.08);
    }
    .meta-tier.c {
        color: rgba(248, 113, 113, 0.95);
        border-color: rgba(248, 113, 113, 0.35);
        background: rgba(248, 113, 113, 0.08);
    }
    .meta-tier.d {
        color: rgba(248, 113, 113, 0.75);
        border-color: rgba(248, 113, 113, 0.25);
        background: rgba(248, 113, 113, 0.05);
    }

    .meta-comp-name {
        font-size: 13px;
        font-weight: 850;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        min-width: 0;
    }

    .meta-comp-style {
        font-size: 10px;
        color: var(--text-tertiary);
        font-weight: 800;
        padding: 3px 8px;
        border-radius: 999px;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.03);
        flex: 0 0 auto;
        white-space: nowrap;
    }

    .meta-comp-blurb {
        font-size: 11px;
        color: var(--text-tertiary);
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .meta-comp-actions {
        display: flex;
        align-items: center;
        gap: 8px;
        flex: 0 0 auto;
    }

    .meta-comp-link {
        width: 32px;
        height: 32px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        border-radius: 10px;
        color: var(--text-tertiary);
        font-weight: 900;
        font-size: 14px;
        cursor: pointer;
        display: grid;
        place-items: center;
        padding: 0;
        text-decoration: none;
    }

    .meta-comp-link:hover {
        border-color: rgba(45, 212, 191, 0.35);
        color: rgba(45, 212, 191, 0.95);
        background: rgba(45, 212, 191, 0.06);
    }

    .meta-comp-visual {
        display: grid;
        grid-template-columns: 1fr auto;
        gap: 12px;
        align-items: center;
    }

    .sig-icons {
        display: flex;
        gap: 6px;
        align-items: center;
        min-width: 0;
        flex-wrap: wrap;
    }

    .sig-icon {
        width: 26px;
        height: 26px;
        border-radius: 10px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        overflow: hidden;
        display: grid;
        place-items: center;
        flex: 0 0 auto;
    }

    .sig-icon img {
        width: 100%;
        height: 100%;
        object-fit: cover;
        display: block;
    }

    .sig-fallback {
        width: 12px;
        height: 12px;
        border-radius: 6px;
        opacity: 0.8;
    }

    .sig-fallback.unit { background: rgba(255, 107, 157, 0.5); }
    .sig-fallback.trait { background: rgba(168, 85, 247, 0.5); }
    .sig-fallback.item { background: rgba(0, 217, 255, 0.5); }

    .hist {
        display: flex;
        gap: 4px;
        align-items: flex-end;
        justify-content: flex-end;
        flex: 0 0 auto;
    }

    .hist-col {
        width: 14px;
        display: flex;
        flex-direction: column;
        align-items: center;
        gap: 4px;
    }

    .bar-wrap {
        width: 100%;
        height: 38px;
        display: flex;
        align-items: flex-end;
        justify-content: center;
    }

    .bar {
        width: 100%;
        border-radius: 5px;
    }

    .hist.skeleton .bar.sk {
        height: 14px;
        background: rgba(255, 255, 255, 0.06);
        border: 1px solid rgba(255, 255, 255, 0.06);
        animation: pulse 1.4s ease-in-out infinite;
    }

    @keyframes pulse {
        0%, 100% { opacity: 0.65; }
        50% { opacity: 1; }
    }

    .hist-label {
        font-size: 9px;
        color: var(--text-tertiary);
        font-weight: 700;
    }

    .meta-comp-metrics {
        display: flex;
        flex-direction: column;
        align-items: flex-end;
        gap: 6px;
        min-width: 90px;
    }

    .metric-top {
        display: flex;
        gap: 8px;
        align-items: baseline;
        justify-content: flex-end;
        width: 100%;
    }

    .avg {
        font-size: 13px;
        font-weight: 900;
        color: var(--text-primary);
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .delta {
        font-size: 11px;
        font-weight: 900;
        color: var(--text-tertiary);
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .delta.pos {
        color: var(--success);
    }

    .delta.neg {
        color: var(--error);
    }

    .metric-bottom {
        display: flex;
        gap: 8px;
        align-items: baseline;
        justify-content: flex-end;
        width: 100%;
    }

    .cluster-size,
    .cluster-share {
        font-size: 10px;
        color: var(--text-tertiary);
        font-weight: 700;
        letter-spacing: 0.02em;
        font-variant-numeric: tabular-nums;
        font-feature-settings: "tnum" 1;
    }

    .meta-comps-more {
        width: 100%;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.02);
        border-radius: 12px;
        color: var(--text-secondary);
        font-weight: 800;
        font-size: 11px;
        padding: 8px 10px;
        cursor: pointer;
        font-family: inherit;
    }

    .meta-comps-more:hover {
        border-color: var(--border-hover);
        background: rgba(255, 255, 255, 0.04);
        color: var(--text-primary);
    }

    .meta-comps-updated {
        font-size: 10px;
        color: var(--text-tertiary);
        padding-top: 2px;
    }
</style>
