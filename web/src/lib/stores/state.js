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

// Hovered tokens (transient highlight for exploration)
export const hoveredTokens = writable(new Set());

// Graph highlight priority: hover beats selection.
export const graphHighlightTokens = derived(
    [hoveredTokens, highlightedTokens],
    ([$hoveredTokens, $highlightedTokens]) => {
        if ($hoveredTokens && $hoveredTokens.size > 0) return $hoveredTokens;
        return $highlightedTokens;
    }
);

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
export const itemExplorerSortMode = writable('necessity'); // helpful | harmful | impact | necessity
export const itemExplorerNecessityOutcome = writable('top4'); // top4 | win | placement | rank_score
export const itemExplorerFocus = writable('unit'); // unit | item
export const itemExplorerUnit = writable(null);
export const itemExplorerItem = writable(null);

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

function _stripNegation(token) {
    if (typeof token !== 'string') return '';
    return token.startsWith('-') || token.startsWith('!') ? token.slice(1) : token;
}

function _oppositeToken(token) {
    const base = _stripNegation(token);
    if (!base) return null;
    return token.startsWith('-') || token.startsWith('!') ? base : `-${base}`;
}

function _isNegated(token) {
    return typeof token === 'string' && (token.startsWith('-') || token.startsWith('!'));
}

function _parseEquippedRaw(rawToken) {
    if (typeof rawToken !== 'string' || !rawToken.startsWith('E:')) return null;
    const rest = rawToken.slice(2);
    const sep = rest.indexOf('|');
    if (sep === -1) return null;
    const unit = rest.slice(0, sep);
    let itemPart = rest.slice(sep + 1);
    let copies = 1;

    const colon = itemPart.lastIndexOf(':');
    if (colon !== -1) {
        const maybe = itemPart.slice(colon + 1);
        const n = parseInt(maybe, 10);
        if (Number.isFinite(n) && String(n) === maybe && n >= 2) {
            copies = n;
            itemPart = itemPart.slice(0, colon);
        }
    }

    if (!unit || !itemPart) return null;
    return { unit, item: itemPart, copies };
}

function _equippedPrefix(unit, item) {
    return `E:${unit}|${item}`;
}

function _equippedMatchesPrefix(rawToken, prefix) {
    return rawToken === prefix || rawToken.startsWith(`${prefix}:`);
}

function _equippedCopiesFromRaw(rawToken) {
    const parsed = _parseEquippedRaw(rawToken);
    return parsed?.copies ?? 1;
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
        const raw = _stripNegation(token);
        const equipped = _parseEquippedRaw(raw);
        if (equipped) {
            const prefix = _equippedPrefix(equipped.unit, equipped.item);
            const wantCopies = equipped.copies ?? 1;
            const wantNegated = _isNegated(token);

            // Remove any opposite-sign variants for this exact equipped constraint.
            let next = tokens.filter((t) => {
                const tRaw = _stripNegation(t);
                if (!_equippedMatchesPrefix(tRaw, prefix)) return true;
                return _isNegated(t) === wantNegated;
            });

            // Find existing same-sign count tokens for this unit+item.
            const existing = next.filter((t) => {
                const tRaw = _stripNegation(t);
                return _equippedMatchesPrefix(tRaw, prefix) && _isNegated(t) === wantNegated;
            });

            const best = (() => {
                if (existing.length === 0) return null;
                const counts = existing.map((t) => _equippedCopiesFromRaw(_stripNegation(t)));
                if (!wantNegated) return Math.max(...counts); // include: higher copies is stricter
                return Math.min(...counts); // exclude: lower copies is stricter (excludes more)
            })();

            const shouldAdd = (() => {
                if (best == null) return true;
                if (!wantNegated) return wantCopies > best;
                return wantCopies < best;
            })();

            if (!shouldAdd) return next;

            // Replace existing same-sign variants with the stricter token.
            next = next.filter((t) => {
                const tRaw = _stripNegation(t);
                if (!_equippedMatchesPrefix(tRaw, prefix)) return true;
                return _isNegated(t) !== wantNegated;
            });

            const finalToken = wantCopies >= 2 ? `${wantNegated ? '-' : ''}${prefix}:${wantCopies}` : `${wantNegated ? '-' : ''}${prefix}`;
            posthog.capture('token_added', { token: finalToken, source });
            recordAction({ type: 'token_added', source, token: finalToken });
            return [...next, finalToken];
        }

        const opposite = _oppositeToken(token);
        let next = tokens;
        if (opposite && next.includes(opposite)) {
            next = next.filter(t => t !== opposite);
        }
        if (!next.includes(token)) {
            posthog.capture('token_added', { token, source });
            recordAction({ type: 'token_added', source, token });
            return [...next, token];
        }
        return next;
    });
}

