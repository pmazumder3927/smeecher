<script>
    import { onMount } from 'svelte';
    import { fetchItemFilters } from '../api.js';
    import {
        itemTypeFilters,
        itemPrefixFilters,
        toggleItemTypeFilter,
        toggleItemPrefixFilter,
        clearItemPrefixFilters,
        resetItemFilters,
        setItemTypeFilters,
        setItemPrefixFilters
    } from '../stores/state.js';

    export let compact = false;
    export let active = false;

    let open = false;
    let loading = false;
    let error = null;
    let options = null;
    let prefixQuery = '';

    $: selectedTypeCount = $itemTypeFilters.size;
    $: selectedPrefixCount = $itemPrefixFilters.size;
    $: hasCustomFilters = selectedTypeCount !== 5 || selectedPrefixCount > 0;
    $: showPrefixSearch = (options?.item_prefixes?.length ?? 0) > 10;
    $: filteredPrefixes = options?.item_prefixes
        ? options.item_prefixes.filter((p) => {
              if (!showPrefixSearch) return true;
              return p.key.toLowerCase().includes(prefixQuery.trim().toLowerCase());
          })
        : [];

    async function load() {
        loading = true;
        error = null;
        try {
            options = await fetchItemFilters();
        } catch (e) {
            error = e?.message ?? String(e);
        } finally {
            loading = false;
        }
    }

    function toggleOpen() {
        open = !open;
        if (open && !options && !loading) load();
    }

    function resetAll() {
        resetItemFilters();
        prefixQuery = '';
    }

    function typeOnly(key) {
        setItemTypeFilters([key]);
    }

    function prefixOnly(key) {
        setItemPrefixFilters([key]);
    }

    function handleDocClick(event) {
        if (!open) return;
        if (event.target.closest('.item-filter-menu')) return;
        open = false;
    }

    function handleKeydown(event) {
        if (!open) return;
        if (event.key === 'Escape') {
            event.preventDefault();
            open = false;
        }
    }

    onMount(() => {
        document.addEventListener('click', handleDocClick);
        document.addEventListener('keydown', handleKeydown);
        return () => {
            document.removeEventListener('click', handleDocClick);
            document.removeEventListener('keydown', handleKeydown);
        };
    });
</script>

