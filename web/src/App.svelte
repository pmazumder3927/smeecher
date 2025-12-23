<script>
    import { onMount } from 'svelte';
    import Header from './lib/components/Header.svelte';
    import SearchBar from './lib/components/SearchBar.svelte';
    import FilterToggles from './lib/components/FilterToggles.svelte';
    import Stats from './lib/components/Stats.svelte';
    import Chips from './lib/components/Chips.svelte';
    import TopKInput from './lib/components/TopKInput.svelte';
    import SortModeSelector from './lib/components/SortModeSelector.svelte';
    import ClusterExplorer from './lib/components/ClusterExplorer.svelte';
    import Graph from './lib/components/Graph.svelte';
    import Legend from './lib/components/Legend.svelte';
    import Tooltip from './lib/components/Tooltip.svelte';

    import { loadCDragonData, assetsLoaded } from './lib/stores/assets.js';
    import { selectedTokens, topK, graphData, activeTypes, sortMode } from './lib/stores/state.js';
    import { fetchGraphData } from './lib/api.js';

    let ready = false;

    // Fetch graph when tokens, topK, activeTypes, or sortMode change (but only after assets loaded)
    $: if (ready) fetchGraph($selectedTokens, $topK, $activeTypes, $sortMode);

    async function fetchGraph(tokens, k, types, mode) {
        try {
            const data = await fetchGraphData(tokens, k, types, mode);
            graphData.set(data);
        } catch (error) {
            console.error('Failed to fetch graph:', error);
        }
    }

    onMount(async () => {
        await loadCDragonData();
        ready = true;
        await fetchGraph($selectedTokens, $topK, $activeTypes, $sortMode);
    });
</script>

<div class="container">
    <Header />

    <div class="control-panel">
        <div class="control-panel-top">
            <SearchBar />
            <FilterToggles />
            <Stats />
        </div>
        <div class="control-panel-bottom">
            <Chips />
            <SortModeSelector />
            <TopKInput on:change={() => fetchGraph($selectedTokens, $topK, $activeTypes, $sortMode)} />
        </div>
    </div>

    <div class="main-row">
        <Graph />
        <ClusterExplorer />
    </div>
    <Legend />
</div>

<Tooltip />

<style>
    .container {
        max-width: 1400px;
        margin: 0 auto;
        padding: 32px 40px 24px;
        height: 100%;
        display: flex;
        flex-direction: column;
    }

    .control-panel {
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 10px;
        padding: 14px 18px;
        margin-bottom: 16px;
        flex-shrink: 0;
        display: flex;
        flex-direction: column;
        gap: 12px;
    }

    .control-panel-top {
        display: flex;
        align-items: center;
        gap: 16px;
    }

    .control-panel-bottom {
        display: flex;
        align-items: center;
        gap: 16px;
        min-height: 28px;
    }

    .main-row {
        flex: 1;
        min-height: 0;
        display: flex;
        gap: 12px;
        margin-bottom: 16px;
    }

    @media (max-width: 768px) {
        .container {
            padding: 20px 16px 16px;
        }

        .control-panel {
            padding: 12px 14px;
        }

        .control-panel-top {
            flex-wrap: wrap;
            gap: 12px;
        }

        .main-row {
            flex-direction: column;
            gap: 12px;
        }
    }
</style>