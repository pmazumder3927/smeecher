/**
 * Token parsing utilities
 */

export function isNegatedToken(token) {
    return typeof token === 'string' && (token.startsWith('-') || token.startsWith('!'));
}

export function stripNegation(token) {
    if (typeof token !== 'string') return '';
    return isNegatedToken(token) ? token.slice(1) : token;
}

/**
 * Get token type from token string
 */
export function getTokenType(token) {
    const t = stripNegation(token);
    if (t.startsWith('U:')) return 'unit';
    if (t.startsWith('I:')) return 'item';
    if (t.startsWith('E:')) return 'equipped';
    if (t.startsWith('T:')) return 'trait';
    return 'unknown';
}

/**
 * Parse token into components
 */
export function parseToken(token) {
    const negated = isNegatedToken(token);
    const raw = stripNegation(token);
    const type = getTokenType(raw);

    switch (type) {
        case 'unit': {
            const rest = raw.slice(2);
            const idx = rest.lastIndexOf(':');
            if (idx !== -1) {
                const unit = rest.slice(0, idx);
                const starsRaw = rest.slice(idx + 1);
                const stars = parseInt(starsRaw, 10);
                if (unit && Number.isFinite(stars)) {
                    return { type: 'unit', unit, stars, negated };
                }
            }
            return { type: 'unit', unit: rest, stars: null, negated };
        }

        case 'item':
            return { type: 'item', item: raw.slice(2), negated };

        case 'equipped': {
            const [unit, item] = raw.slice(2).split('|');
            return { type: 'equipped', unit, item, negated };
        }

        case 'trait': {
            const parts = raw.slice(2).split(':');
            if (parts.length === 2) {
                return { type: 'trait', trait: parts[0], tier: parseInt(parts[1]), negated };
            }
            return { type: 'trait', trait: parts[0], tier: null, negated };
        }

        default:
            return { type: 'unknown', negated };
    }
}

/**
 * Get display label for a token
 */
export function getTokenLabel(token, getDisplayName) {
    const parsed = parseToken(token);

    let label = '';
    switch (parsed.type) {
        case 'unit':
            if (parsed.stars) {
                label = `${getDisplayName('unit', parsed.unit)} ${parsed.stars}★`;
                break;
            }
            label = getDisplayName('unit', parsed.unit);
            break;

        case 'item':
            label = getDisplayName('item', parsed.item);
            break;

        case 'equipped':
            label = `${getDisplayName('unit', parsed.unit)} → ${getDisplayName('item', parsed.item)}`;
            break;

        case 'trait':
            const name = getDisplayName('trait', parsed.trait);
            label = parsed.tier ? `${name} ${parsed.tier}` : name;
            break;

        default:
            label = stripNegation(token);
            break;
    }

    if (parsed.negated) return `Not ${label}`;
    return label;
}
