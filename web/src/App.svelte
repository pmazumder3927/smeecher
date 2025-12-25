<script>
    import { onMount } from 'svelte';
    import Header from './lib/components/Header.svelte';
    import ControlPanel from './lib/components/ControlPanel.svelte';
    import ClusterExplorer from './lib/components/ClusterExplorer.svelte';
    import ItemExplorer from './lib/components/ItemExplorer.svelte';
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

    <ControlPanel />

    <div class="main-row">
        <ItemExplorer />
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
        gap: 24px;
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
            gap: 16px;
        }

        .main-row {
            flex-direction: column;
            gap: 12px;
        }
    }
</style>