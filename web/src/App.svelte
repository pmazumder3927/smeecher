<script>
    import { onMount } from 'svelte';
    import Header from './lib/components/Header.svelte';
    import ControlPanel from './lib/components/ControlPanel.svelte';
    import ClusterExplorer from './lib/components/ClusterExplorer.svelte';
    import ItemExplorer from './lib/components/ItemExplorer.svelte';
    import Graph from './lib/components/Graph.svelte';
    import Legend from './lib/components/Legend.svelte';
    import Tooltip from './lib/components/Tooltip.svelte';
    import Walkthrough from './lib/components/Walkthrough.svelte';
    import ChangelogPage from './lib/components/ChangelogPage.svelte';

    import { loadCDragonData } from './lib/stores/assets.js';
    import { selectedTokens, topK, graphData, activeTypes, sortMode, itemTypeFilters, itemPrefixFilters } from './lib/stores/state.js';
    import { fetchGraphData } from './lib/api.js';

    let ready = false;
    let walkthroughOpen = false;
    const WALKTHROUGH_KEY = 'smeecher_walkthrough_seen';

    let fetchTimer = null;
    let fetchVersion = 0;

    // Fetch graph when tokens, topK, activeTypes, or sortMode change (debounced).
    let page = 'home';
    let homeInitPromise = null;
    let mounted = false;

    $: if (ready && page === 'home') scheduleFetchGraph($selectedTokens, $topK, $activeTypes, $sortMode, $itemTypeFilters, $itemPrefixFilters);
    $: if (mounted && page === 'home') initHomeIfNeeded();

    function normalizePathname(pathname) {
        const p = String(pathname || '/').replace(/\/+$/, '');
        return p === '' ? '/' : p;
    }

    function pageForPath(pathname) {
        const p = normalizePathname(pathname);
        if (p === '/changelog') return 'changelog';
        return 'home';
    }

    function syncPageFromLocation() {
        if (typeof window === 'undefined') return;
        page = pageForPath(window.location.pathname);
    }

    function navigateTo(path) {
        if (typeof window === 'undefined') return;
        const nextPath = normalizePathname(path);
        const current = normalizePathname(window.location.pathname);
        if (nextPath === current) return;
        window.history.pushState({}, '', nextPath);
        syncPageFromLocation();
    }

    function handleNavigate(event) {
        const path = event?.detail?.path;
        if (typeof path !== 'string') return;
        navigateTo(path);
    }

    async function initHomeIfNeeded() {
        if (ready) return;
        if (homeInitPromise) return homeInitPromise;

        homeInitPromise = (async () => {
            await loadCDragonData();
            ready = true;
            const version = ++fetchVersion;
            clearTimeout(fetchTimer);
            await fetchGraph($selectedTokens, $topK, $activeTypes, $sortMode, $itemTypeFilters, $itemPrefixFilters, version);

            try {
                const seen = localStorage.getItem(WALKTHROUGH_KEY) === '1';
                if (!seen) walkthroughOpen = true;
            } catch {
                // ignore
            }
        })().finally(() => {
            homeInitPromise = null;
        });

        return homeInitPromise;
    }

    function scheduleFetchGraph(tokens, k, types, mode, itemTypes, itemPrefixes) {
        clearTimeout(fetchTimer);

        const version = ++fetchVersion;
        const tokensSnapshot = [...tokens];
        const typesSnapshot = new Set(types);
        const modeSnapshot = mode;
        const kSnapshot = k;
        const itemTypesSnapshot = new Set(itemTypes);
        const itemPrefixesSnapshot = new Set(itemPrefixes);

        fetchTimer = setTimeout(() => {
            fetchGraph(tokensSnapshot, kSnapshot, typesSnapshot, modeSnapshot, itemTypesSnapshot, itemPrefixesSnapshot, version);
        }, 150);
    }

    async function fetchGraph(tokens, k, types, mode, itemTypes, itemPrefixes, version = fetchVersion) {
        try {
            const data = await fetchGraphData(tokens, k, types, mode, { itemTypes, itemPrefixes });
            if (version !== fetchVersion) return; // Ignore stale responses
            graphData.set(data);
        } catch (error) {
            console.error('Failed to fetch graph:', error);
        }
    }

    onMount(async () => {
        syncPageFromLocation();
        mounted = true;
        const onPopState = () => syncPageFromLocation();
        window.addEventListener('popstate', onPopState);

        if (page === 'home') {
            await initHomeIfNeeded();
        }

        return () => {
            window.removeEventListener('popstate', onPopState);
        };
    });

    function handleWalkthroughClose(event) {
        walkthroughOpen = false;
        if (!event?.detail?.markSeen) return;
        try {
            localStorage.setItem(WALKTHROUGH_KEY, '1');
        } catch {
            // ignore
        }
    }
</script>

<div class="container">
    <Header {page} on:navigate={handleNavigate} on:openWalkthrough={() => walkthroughOpen = true} />

    {#if page === 'home'}
        <ControlPanel />

        <div class="main-row">
            <ItemExplorer />
            <Graph />
            <ClusterExplorer />
        </div>
        <Legend />
    {:else if page === 'changelog'}
        <ChangelogPage />
    {/if}
</div>

<Tooltip />
<Walkthrough open={walkthroughOpen} on:close={handleWalkthroughClose} />

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
