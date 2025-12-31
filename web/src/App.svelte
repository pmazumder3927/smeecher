<script>
    import { onMount } from 'svelte';
    import ClusterExplorer from './lib/components/ClusterExplorer.svelte';
    import ItemExplorer from './lib/components/ItemExplorer.svelte';
    import Graph from './lib/components/Graph.svelte';
    import Legend from './lib/components/Legend.svelte';
    import Tooltip from './lib/components/Tooltip.svelte';
    import Walkthrough from './lib/components/Walkthrough.svelte';
    import ChangelogPage from './lib/components/ChangelogPage.svelte';
    import Header from './lib/components/Header.svelte';

    import { loadCDragonData } from './lib/stores/assets.js';
    import { selectedTokens, topK, graphData, activeTypes, sortMode, itemTypeFilters, itemPrefixFilters } from './lib/stores/state.js';
    import { fetchGraphData } from './lib/api.js';
    import { hydrateStateFromLocation, hydrateStateFromUrlOnly, startStateSync } from './lib/utils/urlState.js';

    let ready = false;
    let walkthroughOpen = false;
    const WALKTHROUGH_KEY = 'smeecher_walkthrough_seen';

    let fetchTimer = null;
    let fetchVersion = 0;
    let stopStateSync = null;

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
        // Preserve current query string so the user can bounce between pages
        // without losing their current filter state permalink.
        window.history.pushState({}, '', `${nextPath}${window.location.search || ''}`);
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
        }, $graphData ? 150 : 0);
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
        hydrateStateFromLocation();
        stopStateSync = startStateSync();

        const onPopState = () => {
            syncPageFromLocation();
            if (stopStateSync?.suspend) {
                stopStateSync.suspend(() => hydrateStateFromUrlOnly());
            } else {
                hydrateStateFromUrlOnly();
            }
        };
        window.addEventListener('popstate', onPopState);

        if (page === 'home') {
            await initHomeIfNeeded();
        }

        return () => {
            window.removeEventListener('popstate', onPopState);
            stopStateSync?.();
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

<div class="app-shell">
    <Header {page} on:navigate={handleNavigate} on:openWalkthrough={() => walkthroughOpen = true} />

    {#if page === 'home'}
        <main class="workspace">
            <ItemExplorer />
            <div class="graph-area">
                <Graph />
                <Legend />
            </div>
            <ClusterExplorer />
        </main>
    {:else if page === 'changelog'}
        <main class="page-container">
            <ChangelogPage />
        </main>
    {/if}
</div>

<Tooltip />
<Walkthrough open={walkthroughOpen} on:close={handleWalkthroughClose} />

<style>
    .app-shell {
        height: 100%;
        display: flex;
        flex-direction: column;
        overflow: hidden;
    }

    .workspace {
        flex: 1;
        min-height: 0;
        display: flex;
        gap: 0;
    }

    .graph-area {
        flex: 1;
        min-width: 0;
        display: flex;
        flex-direction: column;
        position: relative;
    }

    .page-container {
        max-width: 900px;
        margin: 0 auto;
        padding: 32px 24px;
        display: flex;
        flex-direction: column;
        flex: 1;
        min-height: 0;
        overflow: auto;
    }

    @media (max-width: 900px) {
        .page-container {
            padding: 24px 18px;
        }
    }

    @media (max-width: 768px) {
        .workspace {
            flex-direction: column;
        }

        .page-container {
            padding: 20px 16px;
        }
    }
</style>
