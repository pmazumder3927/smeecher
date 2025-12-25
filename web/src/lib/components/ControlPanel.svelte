<script>
    import SearchBar from './SearchBar.svelte';
    import FilterToggles from './FilterToggles.svelte';
    import Stats from './Stats.svelte';
    import Chips from './Chips.svelte';
    import TopKInput from './TopKInput.svelte';
    import SortModeSelector from './SortModeSelector.svelte';
</script>

<div class="control-panel">
    <div class="primary-row">
        <div class="search-section">
            <SearchBar />
            <div class="toggles-wrapper">
                <FilterToggles />
            </div>
        </div>
        <div class="stats-wrapper">
            <Stats />
        </div>
    </div>

    <div class="secondary-row">
        <div class="chips-container">
            <Chips />
        </div>
        <div class="controls-container">
            <SortModeSelector />
            <div class="separator"></div>
            <TopKInput />
        </div>
    </div>
</div>

<style>
    .control-panel {
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 12px;
        padding: 0;
        display: flex;
        flex-direction: column;
        box-shadow: 0 4px 12px rgba(0,0,0,0.1);
        /* Allow search results and other dropdowns to overflow */
    }

    .primary-row {
        position: relative;
        z-index: 10;
        padding: 16px 20px;
        display: flex;
        gap: 24px;
        align-items: center;
        justify-content: space-between;
        background: var(--bg-secondary);
        border-top-left-radius: 11px;
        border-top-right-radius: 11px;
    }

    .search-section {
        flex: 1;
        display: flex;
        align-items: center;
        gap: 16px;
        min-width: 0;
    }

    /* Make search bar take available space */
    .search-section :global(.search-wrapper) {
        flex: 1;
        max-width: 600px;
    }

    .stats-wrapper {
        flex-shrink: 0;
        padding-left: 24px;
        border-left: 1px solid var(--border);
    }

    .secondary-row {
        position: relative;
        z-index: 5;
        padding: 12px 20px;
        display: flex;
        gap: 24px;
        align-items: flex-start; /* Allow chips to wrap */
        justify-content: space-between;
        background: var(--bg-tertiary);
        border-top: 1px solid var(--border);
        min-height: 52px;
        border-bottom-left-radius: 11px;
        border-bottom-right-radius: 11px;
    }

    .chips-container {
        flex: 1;
        display: flex;
        align-items: center;
        min-width: 0;
    }

    .controls-container {
        display: flex;
        align-items: center;
        gap: 16px;
        flex-shrink: 0;
    }

    .separator {
        width: 1px;
        height: 16px;
        background: var(--border);
    }

    @media (max-width: 1024px) {
        .primary-row {
            flex-direction: column;
            align-items: stretch;
            gap: 16px;
        }

        .stats-wrapper {
            padding-left: 0;
            border-left: none;
            justify-content: flex-end;
            display: flex;
        }

        .search-section :global(.search-wrapper) {
            max-width: none;
        }
    }

    @media (max-width: 768px) {
        .control-panel {
            border-radius: 8px;
        }

        .primary-row {
            border-top-left-radius: 7px;
            border-top-right-radius: 7px;
        }

        .secondary-row {
            border-bottom-left-radius: 7px;
            border-bottom-right-radius: 7px;
        }

        .search-section {
            flex-direction: column;
            align-items: stretch;
            gap: 12px;
        }

        .secondary-row {
            flex-direction: column;
            gap: 16px;
            align-items: stretch;
        }

        .controls-container {
            justify-content: space-between;
            width: 100%;
        }
    }
</style>

