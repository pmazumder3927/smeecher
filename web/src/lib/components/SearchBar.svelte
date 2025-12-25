<script>
    import { onMount } from 'svelte';
    import { fetchSearchIndex, searchTokens } from '../api.js';
    import { addToken, addTokens } from '../stores/state.js';
    import { getDisplayName } from '../stores/assets.js';
    import posthog from '../client/posthog';
    import VoiceInput from './VoiceInput.svelte';

    let query = '';
    let results = [];
    let showResults = false;
    let selectedIndex = -1;
    let analyticsTimeout;

    let searchIndex = [];
    let searchReady = false;
    let exactLookup = new Map();
    let tokenLookup = new Map();

    const MAX_SPAN_WORDS = 4;
    const STOP_WORDS = new Set(['and', 'or', 'with', 'plus', 'then', 'etc', '&']);

    function normalizeSearchText(text) {
        return text.toLowerCase().replace(/[^a-z0-9]/g, '');
    }

    function getActiveSegment(text) {
        const parts = text.split(/[\s,;]+/);
        return parts[parts.length - 1] || '';
    }

    function scoreMatch(entry, qNorm) {
        if (entry.labelNorm === qNorm || entry.tokenSuffixNorm === qNorm) return 0;
        if (entry.labelNorm.startsWith(qNorm) || entry.tokenSuffixNorm.startsWith(qNorm)) return 1;
        return 2;
    }

    function searchLocal(segment) {
        const qNorm = normalizeSearchText(segment);
        if (!qNorm) return [];

        const matches = [];
        for (const entry of searchIndex) {
            if (entry.labelNorm.includes(qNorm) || entry.tokenSuffixNorm.includes(qNorm)) {
                matches.push(entry);
            }
        }

        matches.sort((a, b) => {
            const sa = scoreMatch(a, qNorm);
            const sb = scoreMatch(b, qNorm);
            if (sa !== sb) return sa - sb;
            if (a.count !== b.count) return b.count - a.count;
            return a.label.length - b.label.length;
        });

        return matches.slice(0, 20).map(({ token, label, type, count }) => ({ token, label, type, count }));
    }

    function resolvePhraseToToken(phrase) {
        const key = normalizeSearchText(phrase);
        if (!key) return null;

        const exact = exactLookup.get(key);
        if (exact) return exact;

        // Unique prefix match (lets users type "aat" -> Aatrox, etc.)
        let best = null;
        let bestCount = -1;
        let matches = 0;
        for (const entry of searchIndex) {
            if (entry.labelNorm.startsWith(key) || entry.tokenSuffixNorm.startsWith(key)) {
                matches += 1;
                if (entry.count > bestCount) {
                    best = entry.token;
                    bestCount = entry.count;
                }
                if (matches > 1) {
                    // Ambiguous: force the user to type more or pick from suggestions
                    return null;
                }
            }
        }
        return best;
    }

    function parseTokensFromText(text) {
        const words = text
            .split(/[\s,;]+/)
            .map((w) => w.trim())
            .filter(Boolean);

        const tokens = [];
        let i = 0;
        while (i < words.length) {
            if (STOP_WORDS.has(words[i].toLowerCase())) {
                i += 1;
                continue;
            }

            let matchedToken = null;
            let matchedSpan = 0;
            for (let span = Math.min(MAX_SPAN_WORDS, words.length - i); span >= 1; span -= 1) {
                const phrase = words.slice(i, i + span).join(' ');
                const token = resolvePhraseToToken(phrase);
                if (token) {
                    matchedToken = token;
                    matchedSpan = span;
                    break;
                }
            }

            if (!matchedToken) break;

            tokens.push(matchedToken);
            i += matchedSpan;
        }

        const leftover = words.slice(i).join(' ');
        return { tokens, leftover };
    }

    function trackSearchOnce(resultCount) {
        clearTimeout(analyticsTimeout);
        analyticsTimeout = setTimeout(() => {
            posthog.capture('search', { query, result_count: resultCount, mode: searchReady ? 'local' : 'backend' });
        }, 250);
    }

    function clearResults() {
        results = [];
        showResults = false;
        selectedIndex = -1;
    }

    function clearInput() {
        query = '';
        clearResults();
    }

    function addMany(tokens, source) {
        const unique = Array.from(new Set(tokens || [])).filter(Boolean);
        if (unique.length === 0) return;
        if (unique.length === 1) {
            addToken(unique[0], source);
            return;
        }
        addTokens(unique, source);
        posthog.capture('search_bulk_add', { tokens_added: unique.length, tokens: unique });
    }

    async function handleInput() {
        selectedIndex = -1;

        const segment = getActiveSegment(query);
        if (!segment || segment.length < 1) {
            clearResults();
            return;
        }

        try {
            if (searchReady) {
                results = searchLocal(segment);
                showResults = results.length > 0;
                if (showResults) selectedIndex = 0;
                trackSearchOnce(results.length);
                return;
            }

            // Fallback: backend search (only used if the index fails to load)
            if (segment.length < 2) {
                clearResults();
                return;
            }

            const backendResults = await searchTokens(segment);
            results = backendResults;
            showResults = results.length > 0;
            if (showResults) selectedIndex = 0;
            trackSearchOnce(results.length);
        } catch (error) {
            console.error('Search error:', error);
        }
    }

    function handleKeydown(event) {
        if (event.key === 'Enter') {
            const text = query.trim();
            if (!text) return;

            const segment = getActiveSegment(query);

            // If the user is just picking a single suggestion, preserve existing behavior
            const looksLikeSingle = !/[\s,;]+/.test(text);
            if (looksLikeSingle && showResults && results.length > 0 && selectedIndex >= 0) {
                event.preventDefault();
                handleSelect(results[selectedIndex].token);
                return;
            }

            if (!searchReady) return;

            const { tokens, leftover } = parseTokensFromText(text);

            // If we couldn't fully resolve the tail, let Enter choose the highlighted suggestion
            let selectedToken = null;
            let nextQuery = leftover;
            if (leftover && showResults && results.length > 0 && selectedIndex >= 0) {
                const leftoverWords = leftover.split(/[\s,;]+/).filter(Boolean);
                const lastLeftover = leftoverWords[leftoverWords.length - 1] || '';
                if (lastLeftover === segment) {
                    const selected = results[selectedIndex].token;
                    const selectedEntry = tokenLookup.get(selected);
                    const leftoverNorm = normalizeSearchText(leftover);

                    selectedToken = selected;
                    if (selectedEntry && leftoverNorm && (selectedEntry.labelNorm.startsWith(leftoverNorm) || selectedEntry.tokenSuffixNorm.startsWith(leftoverNorm))) {
                        nextQuery = '';
                    } else {
                        leftoverWords.pop();
                        nextQuery = leftoverWords.join(' ');
                    }
                }
            }

            const toAdd = selectedToken ? [...tokens, selectedToken] : tokens;
            if (toAdd.length === 0) return;

            event.preventDefault();
            addMany(toAdd, 'search');
            query = nextQuery;
            clearResults();
            return;
        }

        if (!showResults || results.length === 0) return;

        if (event.key === 'ArrowDown') {
            event.preventDefault();
            selectedIndex = (selectedIndex + 1) % results.length;
            scrollIntoView(selectedIndex);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            selectedIndex = (selectedIndex - 1 + results.length) % results.length;
            scrollIntoView(selectedIndex);
        } else if (event.key === 'Escape') {
            showResults = false;
        }
    }

    function scrollIntoView(index) {
        const el = document.getElementById(`result-${index}`);
        if (el) {
            el.scrollIntoView({ block: 'nearest', behavior: 'smooth' });
        }
    }

    function handleSelect(token) {
        // If the user typed multiple words (e.g. "diana aat") and clicks a suggestion,
        // resolve and add everything we can in one shot.
        if (searchReady) {
            const words = query.trim().split(/[\s,;]+/).filter(Boolean);
            const prefix = words.length > 1 ? words.slice(0, -1).join(' ') : '';
            const prefixTokens = prefix ? parseTokensFromText(prefix).tokens : [];
            addMany([...prefixTokens, token], 'search');
        } else {
            addToken(token, 'search');
        }

        posthog.capture('search_result_selected', { token, source: 'search' });
        clearInput();
    }

    function handleFocus() {
        if (results.length > 0) {
            showResults = true;
        }
    }

    function handleClickOutside(event) {
        if (!event.target.closest('.search-container')) {
            showResults = false;
        }
    }

    function getTypeClass(type) {
        return type === 'unit' ? 'unit' :
               type === 'item' ? 'item' :
               type === 'trait' ? 'trait' : 'item';
    }

    onMount(async () => {
        try {
            const index = await fetchSearchIndex();
            searchIndex = index.map((e) => ({
                ...e,
                labelNorm: normalizeSearchText(e.label),
                tokenSuffixNorm: normalizeSearchText(e.token.slice(2)),
                count: e.count || 0,
            }));
            exactLookup = new Map();
            tokenLookup = new Map();
            for (const entry of searchIndex) {
                if (entry.labelNorm && !exactLookup.has(entry.labelNorm)) exactLookup.set(entry.labelNorm, entry.token);
                if (entry.tokenSuffixNorm && !exactLookup.has(entry.tokenSuffixNorm)) exactLookup.set(entry.tokenSuffixNorm, entry.token);
                if (entry.token && !tokenLookup.has(entry.token)) tokenLookup.set(entry.token, entry);
            }
            searchReady = true;
        } catch (e) {
            console.warn('Failed to load search index, falling back to backend search:', e);
            searchReady = false;
        }
    });
