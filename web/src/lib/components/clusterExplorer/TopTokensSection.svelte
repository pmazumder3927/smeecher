<script>
    import { getTokenType } from '../../utils/tokens.js';
    import { hasIconFailed, markIconFailed } from '../../stores/assets.js';
    import { fmtLift, fmtPct } from './formatting.js';

    export let sectionEl;
    export let unifiedTokens = [];
    export let tokenText;
    export let tokenIcon;
    export let onAddToken = () => {};
    export let onHoverTokens = () => {};
    export let onClearHover = () => {};
</script>

<div class="unified-tokens" bind:this={sectionEl}>
    <div class="tokens-header">
        <span class="tokens-title">Top Tokens</span>
        <span class="tokens-count">{unifiedTokens.length} tokens</span>
    </div>
    <div class="tokens-list">
        {#each unifiedTokens as tok}
            <button
                class="token-row {tok.type}"
                on:click={() => onAddToken(tok.token)}
                on:mouseenter={() => onHoverTokens([tok.token])}
                on:mouseleave={onClearHover}
            >
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

<style>
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

    .token-fallback.unit::after {
        background: rgba(255, 107, 157, 0.5);
    }
    .token-fallback.trait::after {
        background: rgba(168, 85, 247, 0.5);
    }
    .token-fallback.item::after {
        background: rgba(0, 217, 255, 0.5);
    }

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

    @media (max-width: 768px) {
        .tokens-list {
            max-height: 50vh;
        }
    }
</style>
