/**
 * Color utilities for Smeecher
 */

/**
 * Get color for placement value
 * 1 = best (green), 4.5 = yellow, 8 = worst (red)
 */
export function getPlacementColor(placement) {
    const min = 1, max = 8, mid = 4.5;
    const p = Math.max(min, Math.min(max, placement));

    if (p <= mid) {
        const t = (p - min) / (mid - min);
        const r = Math.round(34 + t * 221);
        const g = Math.round(197 - t * 17);
        const b = Math.round(94 - t * 54);
        return `rgb(${r}, ${g}, ${b})`;
    } else {
        const t = (p - mid) / (max - mid);
        const r = 255;
        const g = Math.round(180 - t * 130);
        const b = Math.round(40 + t * 20);
        return `rgb(${r}, ${g}, ${b})`;
    }
}

/**
 * CSS variable colors
 */
export const colors = {
    unit: '#ff6b9d',
    item: '#00d9ff',
    trait: '#a855f7',
    equipped: '#f5a623',
    success: '#00d9a5',
    error: '#ff4444',
    cooccur: '#666666'
};

/**
 * Get color for token type
 */
export function getTypeColor(type) {
    return colors[type] || colors.cooccur;
}
