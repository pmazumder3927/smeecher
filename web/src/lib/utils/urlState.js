import { get } from 'svelte/store';
import { activeTypes, itemPrefixFilters, itemTypeFilters, selectedTokens, sortMode, topK, setItemPrefixFilters, setItemTypeFilters } from '../stores/state.js';

const STORAGE_KEY = 'smeecher_state_v1';

const DEFAULT_TOP_K = 15;
const DEFAULT_SORT_MODE = 'impact';
const DEFAULT_TYPES = ['unit', 'item', 'trait'];
const DEFAULT_ITEM_TYPES = ['component', 'full', 'radiant', 'artifact', 'emblem'];

const ALLOWED_TYPES = new Set(DEFAULT_TYPES);
const ALLOWED_SORT_MODES = new Set(['impact', 'helpful', 'harmful']);
const ALLOWED_ITEM_TYPES = new Set(DEFAULT_ITEM_TYPES);

const _queueMicrotask =
    typeof queueMicrotask === 'function' ? queueMicrotask : (fn) => Promise.resolve().then(fn);

function parseCommaList(value) {
    if (typeof value !== 'string') return [];
    return value
        .split(',')
        .map((t) => t.trim())
        .filter(Boolean);
}

function uniqueStrings(list, max = 500) {
    const seen = new Set();
    const out = [];
    for (const raw of list) {
        if (typeof raw !== 'string') continue;
        const v = raw.trim();
        if (!v || seen.has(v)) continue;
        seen.add(v);
        out.push(v);
        if (out.length >= max) break;
    }
    return out;
}

function isProbablyToken(value) {
    if (typeof value !== 'string') return false;
    const t = value.trim();
    if (!t) return false;
    if (/\s/.test(t)) return false;
    const raw = t.replace(/^[-!]+/, '');
    return raw.startsWith('U:') || raw.startsWith('I:') || raw.startsWith('T:') || raw.startsWith('E:');
}

function parseTokensParam(value) {
    return uniqueStrings(parseCommaList(value).filter(isProbablyToken), 200);
}

function parseTypesParam(value) {
    const types = uniqueStrings(parseCommaList(value), 10).filter((t) => ALLOWED_TYPES.has(t));
    return types.length > 0 ? types : [...DEFAULT_TYPES];
}

function parseTopKParam(value) {
    const n = parseInt(String(value ?? ''), 10);
    if (!Number.isFinite(n)) return DEFAULT_TOP_K;
    return Math.max(0, Math.min(500, n));
}

function parseSortModeParam(value) {
    const m = String(value ?? '').trim();
    if (!ALLOWED_SORT_MODES.has(m)) return DEFAULT_SORT_MODE;
    return m;
}

function parseItemTypesParam(value) {
    const types = uniqueStrings(parseCommaList(value), 20).filter((t) => ALLOWED_ITEM_TYPES.has(t));
    return types.length > 0 ? types : [...DEFAULT_ITEM_TYPES];
}

function parseItemPrefixesParam(value) {
    return uniqueStrings(parseCommaList(value), 50);
}

function hasAnyStateParams(params) {
    if (!(params instanceof URLSearchParams)) return false;
    return (
        params.has('tokens') ||
        params.has('top_k') ||
        params.has('types') ||
        params.has('sort_mode') ||
        params.has('item_types') ||
        params.has('item_prefixes')
    );
}

function getDefaultState() {
    return {
        tokens: [],
        top_k: DEFAULT_TOP_K,
        types: [...DEFAULT_TYPES],
        sort_mode: DEFAULT_SORT_MODE,
        item_types: [...DEFAULT_ITEM_TYPES],
        item_prefixes: [],
    };
}

