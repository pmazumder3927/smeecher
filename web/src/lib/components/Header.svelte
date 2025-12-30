<script>
    import { createEventDispatcher } from 'svelte';
    import SearchBar from './SearchBar.svelte';
    import Chips from './Chips.svelte';
    import FilterToggles from './FilterToggles.svelte';
    import ItemFilterMenu from './ItemFilterMenu.svelte';
    import SortModeSelector from './SortModeSelector.svelte';
    import TopKInput from './TopKInput.svelte';
    import Stats from './Stats.svelte';
    import { activeTypes, selectedTokens } from '../stores/state.js';

    const dispatch = createEventDispatcher();

    export let page = 'home';

    $: itemActive = $activeTypes.has('item');

    function openWalkthrough() {
        dispatch('openWalkthrough');
    }

    function navigate(path) {
        dispatch('navigate', { path });
    }
</script>

<header class="app-header" class:home={page === 'home'} class:changelog={page === 'changelog'}>
    <div class="bar-row">
        <div class="brand">
            <button class="brand-btn" type="button" on:click={() => navigate('/')} aria-label="Go to smeecher home">
                smeecher
            </button>
            <a href="https://www.pramit.gg" target="_blank" rel="noopener" class="by-line">by JoyFired</a>
        </div>

        {#if page === 'home'}
            <div class="search-area">
                <SearchBar />
                <div class="stats-pill" aria-label="Stats">
                    <Stats />
                </div>
            </div>

            <div class="controls-wrap" aria-label="Filters and sorting">
                <FilterToggles />
                <div class="item-scope">
                    <ItemFilterMenu compact active={itemActive} />
                </div>
                <SortModeSelector />
                <TopKInput />
            </div>

            <div class="actions-wrap">
                <button type="button" class="icon-btn" on:click={() => navigate('/changelog')} title="Changelog">
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                        <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z" />
                        <path d="M14 2v6h6" />
                    </svg>
                    <span class="sr-only">Changelog</span>
                </button>

                <button type="button" class="icon-btn" on:click={openWalkthrough} title="Help">
                    <span aria-hidden="true">?</span>
                    <span class="sr-only">Help</span>
                </button>
            </div>
        {:else}
            <div class="actions-wrap">
                <button class="text-btn" type="button" on:click={() => navigate('/')} aria-label="Back to app">
                    Back
                </button>
                <button type="button" class="icon-btn" on:click={openWalkthrough} title="Help">
                    <span aria-hidden="true">?</span>
                    <span class="sr-only">Help</span>
                </button>
            </div>
        {/if}
    </div>

    {#if page === 'home' && $selectedTokens.length > 0}
        <div class="chips-row">
            <Chips />
        </div>
    {/if}
</header>

<style>
    .app-header {
        position: relative;
        z-index: 30;
        display: flex;
        flex-direction: column;
        flex-shrink: 0;
        border-bottom: 1px solid var(--border);
        background: rgba(10, 10, 10, 0.9);
        backdrop-filter: blur(14px);
    }

    .bar-row {
        align-items: center;
        display: flex;
        flex-wrap: wrap;
        gap: 10px 12px;
        padding: 9px 14px;
    }

    .brand {
        display: flex;
        align-items: baseline;
        gap: 8px;
        flex-shrink: 0;
    }

    .brand-btn {
        border: none;
        background: transparent;
        padding: 0;
        cursor: pointer;
        font-family: inherit;
        text-transform: lowercase;
        font-size: 18px;
        font-weight: 850;
        letter-spacing: -0.03em;
        color: var(--text-primary);
        line-height: 1;
    }

    .by-line {
        font-size: 11px;
        font-style: italic;
        color: var(--text-tertiary);
        text-decoration: none;
        opacity: 0.65;
        transition: opacity 0.2s ease, color 0.2s ease;
        width: fit-content;
    }

    .by-line:hover {
        opacity: 1;
        color: var(--text-secondary);
    }

    .search-area {
        min-width: 0;
        flex: 1 1 520px;
        display: flex;
        align-items: center;
        gap: 10px;
    }

    .search-area :global(.search-wrapper) {
        flex: 1;
        min-width: 0;
    }

    .controls-wrap {
        display: flex;
        align-items: center;
        gap: 8px;
        flex-shrink: 0;
        flex-wrap: wrap;
    }

    .item-scope {
        display: inline-flex;
        align-items: center;
    }

    .stats-pill {
        height: 36px;
        display: inline-flex;
        align-items: center;
        padding: 0 10px;
        border: 1px solid var(--border);
        border-radius: 10px;
        background: rgba(255, 255, 255, 0.03);
        color: var(--text-primary);
    }

    .actions-wrap {
        display: flex;
        align-items: center;
        gap: 6px;
        margin-left: auto;
        padding-left: 12px;
        border-left: 1px solid var(--border);
    }

    .text-btn {
        display: inline-flex;
        align-items: center;
        gap: 8px;
        height: 36px;
        padding: 0 10px;
        font-size: 11px;
        font-weight: 700;
        letter-spacing: 0.02em;
        background: rgba(255, 255, 255, 0.03);
        border: 1px solid var(--border);
        border-radius: 10px;
        color: var(--text-secondary);
        cursor: pointer;
        transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
        font-family: inherit;
        white-space: nowrap;
        line-height: 1;
    }

    .text-btn:hover {
        background: rgba(255, 255, 255, 0.06);
        border-color: var(--border-hover);
        color: var(--text-primary);
    }

    .icon-btn {
        display: inline-flex;
        align-items: center;
        justify-content: center;
        width: 36px;
        height: 36px;
        background: transparent;
        border: 1px solid var(--border);
        border-radius: 10px;
        color: var(--text-secondary);
        font-size: 12px;
        font-weight: 800;
        cursor: pointer;
        transition: background 0.15s ease, color 0.15s ease, border-color 0.15s ease;
        font-family: inherit;
        line-height: 1;
    }

    .icon-btn:hover {
        background: rgba(255, 255, 255, 0.06);
        border-color: var(--border-hover);
        color: var(--text-primary);
    }

    .sr-only {
        position: absolute;
        width: 1px;
        height: 1px;
        padding: 0;
        margin: -1px;
        overflow: hidden;
        clip: rect(0, 0, 0, 0);
        border: 0;
        white-space: nowrap;
    }

    .chips-row {
        padding: 0 16px 12px;
    }

    @media (max-width: 1024px) {
        .stats-pill {
            display: none;
        }
    }

    @media (max-width: 900px) {
        .bar-row {
            padding: 9px 12px;
            gap: 8px 10px;
        }

        .search-area {
            order: 3;
            flex: 1 1 100%;
            min-width: 0;
        }

        .controls-wrap {
            order: 4;
            width: 100%;
            justify-content: space-between;
            gap: 8px 10px;
        }

        .actions-wrap {
            order: 2;
            padding-left: 0;
            border-left: none;
        }
    }

    @media (max-width: 768px) {
        .by-line {
            display: none;
        }

        .controls-wrap {
            justify-content: flex-start;
        }

        .chips-row {
            padding: 0 12px 12px;
        }
    }
</style>
