<script>
    import { createEventDispatcher } from 'svelte';

    const dispatch = createEventDispatcher();

    export let page = 'home';

    function openWalkthrough() {
        dispatch('openWalkthrough');
    }

    function navigate(path) {
        dispatch('navigate', { path });
    }
</script>

<div class="header-inline">
    <div class="logo">
        <span class="logo-text">smeecher</span>
        <a href="https://www.pramit.gg" target="_blank" rel="noopener" class="by-line">by JoyFired</a>
    </div>

    <div class="actions">
        {#if page === 'changelog'}
            <button class="action-btn" on:click={() => navigate('/')} aria-label="Back to app">
                Back
            </button>
        {:else}
            <button class="action-btn" on:click={() => navigate('/changelog')} aria-label="Open changelog">
                Changelog
            </button>
            <button class="action-btn" on:click={openWalkthrough} aria-label="Open walkthrough">
                ?
            </button>
        {/if}
    </div>
</div>

<style>
    .header-inline {
        display: flex;
        align-items: center;
        gap: 12px;
        flex-shrink: 0;
    }

    .logo {
        display: flex;
        align-items: baseline;
        gap: 8px;
    }

    .logo-text {
        font-size: 20px;
        font-weight: 800;
        letter-spacing: -0.03em;
        color: var(--text-primary);
        text-transform: lowercase;
        line-height: 1;
    }

    .by-line {
        font-size: 11px;
        font-style: italic;
        color: var(--text-tertiary);
        text-decoration: none;
        opacity: 0.6;
        transition: opacity 0.2s ease, color 0.2s ease;
        white-space: nowrap;
    }

    .by-line:hover {
        opacity: 1;
        color: var(--text-secondary);
    }

    .actions {
        display: flex;
        align-items: center;
        gap: 4px;
    }

    .action-btn {
        padding: 4px 8px;
        font-size: 10px;
        font-weight: 700;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        background: transparent;
        border: 1px solid var(--border);
        border-radius: 6px;
        color: var(--text-tertiary);
        cursor: pointer;
        transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
        font-family: inherit;
    }

    .action-btn:hover {
        background: rgba(255, 255, 255, 0.05);
        border-color: var(--border-hover);
        color: var(--text-primary);
    }

    @media (max-width: 768px) {
        .header-inline {
            width: 100%;
            justify-content: space-between;
        }

        .logo-text {
            font-size: 18px;
        }

        .by-line {
            display: none;
        }
    }
</style>