function readStateFromUrlSearch(search) {
    if (typeof search !== 'string') return null;
    const params = new URLSearchParams(search.startsWith('?') ? search : `?${search}`);
    if (!hasAnyStateParams(params)) return null;

    return {
        tokens: parseTokensParam(params.get('tokens') ?? ''),
        top_k: parseTopKParam(params.get('top_k')),
        types: parseTypesParam(params.get('types')),
        sort_mode: parseSortModeParam(params.get('sort_mode')),
        item_types: parseItemTypesParam(params.get('item_types')),
        item_prefixes: parseItemPrefixesParam(params.get('item_prefixes')),
    };
}

function readStateFromStorage() {
    if (typeof window === 'undefined') return null;
    try {
        const raw = window.localStorage.getItem(STORAGE_KEY);
        if (!raw) return null;
        const data = JSON.parse(raw);
        if (!data || typeof data !== 'object') return null;

        return {
            tokens: Array.isArray(data.tokens) ? parseTokensParam(data.tokens.join(',')) : [],
            top_k: parseTopKParam(data.top_k),
            types: Array.isArray(data.types) ? parseTypesParam(data.types.join(',')) : [...DEFAULT_TYPES],
            sort_mode: parseSortModeParam(data.sort_mode),
            item_types: Array.isArray(data.item_types) ? parseItemTypesParam(data.item_types.join(',')) : [...DEFAULT_ITEM_TYPES],
            item_prefixes: Array.isArray(data.item_prefixes)
                ? parseItemPrefixesParam(data.item_prefixes.join(','))
                : [],
        };
    } catch {
        return null;
    }
}

function writeStateToStorage(state) {
    if (typeof window === 'undefined') return;
    try {
        window.localStorage.setItem(
            STORAGE_KEY,
            JSON.stringify({
                tokens: state.tokens,
                top_k: state.top_k,
                types: state.types,
                sort_mode: state.sort_mode,
                item_types: state.item_types,
                item_prefixes: state.item_prefixes,
            })
        );
    } catch {
        // ignore (private mode / quota / etc.)
    }
}

function applyStateToStores(state) {
    if (!state) return;

    selectedTokens.set(Array.isArray(state.tokens) ? state.tokens : []);
    activeTypes.set(new Set(Array.isArray(state.types) ? state.types : DEFAULT_TYPES));
    topK.set(parseTopKParam(state.top_k));
    sortMode.set(parseSortModeParam(state.sort_mode));
    setItemTypeFilters(Array.isArray(state.item_types) ? state.item_types : DEFAULT_ITEM_TYPES);
    setItemPrefixFilters(Array.isArray(state.item_prefixes) ? state.item_prefixes : []);
}

function applyDefaultsToStores() {
    applyStateToStores(getDefaultState());
}

export function hydrateStateFromLocation() {
    if (typeof window === 'undefined') return;

    const fromUrl = readStateFromUrlSearch(window.location.search);
    if (fromUrl) {
        applyStateToStores(fromUrl);
        return;
    }

    const fromStorage = readStateFromStorage();
    if (fromStorage) {
        applyStateToStores(fromStorage);
    }
}

export function hydrateStateFromUrlOnly() {
    if (typeof window === 'undefined') return;
    const fromUrl = readStateFromUrlSearch(window.location.search);
    if (fromUrl) applyStateToStores(fromUrl);
    else applyDefaultsToStores();
}

function stateFromStores() {
    const tokens = get(selectedTokens);
    const types = [...get(activeTypes)];
    const k = get(topK);
    const mode = get(sortMode);
    const itemTypes = [...get(itemTypeFilters)];
    const itemPrefixes = [...get(itemPrefixFilters)];

    const typesNormalized = uniqueStrings(types, 10).filter((t) => ALLOWED_TYPES.has(t));
    const itemTypesNormalized = uniqueStrings(itemTypes, 20).filter((t) => ALLOWED_ITEM_TYPES.has(t));

    return {
        tokens: Array.isArray(tokens) ? tokens.slice() : [],
        top_k: parseTopKParam(k),
        types: typesNormalized.length > 0 ? typesNormalized : [...DEFAULT_TYPES],
        sort_mode: parseSortModeParam(mode),
        item_types: itemTypesNormalized.length > 0 ? itemTypesNormalized : [...DEFAULT_ITEM_TYPES],
        item_prefixes: uniqueStrings(itemPrefixes, 50),
    };
}