</script>

<svelte:document on:click={handleClickOutside} />

<div class="search-wrapper">
    <div class="search-container" data-walkthrough="search">
        <input
            type="text"
            bind:value={query}
            on:input={handleInput}
            on:focus={handleFocus}
            on:keydown={handleKeydown}
            placeholder="Search units, items, traits… (Enter adds all)"
            autocomplete="off"
        />

    {#if showResults}
        <div class="search-results">
            {#each results as result, i}
                <button
                    id="result-{i}"
                    class="search-result"
                    class:selected={i === selectedIndex}
                    on:click={() => handleSelect(result.token)}
                    on:mouseenter={() => selectedIndex = i}
                >
                    <span class="label">{getDisplayName(result.type, result.label)}</span>
                    <span class="meta">
                        <span class="type-badge {getTypeClass(result.type)}">{result.type}</span>
                        {#if result.count > 0}
                            <span>{result.count.toLocaleString()} games</span>
                        {/if}
                    </span>
                </button>
            {:else}
                <div class="search-result no-results">
                    <span class="label">No results found</span>
                </div>
            {/each}
        </div>
    {/if}
    </div>
    <VoiceInput />
</div>

<style>
    .search-wrapper {
        display: flex;
        gap: 8px;
        align-items: center;
        flex: 1;
        min-width: 0;
    }

    .search-container {
        position: relative;
        flex: 1;
        min-width: 0;
    }

    input {
        width: 100%;
        padding: 8px 12px;
        font-size: 13px;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        border-radius: 6px;
        color: var(--text-primary);
        outline: none;
        transition: all 0.2s ease;
        font-family: inherit;
    }

    input::placeholder {
        color: var(--text-tertiary);
    }

    input:focus {
        border-color: var(--accent);
        background: var(--bg-tertiary);
        box-shadow: 0 4px 16px rgba(0, 112, 243, 0.15);
    }

    .search-results {
        position: absolute;
        top: calc(100% + 4px);
        left: 0;
        right: 0;
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 8px;
        max-height: 320px;
        overflow-y: auto;
        z-index: 100;
        box-shadow: 0 8px 32px rgba(0, 0, 0, 0.4);
    }

    .search-result {
        width: 100%;
        padding: 12px 16px;
        cursor: pointer;
        display: flex;
        justify-content: space-between;
        align-items: center;
        transition: background 0.15s ease;
        border: none;
        border-bottom: 1px solid var(--border);
        background: transparent;
        text-align: left;
        font-family: inherit;
    }

    .search-result:last-child {
        border-bottom: none;
    }

    .search-result:hover:not(.no-results),
    .search-result.selected:not(.no-results) {
        background: var(--bg-tertiary);
    }

    .search-result.no-results {
        cursor: default;
    }

    .label {
        font-weight: 500;
        font-size: 14px;
        color: var(--text-primary);
    }

    .meta {
        font-size: 12px;
        color: var(--text-secondary);
        display: flex;
        gap: 8px;
        align-items: center;
    }

    .type-badge {
        padding: 2px 6px;
        border-radius: 4px;
        font-size: 10px;
        font-weight: 600;
        text-transform: uppercase;
        letter-spacing: 0.05em;
    }

    .type-badge.unit {
        background: rgba(255, 107, 157, 0.15);
        color: var(--unit);
    }

    .type-badge.item {
        background: rgba(0, 217, 255, 0.15);
        color: var(--item);
    }

    .type-badge.trait {
        background: rgba(168, 85, 247, 0.15);
        color: var(--trait);
    }

    .search-results::-webkit-scrollbar {
        width: 8px;
    }

    .search-results::-webkit-scrollbar-track {
        background: var(--bg-primary);
    }

    .search-results::-webkit-scrollbar-thumb {
        background: var(--border);
        border-radius: 4px;
    }

    @media (max-width: 768px) {
        .search-wrapper {
            flex: 1 1 100%;
            order: 1;
        }

        input {
            padding: 10px 14px;
            font-size: 14px;
        }
    }
</style>
