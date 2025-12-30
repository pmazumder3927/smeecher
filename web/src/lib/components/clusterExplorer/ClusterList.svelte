<script>
    import AvgPlacement from '../AvgPlacement.svelte';
    import { getTokenType } from '../../utils/tokens.js';
    import { hasIconFailed, markIconFailed } from '../../stores/assets.js';
    import { getPlacementColor } from '../../utils/colors.js';

    export let clusters = [];
    export let selectedClusterId = null;
    export let tokenText;
    export let tokenIcon;
    export let tokenTypeClass;
    export let onSelectCluster = () => {};
    export let onHoverTokens = () => {};
    export let onClearHover = () => {};

    function signaturePreview(cluster, maxIcons = 8) {
        const sig = cluster?.signature_tokens ?? [];
        const tokens = sig.slice(0, maxIcons);
        const more = Math.max(0, sig.length - tokens.length);
        return { tokens, more };
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
</script>

<div class="cluster-list">
    {#each clusters as c (c.cluster_id)}
        {@const preview = signaturePreview(c)}
        <button
            class="cluster"
            class:selected={c.cluster_id === selectedClusterId}
            on:click={() => onSelectCluster(c)}
            on:mouseenter={() => onHoverTokens(c.signature_tokens ?? [])}
            on:mouseleave={onClearHover}
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

<style>
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

    .delta.pos {
        color: var(--success);
    }
    .delta.neg {
        color: var(--error);
    }

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
        font-feature-settings: 'tnum' 1;
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

    .sig-icon.unit {
        box-shadow: inset 0 0 0 1px rgba(255, 107, 157, 0.18);
    }
    .sig-icon.item {
        box-shadow: inset 0 0 0 1px rgba(0, 217, 255, 0.18);
    }
    .sig-icon.trait {
        box-shadow: inset 0 0 0 1px rgba(168, 85, 247, 0.18);
    }

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

    .sig-fallback.unit::after {
        background: rgba(255, 107, 157, 0.45);
    }
    .sig-fallback.item::after {
        background: rgba(0, 217, 255, 0.45);
    }
    .sig-fallback.trait::after {
        background: rgba(168, 85, 247, 0.45);
    }

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
</style>
