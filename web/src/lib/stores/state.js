import { writable, derived } from 'svelte/store';

// Selected filter tokens
export const selectedTokens = writable([]);

// Graph data from API
export const graphData = writable(null);

// Active node types for filtering
export const activeTypes = writable(new Set(['unit', 'item', 'trait']));

// Highlighted tokens (e.g., cluster selection)
export const highlightedTokens = writable(new Set());

// Tooltip state
export const tooltip = writable({
    visible: false,
    x: 0,
    y: 0,
    content: null,
    fromTouch: false
});

// Top-K limit
export const topK = writable(15);

// Derived stats from graph data
export const stats = derived(graphData, ($graphData) => {
    if (!$graphData?.base) {
        return { games: 0, avgPlacement: 4.5 };
    }
    return {
        games: $graphData.base.n,
        avgPlacement: $graphData.base.avg_placement
    };
});

// Helper functions
export function addToken(token) {
    selectedTokens.update(tokens => {
        if (!tokens.includes(token)) {
            return [...tokens, token];
        }
        return tokens;
    });
}

export function addTokens(tokensToAdd) {
    selectedTokens.update(tokens => {
        const existing = new Set(tokens);
        const next = [...tokens];
        for (const t of tokensToAdd) {
            if (!existing.has(t)) {
                existing.add(t);
                next.push(t);
            }
        }
        return next;
    });
}

export function setTokens(tokens) {
    selectedTokens.set(tokens);
}

export function clearTokens() {
    selectedTokens.set([]);
}

export function removeToken(token) {
    selectedTokens.update(tokens => tokens.filter(t => t !== token));
}

export function toggleType(type) {
    activeTypes.update(types => {
        const newTypes = new Set(types);
        if (newTypes.has(type)) {
            newTypes.delete(type);
        } else {
            newTypes.add(type);
        }
        return newTypes;
    });
}

export function showTooltip(x, y, content, fromTouch = false) {
    tooltip.set({ visible: true, x, y, content, fromTouch });
}

export function hideTooltip() {
    tooltip.update(t => {
        if (!t.fromTouch) {
            return { ...t, visible: false };
        }
        return t;
    });
}

export function forceHideTooltip() {
    tooltip.set({ visible: false, x: 0, y: 0, content: null, fromTouch: false });
}

export function setHighlightedTokens(tokens) {
    highlightedTokens.set(new Set(tokens));
}

export function clearHighlightedTokens() {
    highlightedTokens.set(new Set());
}