export function equipItemOnUnit(unit, item, source = 'equip_ui') {
    const unitToken = `U:${unit}`;
    const equippedToken = `E:${unit}|${item}`;
    const equippedPrefix = _equippedPrefix(unit, item);
    const oppositeUnitToken = `-U:${unit}`;
    const altOppositeUnitToken = `!U:${unit}`;
    const oppositeEquippedToken = `-E:${unit}|${item}`;
    const altOppositeEquippedToken = `!E:${unit}|${item}`;

    selectedTokens.update(tokens => {
        // Avoid contradictory include+exclude for the exact same token(s)
        const next = tokens.filter((t) => {
            if (t === oppositeUnitToken || t === altOppositeUnitToken) return false;
            const raw = _stripNegation(t);
            // Drop any excludes for this exact unit+item (including count tokens).
            if (_equippedMatchesPrefix(raw, equippedPrefix) && _isNegated(t)) return false;
            // Legacy exact-token excludes.
            if (t === oppositeEquippedToken || t === altOppositeEquippedToken) return false;
            return true;
        });
        const existing = new Set(next);
        if (!existing.has(unitToken)) {
            next.push(unitToken);
        }
        const hasAnyEquipped = next.some((t) => !_isNegated(t) && _equippedMatchesPrefix(_stripNegation(t), equippedPrefix));
        if (!hasAnyEquipped) {
            next.push(equippedToken);
            posthog.capture('token_added', { token: equippedToken, source });
            recordAction({ type: 'token_added', source, token: equippedToken });
        }
        return next;
    });
}

export function excludeItemOnUnit(unit, item, source = 'equip_ui') {
    const equippedToken = `-E:${unit}|${item}`;
    const equippedPrefix = _equippedPrefix(unit, item);
    const oppositeEquippedToken = `E:${unit}|${item}`;
    const broadUnitExcludeToken = `-U:${unit}`;
    const altBroadUnitExcludeToken = `!U:${unit}`;

    selectedTokens.update(tokens => {
        // Avoid contradictory include+exclude for the exact same equipped token
        // Also drop broad unit-exclusion if the user is specifying a narrower exclusion.
        const next = tokens.filter((t) => {
            if (t === broadUnitExcludeToken || t === altBroadUnitExcludeToken) return false;
            const raw = _stripNegation(t);
            // Drop any includes for this exact unit+item (including count tokens).
            if (_equippedMatchesPrefix(raw, equippedPrefix) && !_isNegated(t)) return false;
            // Legacy exact-token include.
            if (t === oppositeEquippedToken) return false;
            return true;
        });
        const existingExclude = next.filter((t) => _isNegated(t) && _equippedMatchesPrefix(_stripNegation(t), equippedPrefix));
        const bestExcludeCopies = existingExclude.length > 0
            ? Math.min(...existingExclude.map((t) => _equippedCopiesFromRaw(_stripNegation(t))))
            : null;
        const alreadyBroad = bestExcludeCopies === 1;
        if (!alreadyBroad) {
            // Replace any narrower exclude (e.g. "-E:Unit|Item:2") with the broad exclusion.
            for (const t of existingExclude) {
                const idx = next.indexOf(t);
                if (idx !== -1) next.splice(idx, 1);
            }
            next.push(equippedToken);
            posthog.capture('token_added', { token: equippedToken, source });
            recordAction({ type: 'token_added', source, token: equippedToken });
        }
        return next;
    });
}

