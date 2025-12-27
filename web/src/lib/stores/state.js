import { writable, derived, get } from 'svelte/store';
import posthog from '../client/posthog';

// Selected filter tokens
export const selectedTokens = writable([]);

// Last user action (for onboarding/UI flows)
export const lastAction = writable({
    type: null,
    source: null,
    token: null,
    tokens: null,
    timestamp: 0
});

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

// Sort mode for graph results: impact, helpful, harmful
export const sortMode = writable('impact');

// Item filters (candidate narrowing, not match constraints)
const DEFAULT_ITEM_TYPE_KEYS = ['component', 'full', 'radiant', 'artifact', 'emblem'];
export const itemTypeFilters = writable(new Set(DEFAULT_ITEM_TYPE_KEYS));
export const itemPrefixFilters = writable(new Set());

// Sidebar UI state
export const itemExplorerOpen = writable(false);
export const itemExplorerTab = writable('builds'); // builds | items
export const itemExplorerSortMode = writable('helpful'); // helpful | harmful | impact
export const itemExplorerUnit = writable(null);

export const clusterExplorerOpen = writable(false);
export const clusterExplorerRunRequest = writable(0);

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

function recordAction(action) {
    lastAction.set({
        type: action?.type ?? null,
        source: action?.source ?? null,
        token: action?.token ?? null,
        tokens: action?.tokens ?? null,
        timestamp: Date.now()
    });
}

// For UI events that don't directly change tokens (e.g. "clusters_run")
export function recordUiAction(type, source = 'ui', payload = {}) {
    recordAction({
        type,
        source,
        token: payload?.token ?? null,
        tokens: payload?.tokens ?? null
    });
}

// Helper functions
export function addToken(token, source = 'unknown') {
    selectedTokens.update(tokens => {
        if (!tokens.includes(token)) {
            posthog.capture('token_added', { token, source });
            recordAction({ type: 'token_added', source, token });
            return [...tokens, token];
        }
        return tokens;
    });
}

export function equipItemOnUnit(unit, item, source = 'equip_ui') {
    const unitToken = `U:${unit}`;
    const equippedToken = `E:${unit}|${item}`;

    selectedTokens.update(tokens => {
        const existing = new Set(tokens);
        const next = [...tokens];
        if (!existing.has(unitToken)) {
            next.push(unitToken);
        }
        if (!existing.has(equippedToken)) {
            next.push(equippedToken);
            posthog.capture('token_added', { token: equippedToken, source });
            recordAction({ type: 'token_added', source, token: equippedToken });
        }
        return next;
    });
}

export function addTokens(tokensToAdd, source = 'unknown') {
    selectedTokens.update(tokens => {
        const existing = new Set(tokens);
        const next = [...tokens];
        const added = [];
        for (const t of tokensToAdd) {
            if (!existing.has(t)) {
                existing.add(t);
                next.push(t);
                added.push(t);
            }
        }
        if (Array.isArray(tokensToAdd) && tokensToAdd.length > 0) {
            recordAction({ type: 'tokens_added', source, tokens: added });
        }
        return next;
    });
}

export function setTokens(tokens, source = 'unknown') {
    recordAction({ type: 'tokens_set', source, tokens });
    selectedTokens.set(tokens);
}

export function clearTokens() {
    posthog.capture('tokens_cleared');
    recordAction({ type: 'tokens_cleared', source: 'ui' });
    selectedTokens.set([]);
}

export function removeToken(token) {
    posthog.capture('token_removed', { token });
    recordAction({ type: 'token_removed', source: 'ui', token });
    selectedTokens.update(tokens => tokens.filter(t => t !== token));
}

export function removeUnitFilters(unit, source = 'ui') {
    const unitToken = `U:${unit}`;
    const unitStarPrefix = `${unitToken}:`;
    const equippedPrefix = `E:${unit}|`;

    selectedTokens.update(tokens => {
        const removed = tokens.filter(t => t === unitToken || t.startsWith(unitStarPrefix) || t.startsWith(equippedPrefix));
        if (removed.length > 0) {
            posthog.capture('unit_filters_removed', { unit, count: removed.length, source });
            recordAction({ type: 'tokens_removed', source, tokens: removed });
        }
        return tokens.filter(t => !(t === unitToken || t.startsWith(unitStarPrefix) || t.startsWith(equippedPrefix)));
    });
}

export function toggleType(type) {
    activeTypes.update(types => {
        const newTypes = new Set(types);
        if (newTypes.has(type)) {
            newTypes.delete(type);
            posthog.capture('filter_toggled', { type, enabled: false });
        } else {
            newTypes.add(type);
            posthog.capture('filter_toggled', { type, enabled: true });
        }
        return newTypes;
    });
}

export function toggleItemTypeFilter(itemType) {
    itemTypeFilters.update((types) => {
        const next = new Set(types);
        if (next.has(itemType)) {
            // Never allow zero selected (would hide all items)
            if (next.size === 1) return types;
            // Item sets (prefix filters) only apply to full items
            if (itemType === 'full' && get(itemPrefixFilters).size > 0) return types;
            next.delete(itemType);
        } else {
            next.add(itemType);
        }
        return next;
    });
}

export function toggleItemPrefixFilter(prefix) {
    let added = false;
    itemPrefixFilters.update((prefixes) => {
        const next = new Set(prefixes);
        if (next.has(prefix)) {
            next.delete(prefix);
        } else {
            next.add(prefix);
            added = true;
        }
        return next;
    });

    // Prefix filters only apply to full items; ensure full items remain enabled.
    if (added) {
        itemTypeFilters.update((types) => {
            if (types.has('full')) return types;
            const next = new Set(types);
            next.add('full');
            return next;
        });
    }
}

export function clearItemPrefixFilters() {
    itemPrefixFilters.set(new Set());
}

export function setItemTypeFilters(types) {
    const next = new Set(types);
    if (next.size === 0) return;

    // If full items are disabled, clear any set/prefix filters (they only apply to full items).
    if (!next.has('full') && get(itemPrefixFilters).size > 0) {
        itemPrefixFilters.set(new Set());
    }

    itemTypeFilters.set(next);
}

export function setItemPrefixFilters(prefixes) {
    const next = new Set(prefixes);
    itemPrefixFilters.set(next);

    if (next.size > 0) {
        itemTypeFilters.update((types) => {
            if (types.has('full')) return types;
            const t = new Set(types);
            t.add('full');
            return t;
        });
    }
}

export function resetItemTypeFilters() {
    itemTypeFilters.set(new Set(DEFAULT_ITEM_TYPE_KEYS));
}

export function resetItemFilters() {
    resetItemTypeFilters();
    clearItemPrefixFilters();
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
