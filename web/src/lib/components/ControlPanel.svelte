<script>
  import SearchBar from "./SearchBar.svelte";
  import FilterToggles from "./FilterToggles.svelte";
  import Stats from "./Stats.svelte";
  import Chips from "./Chips.svelte";
  import TopKInput from "./TopKInput.svelte";
  import SortModeSelector from "./SortModeSelector.svelte";
</script>

<div class="control-panel">
  <div class="search-section">
    <SearchBar />
  </div>
  <div class="chips-section">
    <Chips />
  </div>
  <div class="controls-section">
    <FilterToggles />
    <div class="separator"></div>
    <SortModeSelector />
    <div class="separator"></div>
    <TopKInput />
    <div class="separator"></div>
    <Stats />
  </div>
</div>

<style>
  .control-panel {
    flex: 1;
    display: flex;
    align-items: center;
    gap: 12px;
    min-width: 0;
  }

  .search-section {
    flex: 0 1 320px;
    min-width: 180px;
  }

  .search-section :global(.search-wrapper) {
    width: 100%;
  }

  .chips-section {
    flex: 1;
    min-width: 0;
    display: flex;
    align-items: center;
  }

  .controls-section {
    display: flex;
    align-items: center;
    gap: 10px;
    flex-shrink: 0;
  }

  .separator {
    width: 1px;
    height: 16px;
    background: var(--border);
    opacity: 0.5;
  }

  @media (max-width: 1024px) {
    .control-panel {
      flex-wrap: wrap;
      gap: 8px;
    }

    .search-section {
      flex: 1 1 280px;
    }

    .chips-section {
      flex: 1 1 100%;
      order: 3;
    }
  }

  @media (max-width: 768px) {
    .control-panel {
      flex-direction: column;
      align-items: stretch;
      gap: 10px;
    }

    .search-section {
      flex: none;
      width: 100%;
    }

    .chips-section {
      order: 0;
    }

    .controls-section {
      justify-content: space-between;
      flex-wrap: wrap;
      gap: 8px;
    }

    .separator {
      display: none;
    }
  }
</style>