<div class="item-filter-menu">
    <button
        class="trigger"
        class:compact
        class:active
        on:click={toggleOpen}
        aria-expanded={open}
        aria-haspopup="dialog"
        aria-label="Item filters"
        title="Item filters"
    >
        {#if compact}
            <svg class="icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <path d="M3 5h18M6 12h12M10 19h4" />
            </svg>
        {:else}
            Item filters
        {/if}

        {#if hasCustomFilters}
            <span class="badge">{selectedPrefixCount > 0 ? selectedPrefixCount : selectedTypeCount}</span>
        {/if}
    </button>

    {#if open}
        <div class="popover" role="dialog" aria-label="Item filters">
            {#if loading}
                <div class="muted">Loading…</div>
            {:else if error}
                <div class="error">{error}</div>
            {:else if options}
                <div class="row header-row">
                    <div class="title">Items</div>
                    <div class="header-actions">
                        <button
                            type="button"
                            class="clear"
                            disabled={$itemPrefixFilters.size === 0}
                            on:click={clearItemPrefixFilters}
                            title="Clear item sets"
                        >
                            Clear sets
                        </button>
                        <button type="button" class="reset" on:click={resetAll}>Reset</button>
                    </div>
                </div>

                <div class="list">
                    {#each options.item_types as t (t.key)}
                        <div class="list-row">
                            <button
                                type="button"
                                class="list-toggle"
                                class:checked={$itemTypeFilters.has(t.key)}
                                on:click={() => toggleItemTypeFilter(t.key)}
                                title={`${t.n_items} items`}
                            >
                                <span class="check" aria-hidden="true">
                                    {$itemTypeFilters.has(t.key) ? '✓' : ''}
                                </span>
                                <span class="label">{t.label}</span>
                                <span class="count">{t.n_items}</span>
                            </button>
                            <button
                                type="button"
                                class="only"
                                on:click={() => typeOnly(t.key)}
                                title={`Only ${t.label}`}
                            >
                                Only
                            </button>
                        </div>
                    {/each}

                    {#if options.item_prefixes.length > 0}
                        <div class="divider">
                            <span class="divider-text">Sets</span>
                        </div>

                        {#if showPrefixSearch}
                            <div class="search">
                                <input
                                    type="text"
                                    placeholder="Search sets…"
                                    bind:value={prefixQuery}
                                    class="search-input"
                                />
                            </div>
                        {/if}

                        <div class="list list-scroll">
                            {#each filteredPrefixes as p (p.key)}
                                <div class="list-row">
                                    <button
                                        type="button"
                                        class="list-toggle"
                                        class:checked={$itemPrefixFilters.has(p.key)}
                                        on:click={() => toggleItemPrefixFilter(p.key)}
                                        title={`${p.n_items} items`}
                                    >
                                        <span class="check" aria-hidden="true">
                                            {$itemPrefixFilters.has(p.key) ? '✓' : ''}
                                        </span>
                                        <span class="label">{p.key}</span>
                                        <span class="count">{p.n_items}</span>
                                    </button>
                                    <button
                                        type="button"
                                        class="only"
                                        on:click={() => prefixOnly(p.key)}
                                        title={`Only ${p.key}`}
                                    >
                                        Only
                                    </button>
                                </div>
                            {/each}
                        </div>
                    {/if}
                </div>
            {/if}
        </div>
    {/if}
</div>

<style>
    .item-filter-menu {
        position: relative;
        display: inline-flex;
        align-items: center;
    }

    .trigger {
        height: 28px;
        padding: 6px 10px;
        border-radius: 5px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        color: var(--text-secondary);
        font-size: 11px;
        font-weight: 600;
        cursor: pointer;
        transition: all 0.2s ease;
        text-transform: uppercase;
        letter-spacing: 0.05em;
        font-family: inherit;
        display: inline-flex;
        gap: 6px;
        align-items: center;
        justify-content: center;
        line-height: 1;
        white-space: nowrap;
    }

    .trigger.compact {
        padding: 4px 6px;
        gap: 3px;
        height: 26px;
        font-size: 10px;
    }

    .trigger.active {
        border-color: transparent;
        background: var(--item);
        color: #000;
    }

    .trigger:hover {
        border-color: var(--border-hover);
        color: var(--text-primary);
    }

    .trigger.active:hover {
        color: #000;
        filter: brightness(1.05);
    }

    .icon {
        width: 14px;
        height: 14px;
        display: block;
    }

    .badge {
        font-size: 9px;
        font-weight: 800;
        padding: 2px 6px;
        border-radius: 999px;
        border: 1px solid rgba(0, 217, 255, 0.35);
        background: rgba(0, 217, 255, 0.08);
        color: var(--text-primary);
    }

    .popover {
        position: absolute;
        top: calc(100% + 8px);
        right: 0;
        width: 320px;
        background: var(--bg-secondary);
        border: 1px solid var(--border);
        border-radius: 10px;
        box-shadow: 0 10px 25px rgba(0, 0, 0, 0.22);
        padding: 12px 12px 10px;
        z-index: 50;
    }

    .row {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 10px;
    }

    .header-row {
        margin-bottom: 10px;
    }

    .header-actions {
        display: inline-flex;
        gap: 8px;
        align-items: center;
    }

    .title {
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-weight: 700;
        color: var(--text-tertiary);
    }

    .reset {
        background: transparent;
        border: 1px solid var(--border);
        color: var(--text-tertiary);
        border-radius: 6px;
        padding: 4px 8px;
        font-size: 10px;
        font-weight: 800;
        cursor: pointer;
        font-family: inherit;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }

    .list {
        display: flex;
        flex-direction: column;
        gap: 6px;
    }

    .list-scroll {
        max-height: 220px;
        overflow: auto;
        padding-right: 4px;
    }

    .list-row {
        display: flex;
        gap: 8px;
        align-items: center;
    }

    .list-toggle {
        flex: 1;
        display: flex;
        align-items: center;
        gap: 10px;
        text-align: left;
        background: var(--bg-tertiary);
        border: 1px solid var(--border);
        color: var(--text-tertiary);
        border-radius: 8px;
        padding: 8px 10px;
        font-size: 10px;
        font-weight: 800;
        cursor: pointer;
        transition: background 0.15s ease, border-color 0.15s ease, color 0.15s ease;
        font-family: inherit;
        line-height: 1.1;
        white-space: nowrap;
    }

    .list-toggle:hover {
        color: var(--text-secondary);
        border-color: rgba(255, 255, 255, 0.18);
    }

    .list-toggle.checked {
        color: var(--text-primary);
        border-color: rgba(0, 217, 255, 0.35);
        background: rgba(0, 217, 255, 0.08);
    }

    .clear {
        background: transparent;
        border: 1px solid var(--border);
        color: var(--text-tertiary);
        border-radius: 6px;
        padding: 4px 8px;
        font-size: 10px;
        font-weight: 800;
        cursor: pointer;
        font-family: inherit;
        text-transform: uppercase;
        letter-spacing: 0.06em;
    }

    .only {
        background: transparent;
        border: 1px solid var(--border);
        color: var(--text-tertiary);
        border-radius: 8px;
        padding: 8px 10px;
        font-size: 10px;
        font-weight: 800;
        cursor: pointer;
        font-family: inherit;
        text-transform: uppercase;
        letter-spacing: 0.06em;
        white-space: nowrap;
    }

    .only:hover {
        color: var(--text-secondary);
        border-color: rgba(255, 255, 255, 0.18);
    }

    .clear:disabled {
        opacity: 0.5;
        cursor: default;
    }

    .search {
        margin: 8px 0 2px;
    }

    .search-input {
        width: 100%;
        padding: 8px 10px;
        border-radius: 8px;
        border: 1px solid var(--border);
        background: var(--bg-tertiary);
        color: var(--text-primary);
        font-size: 12px;
        font-family: inherit;
    }

    .search-input::placeholder {
        color: var(--text-tertiary);
    }

    .check {
        width: 16px;
        display: inline-flex;
        justify-content: center;
        color: rgba(0, 217, 255, 0.95);
        flex: 0 0 16px;
    }

    .label {
        flex: 1;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .count {
        color: var(--text-tertiary);
        font-weight: 700;
        flex: 0 0 auto;
    }

    .divider {
        margin: 12px 0 6px;
        border-top: 1px solid var(--border);
        position: relative;
        height: 1px;
    }

    .divider-text {
        position: absolute;
        top: -9px;
        left: 0;
        padding-right: 10px;
        background: var(--bg-secondary);
        font-size: 10px;
        text-transform: uppercase;
        letter-spacing: 0.08em;
        font-weight: 700;
        color: var(--text-tertiary);
    }

    .muted {
        font-size: 12px;
        color: var(--text-tertiary);
    }

    .error {
        font-size: 12px;
        color: rgba(255, 68, 68, 0.95);
    }
</style>
