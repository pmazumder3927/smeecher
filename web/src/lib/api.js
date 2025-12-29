/**
 * API client for Smeecher backend
 */

const API_BASE = '';  // Same origin

/**
 * Search for tokens matching query
 */
export async function searchTokens(query) {
    const response = await fetch(`${API_BASE}/search?q=${encodeURIComponent(query)}`);
    return response.json();
}

/**
 * Fetch searchable token index for client-side search
 */
export async function fetchSearchIndex() {
    const response = await fetch(`${API_BASE}/search-index`);
    if (!response.ok) {
        throw new Error('Failed to fetch search index');
    }
    return response.json();
}

/**
 * Fetch available item filter options (types + algorithmic prefixes)
 */
export async function fetchItemFilters() {
    const response = await fetch(`${API_BASE}/item-filters?t=${Date.now()}`);
    const data = await response.json().catch(() => null);
    if (!response.ok) {
        throw new Error(data?.detail || 'Failed to fetch item filters');
    }
    if (!data || !Array.isArray(data.item_types) || !Array.isArray(data.item_prefixes)) {
        throw new Error('Invalid item filters response');
    }
    return data;
}

/**
 * Fetch graph data for given tokens
 * @param {string[]} tokens - Filter tokens
 * @param {number} topK - Max edges to return per type
 * @param {Set<string>} activeTypes - Active node types (unit, item, trait)
 * @param {string} sortMode - Sort mode: impact, helpful, harmful
 */
export async function fetchGraphData(tokens, topK = 15, activeTypes = null, sortMode = 'impact', options = {}) {
    const tokensParam = tokens.join(',');
    const typesParam = activeTypes ? [...activeTypes].join(',') : 'unit,item,trait';
    const { itemTypes = null, itemPrefixes = null } = options;
    const search = new URLSearchParams({
        tokens: tokensParam,
        top_k: String(topK),
        types: typesParam,
        sort_mode: sortMode,
        t: String(Date.now())
    });

    const types = itemTypes instanceof Set ? [...itemTypes] : Array.isArray(itemTypes) ? itemTypes : [];
    if (types.length > 0) {
        search.set('item_types', types.join(','));
    }
    const prefixes = itemPrefixes instanceof Set ? [...itemPrefixes] : Array.isArray(itemPrefixes) ? itemPrefixes : [];
    if (prefixes.length > 0) {
        search.set('item_prefixes', prefixes.join(','));
    }
    const response = await fetch(`${API_BASE}/graph?${search.toString()}`);
    return response.json();
}

/**
 * Fetch clustering results (comp archetypes) for given tokens
 */
export async function fetchClusters(tokens, params = {}) {
    const tokensParam = tokens.join(',');
    const search = new URLSearchParams({
        tokens: tokensParam,
        t: String(Date.now()),
        ...Object.fromEntries(
            Object.entries(params).map(([k, v]) => [k, String(v)])
        )
    });

    const response = await fetch(`${API_BASE}/clusters?${search.toString()}`);
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data?.detail || 'Failed to fetch clusters');
    }
    return data;
}

/**
 * Fetch "playbook" (win drivers) for a selected cluster
 */
export async function fetchClusterPlaybook(tokens, clusterId, params = {}) {
    const tokensParam = tokens.join(',');
    const search = new URLSearchParams({
        tokens: tokensParam,
        cluster_id: String(clusterId),
        t: String(Date.now()),
        ...Object.fromEntries(
            Object.entries(params).map(([k, v]) => [k, String(v)])
        )
    });

    const response = await fetch(`${API_BASE}/cluster-playbook?${search.toString()}`);
    const data = await response.json();
    if (!response.ok) {
        throw new Error(data?.detail || 'Failed to fetch cluster playbook');
    }
    return data;
}

/**
 * Get engine stats
 */
export async function getStats() {
    const response = await fetch(`${API_BASE}/stats`);
    return response.json();
}

/**
 * Fetch best items for a unit given current filters
 * @param {string} unit - Unit name (e.g., "MissFortune")
 * @param {string[]} tokens - Additional filter tokens
 * @param {Object} options - Optional parameters
 * @param {number} options.minSample - Minimum sample size
 * @param {number} options.topK - Max items to return (0 = unlimited)
 * @param {string} options.sortMode - Sort mode: necessity, helpful, harmful, impact
 * @param {string[]|Set<string>} options.itemTypes - Item types to include (component, full, artifact, emblem, radiant)
 * @param {string[]|Set<string>} options.itemPrefixes - Item prefixes to include (e.g., Bilgewater)
 */
