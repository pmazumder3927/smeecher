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
 * Fetch graph data for given tokens
 * @param {string[]} tokens - Filter tokens
 * @param {number} topK - Max edges to return per type
 * @param {Set<string>} activeTypes - Active node types (unit, item, trait)
 * @param {string} sortMode - Sort mode: impact, helpful, harmful
 */
export async function fetchGraphData(tokens, topK = 15, activeTypes = null, sortMode = 'impact') {
    const tokensParam = tokens.join(',');
    const typesParam = activeTypes ? [...activeTypes].join(',') : 'unit,item,trait';
    const response = await fetch(
        `${API_BASE}/graph?tokens=${encodeURIComponent(tokensParam)}&top_k=${topK}&types=${typesParam}&sort_mode=${sortMode}&t=${Date.now()}`
    );
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
 * @param {string} options.sortMode - Sort mode: helpful, harmful, impact
 */
export async function fetchUnitItems(unit, tokens = [], options = {}) {
    const { minSample = 30, topK = 0, sortMode = 'helpful' } = options;
    const tokensParam = tokens.join(',');
    const search = new URLSearchParams({
        unit,
        tokens: tokensParam,
        min_sample: String(minSample),
        top_k: String(topK),
        sort_mode: sortMode,
        t: String(Date.now())
    });

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
 * Fetch recommended item builds for a unit (searches item sets)
 * @param {string} unit - Unit name (e.g., "MissFortune")
 * @param {string[]} tokens - Additional filter tokens
 * @param {Object} options - Optional parameters
 * @param {number} options.minSample - Minimum sample size
 * @param {number} options.slots - Number of item slots to fill (1-3)
 */
export async function fetchUnitBuild(unit, tokens = [], options = {}) {
    const { minSample = 10, slots = 3 } = options;
    const tokensParam = tokens.join(',');
    const search = new URLSearchParams({
        unit,
        tokens: tokensParam,
        min_sample: String(minSample),
        slots: String(slots),
        t: String(Date.now())
    });

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
