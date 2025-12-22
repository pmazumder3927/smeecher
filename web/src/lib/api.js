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
 * Get engine stats
 */
export async function getStats() {
    const response = await fetch(`${API_BASE}/stats`);
    return response.json();
}