export function addTokens(tokensToAdd, source = 'unknown') {
    selectedTokens.update(tokens => {
        let next = [...tokens];
        const added = [];

        for (const t of tokensToAdd) {
            const raw = _stripNegation(t);
            const equipped = _parseEquippedRaw(raw);
            if (equipped) {
                const prefix = _equippedPrefix(equipped.unit, equipped.item);
                const wantCopies = equipped.copies ?? 1;
                const wantNegated = _isNegated(t);

                // Remove opposite-sign variants.
                next = next.filter((x) => {
                    const xRaw = _stripNegation(x);
                    if (!_equippedMatchesPrefix(xRaw, prefix)) return true;
                    return _isNegated(x) === wantNegated;
                });

                const sameSign = next.filter((x) => {
                    const xRaw = _stripNegation(x);
                    return _equippedMatchesPrefix(xRaw, prefix) && _isNegated(x) === wantNegated;
                });

                const best = (() => {
                    if (sameSign.length === 0) return null;
                    const counts = sameSign.map((x) => _equippedCopiesFromRaw(_stripNegation(x)));
                    if (!wantNegated) return Math.max(...counts);
                    return Math.min(...counts);
                })();

                const shouldAdd = (() => {
                    if (best == null) return true;
                    if (!wantNegated) return wantCopies > best;
                    return wantCopies < best;
                })();

                if (!shouldAdd) continue;

                // Replace same-sign variants with stricter token.
                next = next.filter((x) => {
                    const xRaw = _stripNegation(x);
                    if (!_equippedMatchesPrefix(xRaw, prefix)) return true;
                    return _isNegated(x) !== wantNegated;
                });

                const finalToken = wantCopies >= 2 ? `${wantNegated ? '-' : ''}${prefix}:${wantCopies}` : `${wantNegated ? '-' : ''}${prefix}`;
                if (!next.includes(finalToken)) {
                    next.push(finalToken);
                    added.push(finalToken);
                }
                continue;
            }

            const opposite = _oppositeToken(t);
            if (opposite && next.includes(opposite)) {
                next = next.filter((x) => x !== opposite);
            }
            if (!next.includes(t)) {
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

export function removeUnitFilters(unit, source = 'ui', options = {}) {
    const negated = !!options?.negated;
    const unitToken = negated ? `-U:${unit}` : `U:${unit}`;
    const altUnitToken = negated ? `!U:${unit}` : null;
    const unitStarPrefix = `${unitToken}:`;
    const altUnitStarPrefix = altUnitToken ? `${altUnitToken}:` : null;
    const equippedPrefix = negated ? `-E:${unit}|` : `E:${unit}|`;
    const altEquippedPrefix = negated ? `!E:${unit}|` : null;

    selectedTokens.update(tokens => {
        const removed = tokens.filter(t =>
            t === unitToken ||
            (altUnitToken && t === altUnitToken) ||
            t.startsWith(unitStarPrefix) ||
            (altUnitStarPrefix && t.startsWith(altUnitStarPrefix)) ||
            t.startsWith(equippedPrefix) ||
            (altEquippedPrefix && t.startsWith(altEquippedPrefix))
        );
        if (removed.length > 0) {
            posthog.capture('unit_filters_removed', { unit, negated, count: removed.length, source });
            recordAction({ type: 'tokens_removed', source, tokens: removed });
        }
        return tokens.filter(t =>
            !(
                t === unitToken ||
                (altUnitToken && t === altUnitToken) ||
                t.startsWith(unitStarPrefix) ||
                (altUnitStarPrefix && t.startsWith(altUnitStarPrefix)) ||
                t.startsWith(equippedPrefix) ||
                (altEquippedPrefix && t.startsWith(altEquippedPrefix))
            )
        );
    });
}

export function setUnitStarFilter(unit, stars, source = 'ui', options = {}) {
    const negated = !!options?.negated;
    const baseToken = negated ? `-U:${unit}` : `U:${unit}`;
    const altBaseToken = negated ? `!U:${unit}` : null;
    const starPrefix = `${baseToken}:`;
    const altStarPrefix = altBaseToken ? `${altBaseToken}:` : null;

    const starNum = stars == null ? null : parseInt(stars, 10);
    const desiredStarToken = Number.isFinite(starNum) ? `${baseToken}:${starNum}` : null;

    selectedTokens.update((tokens) => {
        const removed = [];

        const wantStar = Number.isFinite(starNum);
        const wantAny = !wantStar;

        let next = tokens.filter((t) => {
            const isStarToken = t.startsWith(starPrefix) || (altStarPrefix && t.startsWith(altStarPrefix));
            if (isStarToken) {
                removed.push(t);
                return false;
            }

            // For negated unit filters, a base token (-U:Unit) would dominate any
            // star-level exclusion. When selecting a specific star, drop the base.
            if (negated && wantStar && (t === baseToken || (altBaseToken && t === altBaseToken))) {
                removed.push(t);
                return false;
            }

            return true;
        });

        const added = [];

        if (!negated) {
            // Inclusion: keep base token so "Any" means any star.
            if (!next.includes(baseToken)) {
                next = [...next, baseToken];
                added.push(baseToken);
            }
            if (desiredStarToken && !next.includes(desiredStarToken)) {
                next = [...next, desiredStarToken];
                added.push(desiredStarToken);
            }
        } else {
            // Exclusion: either exclude a specific star (no base token),
            // or exclude the unit at any star (base token).
            if (wantStar) {
                if (desiredStarToken && !next.includes(desiredStarToken)) {
                    next = [...next, desiredStarToken];
                    added.push(desiredStarToken);
                }
            } else if (wantAny) {
                if (!next.includes(baseToken) && !(altBaseToken && next.includes(altBaseToken))) {
                    next = [...next, baseToken];
                    added.push(baseToken);
                }
            }
        }

        if (removed.length > 0 || added.length > 0) {
            posthog.capture('unit_star_filter_set', {
                unit,
                stars: Number.isFinite(starNum) ? starNum : null,
                negated,
                added_count: added.length,
                removed_count: removed.length,
                source
            });
            recordAction({
                type: 'unit_star_filter_set',
                source,
                tokens: [...added, ...removed]
            });
        }

        return next;
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

export function setHoveredTokens(tokens) {
    hoveredTokens.set(new Set(tokens));
}

export function clearHoveredTokens() {
    hoveredTokens.set(new Set());
}
