/**
 * Token parsing utilities
 */

/**
 * Get token type from token string
 */
export function getTokenType(token) {
    if (token.startsWith('U:')) return 'unit';
    if (token.startsWith('I:')) return 'item';
    if (token.startsWith('E:')) return 'equipped';
    if (token.startsWith('T:')) return 'trait';
    return 'unknown';
}

/**
 * Parse token into components
 */
export function parseToken(token) {
    const type = getTokenType(token);

    switch (type) {
        case 'unit':
            return { type: 'unit', unit: token.slice(2) };

        case 'item':
            return { type: 'item', item: token.slice(2) };

        case 'equipped': {
            const [unit, item] = token.slice(2).split('|');
            return { type: 'equipped', unit, item };
        }

        case 'trait': {
            const parts = token.slice(2).split(':');
            if (parts.length === 2) {
                return { type: 'trait', trait: parts[0], tier: parseInt(parts[1]) };
            }
            return { type: 'trait', trait: parts[0], tier: null };
        }

        default:
            return { type: 'unknown' };
    }
}

/**
 * Get display label for a token
 */
export function getTokenLabel(token, getDisplayName) {
    const parsed = parseToken(token);

    switch (parsed.type) {
        case 'unit':
            return getDisplayName('unit', parsed.unit);

        case 'item':
            return getDisplayName('item', parsed.item);

        case 'equipped':
            return `${getDisplayName('unit', parsed.unit)} → ${getDisplayName('item', parsed.item)}`;

        case 'trait':
            const name = getDisplayName('trait', parsed.trait);
            return parsed.tier ? `${name} ${parsed.tier}` : name;

        default:
            return token;
    }
}
