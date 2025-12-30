<script>
    import { onMount } from 'svelte';
    import { addTokens, setTokens } from '../../stores/state.js';
    import { getTokenType } from '../../utils/tokens.js';
    import { fetchTFTAcademyMetaComps } from '../../utils/tftacademyMetaComps.js';
    import { hasIconFailed, markIconFailed } from '../../stores/assets.js';
    import posthog from '../../client/posthog';

    const TFTACADEMY_TIERLIST_URL = 'https://tftacademy.com/tierlist/comps';
    const META_COMP_PREVIEW_LIMIT = 12;

    export let tokenText;
    export let tokenIcon;
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

    $: metaCompsVisible = metaCompsShowAll ? metaComps : metaComps.slice(0, META_COMP_PREVIEW_LIMIT);

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

    function previewUnitsForComp(comp, limit = 4) {
        const out = [];
        const seen = new Set();

        for (const u of comp?.units ?? []) {
            const unit = u?.unit;
            if (!unit || seen.has(unit)) continue;
            seen.add(unit);
            out.push(u);
            if (out.length >= limit) break;
        }

        return out;
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

    function exploreMetaCompReplace(comp) {
        const tokens = comp?.unitTokens ?? [];
        if (!Array.isArray(tokens) || tokens.length === 0) return;
        posthog.capture('meta_comp_explored', {
            slug: comp?.slug ?? null,
            tier: comp?.tier ?? null,
            title: metaCompTitle(comp),
            action: 'replace',
        });
        setTokens(tokens, 'meta_comp');
    }

    function exploreMetaCompAdd(comp) {
        const tokens = comp?.unitTokens ?? [];
        if (!Array.isArray(tokens) || tokens.length === 0) return;
        posthog.capture('meta_comp_explored', {
            slug: comp?.slug ?? null,
            tier: comp?.tier ?? null,
            title: metaCompTitle(comp),
            action: 'add',
        });
        addTokens(tokens, 'meta_comp');
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
                <button class="meta-comps-retry" on:click={() => loadMetaComps({ force: true })}>
                    Retry
                </button>
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
                    {@const previewUnits = previewUnitsForComp(comp, 4)}
                    <div class="meta-comp-row">
                        <button
                            type="button"
                            class="meta-comp-main"
                            disabled={!comp?.unitTokens?.length}
                            on:click={() => exploreMetaCompReplace(comp)}
                            on:mouseenter={() => onHoverTokens(comp.unitTokens ?? [])}
                            on:mouseleave={onClearHover}
                            title={`Explore: ${title}${comp.style ? ` • ${comp.style}` : ''}`}
                        >
                            <span class={`meta-tier ${tierClass(tier)}`}>{tier || '—'}</span>
                            <span class="meta-comp-name" title={title}>{title}</span>
                            <span class="meta-comp-sub">
                                {#if previewUnits.length > 0}
                                    <span class="meta-comp-icons" aria-hidden="true">
                                        {#each previewUnits as u, idx (`${u.unit ?? 'unit'}:${idx}`)}
                                            {@const tok = `U:${u.unit}`}
                                            <span class="meta-comp-icon" title={tokenText(tok)}>
                                                {#if tokenIcon(tok) && !hasIconFailed(getTokenType(tok), tok.slice(2))}
                                                    <img
                                                        src={tokenIcon(tok)}
                                                        alt=""
                                                        loading="lazy"
                                                        on:error={() => markIconFailed(getTokenType(tok), tok.slice(2))}
                                                    />
                                                {:else}
                                                    <span class="meta-comp-icon-fallback"></span>
                                                {/if}
                                            </span>
                                        {/each}
                                    </span>
                                {/if}
                                {#if comp.style}
                                    <span class="meta-comp-style">{comp.style}</span>
                                {/if}
                            </span>
                        </button>

                        <button
                            type="button"
                            class="meta-comp-add"
                            title="Add units"
                            disabled={!comp?.unitTokens?.length}
                            on:click={() => exploreMetaCompAdd(comp)}
                        >
                            +
                        </button>

                        {#if guideUrl}
                            <a
                                class="meta-comp-link"
                                href={guideUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                title="Open guide on TFTAcademy"
                            >
                                ↗
                            </a>
                        {/if}
                    </div>
                {/each}

                {#if metaComps.length > META_COMP_PREVIEW_LIMIT}
                    <button
                        type="button"
                        class="meta-comps-more"
                        on:click={() => (metaCompsShowAll = !metaCompsShowAll)}
                    >
                        {metaCompsShowAll ? 'Show less' : `Show all (${metaComps.length})`}
                    </button>
                {/if}

                {#if metaCompsLastUpdated}
                    <div class="meta-comps-updated">
                        Updated {new Date(metaCompsLastUpdated).toLocaleString()}
                    </div>
                {/if}
            </div>
        {/if}
    {/if}
</div>

<style>
    .meta-comps {
        border: 1px solid var(--border);
        border-radius: 12px;
        background: rgba(17, 17, 17, 0.45);
        overflow: hidden;
    }

    .meta-comps-header {
        display: flex;
        align-items: center;
        gap: 8px;
        padding: 8px 10px;
        border-bottom: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.02);
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

    .meta-comps-refresh {
        width: 32px;
        height: 32px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        border-radius: 10px;
        color: var(--text-tertiary);
        font-weight: 900;
        cursor: pointer;
        padding: 0;
    }

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
        text-decoration: none;
        display: grid;
        place-items: center;
    }

    .meta-comps-refresh:hover {
        border-color: var(--border-hover);
        background: rgba(255, 255, 255, 0.04);
        color: var(--text-secondary);
    }

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
    }

    .meta-comps-state.error {
        color: rgba(255, 68, 68, 0.95);
        background: rgba(255, 68, 68, 0.06);
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

    .meta-comps-retry:hover {
        border-color: rgba(255, 68, 68, 0.35);
        background: rgba(255, 68, 68, 0.06);
        color: rgba(255, 68, 68, 0.95);
    }

    .meta-comps-list {
        padding: 10px;
        display: flex;
        flex-direction: column;
        gap: 8px;
    }

    .meta-comp-row {
        display: flex;
        gap: 8px;
        align-items: stretch;
    }

    .meta-comp-main {
        flex: 1;
        border: 1px solid var(--border);
        background: rgba(255, 255, 255, 0.02);
        border-radius: 12px;
        padding: 10px 12px;
        display: grid;
        grid-template-columns: 26px minmax(0, 1fr);
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

    .meta-comp-main:hover:not(:disabled) {
        background: rgba(255, 255, 255, 0.04);
        border-color: rgba(45, 212, 191, 0.35);
    }

    .meta-comp-main:disabled {
        opacity: 0.6;
        cursor: not-allowed;
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
        grid-row: 1 / 3;
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
        font-size: 12px;
        font-weight: 900;
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        min-width: 0;
    }

    .meta-comp-sub {
        grid-column: 2;
        grid-row: 2;
        display: flex;
        gap: 8px;
        align-items: center;
        min-width: 0;
    }

    .meta-comp-icons {
        display: flex;
        gap: 6px;
        align-items: center;
        flex: 0 0 auto;
    }

    .meta-comp-icon {
        width: 22px;
        height: 22px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        display: flex;
        align-items: center;
        justify-content: center;
        overflow: hidden;
    }

    .meta-comp-icon img {
        width: 100%;
        height: 100%;
        object-fit: cover;
    }

    .meta-comp-icon-fallback {
        width: 100%;
        height: 100%;
        background: rgba(255, 255, 255, 0.06);
        border-radius: 7px;
    }

    .meta-comp-style {
        font-size: 11px;
        font-weight: 800;
        color: var(--text-tertiary);
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        min-width: 0;
    }

    .meta-comp-add {
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

    .meta-comp-link {
        width: 38px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        border-radius: 12px;
        color: var(--text-tertiary);
        font-weight: 900;
        font-size: 14px;
        cursor: pointer;
        flex: 0 0 auto;
        display: grid;
        place-items: center;
        padding: 0;
        text-decoration: none;
    }

    .meta-comp-add:hover:not(:disabled) {
        border-color: rgba(45, 212, 191, 0.35);
        color: rgba(45, 212, 191, 0.95);
        background: rgba(45, 212, 191, 0.06);
    }

    .meta-comp-link:hover {
        border-color: rgba(45, 212, 191, 0.35);
        color: rgba(45, 212, 191, 0.95);
        background: rgba(45, 212, 191, 0.06);
    }

    .meta-comp-add:disabled {
        opacity: 0.6;
        cursor: not-allowed;
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
