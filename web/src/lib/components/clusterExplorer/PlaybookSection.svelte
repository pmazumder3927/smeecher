<script>
    import { getTokenType } from '../../utils/tokens.js';
    import { getIconUrl, hasIconFailed, markIconFailed } from '../../stores/assets.js';
    import { deltaClass, fmtPct, fmtSignedPct } from './formatting.js';

    export let sectionEl;
    export let clusterId = null;
    export let playbook = null;
    export let playbookLoading = false;
    export let playbookError = null;
    export let stale = false;
    export let tokenText;
    export let tokenIcon;
    export let tokenTypeClass;
    export let onCompute = () => {};
    export let onAddToken = () => {};
    export let onExcludeToken = () => {};
    export let onHoverTokens = () => {};
    export let onClearHover = () => {};

    $: drivers = playbook?.drivers ?? [];
    $: killers = playbook?.killers ?? [];
</script>

<div class="playbook" bind:this={sectionEl}>
    <div class="playbook-header">
        <div class="playbook-title">Playbook</div>
        <button class="playbook-run" disabled={playbookLoading || stale} on:click={() => onCompute(clusterId)}>
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
            Δs compare games <span class="mono">within this cluster</span> (with token vs without). Baseline 8th:
            {fmtPct(playbook.base.eighth_rate)}.
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
                            <button
                                class="pb-main"
                                on:click={() => onAddToken(row.token)}
                                on:mouseenter={() => onHoverTokens([row.token])}
                                on:mouseleave={onClearHover}
                            >
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
                            <button class="pb-action" title="Exclude" on:click|stopPropagation={() => onExcludeToken(row.token)}>
                                −
                            </button>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>

        <div class="pb-section">
            <div class="pb-title">Biggest griefers</div>
            {#if killers.length === 0}
                <div class="pb-empty">
                    Not enough data to rank griefers (try broadening filters).
                </div>
            {:else}
                <div class="pb-list">
                    {#each killers as row, idx}
                        <div class="pb-row">
                            <button
                                class="pb-main"
                                on:click={() => onAddToken(row.token)}
                                on:mouseenter={() => onHoverTokens([row.token])}
                                on:mouseleave={onClearHover}
                            >
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
                            <button class="pb-action" title="Exclude" on:click|stopPropagation={() => onExcludeToken(row.token)}>
                                −
                            </button>
                        </div>
                    {/each}
                </div>
            {/if}
        </div>
    {/if}
</div>

<style>
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
        font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, 'Liberation Mono', 'Courier New', monospace;
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
        0%,
        100% {
            opacity: 0.65;
        }
        50% {
            opacity: 1;
        }
    }
</style>
