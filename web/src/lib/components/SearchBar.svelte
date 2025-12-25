<script>
    import { searchTokens } from '../api.js';
    import { addToken } from '../stores/state.js';
    import { displayNameIndex, getDisplayName } from '../stores/assets.js';
    import { get } from 'svelte/store';
    import posthog from '../client/posthog';
    import VoiceInput from './VoiceInput.svelte';

    let query = '';
    let results = [];
    let showResults = false;
    let selectedIndex = -1;
    let searchTimeout;

    async function handleInput() {
        clearTimeout(searchTimeout);
        selectedIndex = -1;

        if (query.length < 2) {
            results = [];
            showResults = false;
            return;
        }

        searchTimeout = setTimeout(async () => {
            try {
                const backendResults = await searchTokens(query);

                // Also search local display name index
                const queryLower = query.toLowerCase();
                const localMatches = [];
                const seenTokens = new Set(backendResults.map(r => r.token));
                const index = get(displayNameIndex);

                for (const [displayNameLower, info] of index) {
                    if (displayNameLower.includes(queryLower)) {
                        const prefix = info.type === 'item' ? 'I:' : 'T:';
                        const token = prefix + info.apiName;

                        if (!seenTokens.has(token)) {
                            localMatches.push({
                                token,
                                label: info.apiName,
                                type: info.type,
                                count: 0
                            });
                            seenTokens.add(token);
                        }
                    }
                }

                results = [...backendResults, ...localMatches];
                showResults = results.length > 0;
                
                if (showResults) {
                    selectedIndex = 0;
                }

                posthog.capture('search', { query, result_count: results.length });
            } catch (error) {
                console.error('Search error:', error);
            }
        }, 150);
    }

    function handleKeydown(event) {
        if (!showResults || results.length === 0) return;

        if (event.key === 'ArrowDown') {
            event.preventDefault();
            selectedIndex = (selectedIndex + 1) % results.length;
            scrollIntoView(selectedIndex);
        } else if (event.key === 'ArrowUp') {
            event.preventDefault();
            selectedIndex = (selectedIndex - 1 + results.length) % results.length;
            scrollIntoView(selectedIndex);
        } else if (event.key === 'Enter' && selectedIndex >= 0) {
            event.preventDefault();
            handleSelect(results[selectedIndex].token);
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
        addToken(token);
        posthog.capture('search_result_selected', { token, source: 'search' });
        query = '';
        results = [];
        showResults = false;
        selectedIndex = -1;
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
</script>

<svelte:document on:click={handleClickOutside} />

<div class="search-wrapper">
    <div class="search-container">
        <input
            type="text"
            bind:value={query}
            on:input={handleInput}
            on:focus={handleFocus}
            on:keydown={handleKeydown}
            placeholder="Search units, items, traits..."
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