export async function fetchUnitItems(unit, tokens = [], options = {}) {
    const { minSample = 30, topK = 0, sortMode = 'necessity', itemTypes = null, itemPrefixes = null } = options;
    const tokensParam = tokens.join(',');
    const search = new URLSearchParams({
        unit,
        tokens: tokensParam,
        min_sample: String(minSample),
        top_k: String(topK),
        sort_mode: sortMode,
        t: String(Date.now())
    });

    const types = itemTypes instanceof Set ? [...itemTypes] : Array.isArray(itemTypes) ? itemTypes : [];
    if (types.length > 0) {
        search.set('item_types', types.join(','));
    }

    const prefixes = itemPrefixes instanceof Set ? [...itemPrefixes] : Array.isArray(itemPrefixes) ? itemPrefixes : [];
    if (prefixes.length > 0) {
        search.set('item_prefixes', prefixes.join(','));
    }

    const response = await fetch(`${API_BASE}/unit-items?${search.toString()}`);

    if (!response.ok) {
        // Try to parse error message, but handle non-JSON responses
        let errorMsg = 'Failed to fetch unit items';
        try {
            const data = await response.json();
            errorMsg = data?.detail || errorMsg;
        } catch {
            // Response wasn't JSON (e.g., HTML error page)
        }
        throw new Error(errorMsg);
    }

    return response.json();
}

/**
 * Fetch best unit holders for an item given current filters
 * @param {string} item - Item id (e.g., "GuinsoosRageblade")
 * @param {string[]} tokens - Additional filter tokens (excluding the explored item token)
 * @param {Object} options - Optional parameters
 * @param {number} options.minSample - Minimum sample size
 * @param {number} options.topK - Max units to return (0 = unlimited)
 * @param {string} options.sortMode - Sort mode: necessity, helpful, harmful, impact
 */
export async function fetchItemUnits(item, tokens = [], options = {}) {
    const { minSample = 30, topK = 0, sortMode = 'necessity' } = options;
    const tokensParam = tokens.join(',');
    const search = new URLSearchParams({
        item,
        tokens: tokensParam,
        min_sample: String(minSample),
        top_k: String(topK),
        sort_mode: sortMode,
        t: String(Date.now())
    });

    const response = await fetch(`${API_BASE}/item-units?${search.toString()}`);

    if (!response.ok) {
        let errorMsg = 'Failed to fetch item units';
        try {
            const data = await response.json();
            errorMsg = data?.detail || errorMsg;
        } catch {
            // Response wasn't JSON
        }
        throw new Error(errorMsg);
    }

    return response.json();
}

/**
 * Fetch recommended item builds for a unit (searches item sets)
 * @param {string} unit - Unit name (e.g., "MissFortune")
 * @param {string[]} tokens - Additional filter tokens
 * @param {Object} options - Optional parameters
 * @param {number} options.minSample - Minimum sample size
 * @param {number} options.slots - Number of item slots to fill (1-3)
 * @param {string[]|Set<string>} options.itemTypes - Item types to include (component, full, artifact, emblem, radiant)
 * @param {string[]|Set<string>} options.itemPrefixes - Item prefixes to include (e.g., Bilgewater)
 */
export async function fetchUnitBuild(unit, tokens = [], options = {}) {
    const { minSample = 10, slots = 3, itemTypes = null, itemPrefixes = null } = options;
    const tokensParam = tokens.join(',');
    const search = new URLSearchParams({
        unit,
        tokens: tokensParam,
        min_sample: String(minSample),
        slots: String(slots),
        t: String(Date.now())
    });

    const types = itemTypes instanceof Set ? [...itemTypes] : Array.isArray(itemTypes) ? itemTypes : [];
    if (types.length > 0) {
        search.set('item_types', types.join(','));
    }

    const prefixes = itemPrefixes instanceof Set ? [...itemPrefixes] : Array.isArray(itemPrefixes) ? itemPrefixes : [];
    if (prefixes.length > 0) {
        search.set('item_prefixes', prefixes.join(','));
    }

    const response = await fetch(`${API_BASE}/unit-build?${search.toString()}`);

    if (!response.ok) {
        let errorMsg = 'Failed to fetch unit build';
        try {
            const data = await response.json();
            errorMsg = data?.detail || errorMsg;
        } catch {
            // Response wasn't JSON
        }
        throw new Error(errorMsg);
    }

    return response.json();
}

/**
 * Fetch doubly-robust "necessity" estimate for an item on a unit.
 * @param {string} unit - Unit name (e.g., "KaiSa")
 * @param {string} item - Item id (e.g., "GuinsoosRageblade")
 * @param {string[]} tokens - Additional filter tokens
 * @param {Object} options
 * @param {string} options.outcome - top4 | win | placement | rank_score
 * @param {boolean} options.byCluster - Include coarse CATE map
 */
export async function fetchItemNecessity(unit, item, tokens = [], options = {}) {
    const { outcome = 'top4', byCluster = false } = options;
    const tokensParam = tokens.join(',');
    const search = new URLSearchParams({
        unit,
        item,
        tokens: tokensParam,
        outcome,
        by_cluster: byCluster ? '1' : '0',
        t: String(Date.now())
    });

    const response = await fetch(`${API_BASE}/item-necessity?${search.toString()}`);
    const data = await response.json().catch(() => null);
    if (!response.ok) {
        throw new Error(data?.detail || 'Failed to fetch item necessity');
    }
    return data;
}