function sameStringSet(a, b) {
    const aSet = new Set(Array.isArray(a) ? a : []);
    const bSet = new Set(Array.isArray(b) ? b : []);
    if (aSet.size !== bSet.size) return false;
    for (const v of aSet) if (!bSet.has(v)) return false;
    return true;
}

function shouldOmitParam(key, state) {
    const defaults = getDefaultState();

    if (key === 'tokens') return state.tokens.length === 0;
    if (key === 'top_k') return state.top_k === defaults.top_k;
    if (key === 'types') return sameStringSet(state.types, defaults.types);
    if (key === 'sort_mode') return state.sort_mode === defaults.sort_mode;
    if (key === 'item_types') return sameStringSet(state.item_types, defaults.item_types);
    if (key === 'item_prefixes') return state.item_prefixes.length === 0;
    return false;
}

function applyStateToSearchParams(state, params) {
    // Keep unrelated query params (e.g. utm) intact, but control our own keys.
    const next = new URLSearchParams(params instanceof URLSearchParams ? params : undefined);

    if (shouldOmitParam('tokens', state)) next.delete('tokens');
    else next.set('tokens', state.tokens.join(','));

    if (shouldOmitParam('top_k', state)) next.delete('top_k');
    else next.set('top_k', String(state.top_k));

    if (shouldOmitParam('types', state)) next.delete('types');
    else next.set('types', state.types.join(','));

    if (shouldOmitParam('sort_mode', state)) next.delete('sort_mode');
    else next.set('sort_mode', state.sort_mode);

    if (shouldOmitParam('item_types', state)) next.delete('item_types');
    else next.set('item_types', state.item_types.join(','));

    if (shouldOmitParam('item_prefixes', state)) next.delete('item_prefixes');
    else next.set('item_prefixes', state.item_prefixes.join(','));

    return next;
}

export function buildPermalink({ origin, pathname, searchParams } = {}) {
    if (typeof window === 'undefined') return '';

    const baseOrigin = origin || window.location.origin;
    const basePath = pathname || window.location.pathname;
    const url = new URL(basePath, baseOrigin);
    url.hash = window.location.hash;

    const state = stateFromStores();
    const nextParams = applyStateToSearchParams(state, searchParams ?? new URLSearchParams(window.location.search));
    url.search = nextParams.toString() ? `?${nextParams.toString()}` : '';

    return url.toString();
}

export function startStateSync() {
    if (typeof window === 'undefined') return () => {};

    let scheduled = false;
    let applying = false;
    let lastUrl = '';

    const syncNow = () => {
        scheduled = false;
        if (applying) return;

        const state = stateFromStores();
        writeStateToStorage(state);

        const nextUrl = buildPermalink();
        if (!nextUrl) return;
        if (nextUrl === lastUrl) return;
        if (nextUrl === window.location.href) {
            lastUrl = nextUrl;
            return;
        }

        lastUrl = nextUrl;
        window.history.replaceState({}, '', nextUrl);
    };

    const schedule = () => {
        if (scheduled) return;
        scheduled = true;
        // Batch multiple store updates (e.g. from token operations) into a single URL write.
        _queueMicrotask(syncNow);
    };

    const unsubs = [
        selectedTokens.subscribe(() => schedule()),
        activeTypes.subscribe(() => schedule()),
        topK.subscribe(() => schedule()),
        sortMode.subscribe(() => schedule()),
        itemTypeFilters.subscribe(() => schedule()),
        itemPrefixFilters.subscribe(() => schedule()),
    ];

    // Prime.
    schedule();

    // Expose a small API for temporarily suppressing sync when applying URL state.
    const stop = () => {
        for (const fn of unsubs) fn();
    };

    stop.suspend = (fn) => {
        applying = true;
        try {
            fn?.();
        } finally {
            applying = false;
            schedule();
        }
    };

    return stop;
}
