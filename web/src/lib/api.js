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
 * Fetch graph data for given tokens
 */
export async function fetchGraphData(tokens, topK = 15) {
    const tokensParam = tokens.join(',');
    const response = await fetch(
        `${API_BASE}/graph?tokens=${encodeURIComponent(tokensParam)}&top_k=${topK}&t=${Date.now()}`
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
