<script>
    import { getTokenType } from '../../utils/tokens.js';
    import { getIconUrl, hasIconFailed, markIconFailed } from '../../stores/assets.js';
    import { equipItemOnUnit } from '../../stores/state.js';
    import { fmtPct, stripTrailingNumber, trailingNumber } from './formatting.js';

    const TRAIT_PILL_LIMIT = 8;

    export let sectionEl;
    export let selectedCluster = null;
    export let playbook = null;
    export let playbookLoading = false;
    export let tokenText = (t) => String(t ?? '');
    export let tokenIcon = () => null;
    export let onAddToken = () => {};
    export let onHoverTokens = () => {};
    export let onClearHover = () => {};

    let hoveredItemId = null;
    let traitsExpanded = false;

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
            return compItems
                .map((row) => {
                    const itemId = row?.item;
                    const label = row?.label ?? (itemId ? tokenText(`I:${itemId}`) : '');
                    const holder = row?.best_holder?.unit ?? null;
                    return { itemId, label, holder };
                })
                .filter((r) => !!r.itemId);
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
        for (const row of compItems ?? []) {
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
            for (const row of compItems ?? []) {
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

    function addOne(token) {
        onAddToken(token);
    }
</script>

<div class="sig comp" bind:this={sectionEl}>
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
                        on:mouseenter={() => onHoverTokens([tok])}
                        on:mouseleave={onClearHover}
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
                            on:mouseenter={() => onHoverTokens([u.token])}
                            on:mouseleave={onClearHover}
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
                            on:mouseenter={() => onHoverTokens(holder ? [`I:${itemId}`, `U:${holder}`] : [`I:${itemId}`])}
                            on:mouseleave={onClearHover}
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

<style>
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
        font-feature-settings: 'tnum' 1;
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
        font-feature-settings: 'tnum' 1;
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
        font-feature-settings: 'tnum' 1;
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

    .mono {
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
    }
</style>

