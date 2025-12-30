export function fmtPct(x) {
    return `${Math.round((x ?? 0) * 100)}%`;
}

export function fmtLift(x) {
    if (x === null || x === undefined) return '—';
    if (x >= 9.95) return '×10+';
    return `×${x.toFixed(2)}`;
}

export function fmtSignedPct(x, digits = 1) {
    if (x === null || x === undefined) return '—';
    const v = (x ?? 0) * 100;
    const sign = v > 0 ? '+' : '';
    return `${sign}${v.toFixed(digits)}%`;
}

export function fmtSigned(x, digits = 2) {
    if (x === null || x === undefined) return '—';
    const v = x ?? 0;
    const sign = v > 0 ? '+' : '';
    return `${sign}${v.toFixed(digits)}`;
}

export function trailingNumber(label) {
    const m = String(label ?? '').trim().match(/(\d+)\s*$/);
    return m ? m[1] : null;
}

export function stripTrailingNumber(label) {
    return String(label ?? '').trim().replace(/\s*\d+\s*$/, '');
}

export function deltaClass(metric, value) {
    if (value === null || value === undefined) return 'neutral';
    const v = Number(value);
    if (!Number.isFinite(v)) return 'neutral';

    const eps = 0.005;
    if (metric === 'avg') {
        if (v < -0.05) return 'pos';
        if (v > 0.05) return 'neg';
        return 'neutral';
    }

    if (metric === 'eighth') {
        if (v > eps) return 'neg';
        if (v < -eps) return 'pos';
        return 'neutral';
    }

    // win/top4: higher is better
    if (v > eps) return 'pos';
    if (v < -eps) return 'neg';
    return 'neutral';
}

