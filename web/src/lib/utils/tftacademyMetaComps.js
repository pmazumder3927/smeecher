const TFTACADEMY_TIERLIST_DATA_URL = 'https://tftacademy.com/tierlist/comps/__data.json';
const CACHE_TTL_MS = 5 * 60 * 1000;

let cached = null;
let cachedAtMs = 0;
let inflight = null;

function cleanUnitApiName(apiName) {
    return String(apiName ?? '')
        .replace(/^TFT\d+_/i, '')
        .replace(/^TFT_/i, '');
}

function cleanItemApiName(apiName) {
    return String(apiName ?? '')
        .replace(/^TFT\d+_Item_/i, '')
        .replace(/^TFT_Item_/i, '');
}

function createResolver(values) {
    const memo = new Map();

    function resolve(idx) {
        if (memo.has(idx)) return memo.get(idx);
        const raw = values?.[idx];

        if (Array.isArray(raw)) {
            const out = [];
            memo.set(idx, out);
            for (const childIdx of raw) {
                out.push(resolve(childIdx));
            }
            return out;
        }

        if (raw && typeof raw === 'object') {
            const out = {};
            memo.set(idx, out);
            for (const [key, childIdx] of Object.entries(raw)) {
                out[key] = resolve(childIdx);
            }
            return out;
        }

        memo.set(idx, raw);
        return raw;
    }

    return resolve;
}

function findNodeByRootKey(nodes, key) {
    if (!Array.isArray(nodes)) return null;
    for (const node of nodes) {
        const values = node?.data;
        if (!Array.isArray(values) || values.length === 0) continue;
        const root = values[0];
        if (root && typeof root === 'object' && Object.prototype.hasOwnProperty.call(root, key)) {
            return node;
        }
    }
    return null;
}

function coerceIsoDate(value) {
    if (typeof value !== 'string') return null;
    const parsed = Date.parse(value);
    if (!Number.isFinite(parsed)) return null;
    return new Date(parsed).toISOString();
}

function extractPatch(nodes) {
    const node = findNodeByRootKey(nodes, 'patch');
    if (!node) return null;
    const values = node.data;
    const root = values?.[0];
    const patchIdx = root?.patch;
    if (typeof patchIdx !== 'number') return null;
    const resolve = createResolver(values);
    const patch = resolve(patchIdx);
    return typeof patch === 'string' ? patch : null;
}

function extractLastUpdated(nodes) {
    const node = findNodeByRootKey(nodes, 'lastUpdated');
    if (!node) return null;
    const values = node.data;
    const root = values?.[0];
    const idx = root?.lastUpdated;
    if (typeof idx !== 'number') return null;
    const resolve = createResolver(values);
    return coerceIsoDate(resolve(idx));
}

function extractMetaComps(nodes) {
    const node = findNodeByRootKey(nodes, 'guides');
    if (!node) return [];

    const values = node.data;
    const root = values?.[0];
    const guidesIdx = root?.guides;
    if (typeof guidesIdx !== 'number') return [];

    const guideRefs = values?.[guidesIdx];
    if (!Array.isArray(guideRefs)) return [];

    const resolve = createResolver(values);
    const comps = [];

    for (const compIdx of guideRefs) {
        const compRef = values?.[compIdx];
        if (!compRef || typeof compRef !== 'object') continue;

        const title = typeof compRef.title === 'number' ? resolve(compRef.title) : null;
        const metaTitle = typeof compRef.metaTitle === 'number' ? resolve(compRef.metaTitle) : null;
        const compSlug = typeof compRef.compSlug === 'number' ? resolve(compRef.compSlug) : null;
        const tier = typeof compRef.tier === 'number' ? resolve(compRef.tier) : null;
        const style = typeof compRef.style === 'number' ? resolve(compRef.style) : null;
        const difficulty = typeof compRef.difficulty === 'number' ? resolve(compRef.difficulty) : null;
        const updated = typeof compRef.updated === 'number' ? coerceIsoDate(resolve(compRef.updated)) : null;

        const mainChampionRaw =
            typeof compRef.mainChampion === 'number' ? resolve(compRef.mainChampion) : null;
        const mainChampionApiName = mainChampionRaw?.apiName;
        const mainChampion = mainChampionApiName
            ? {
                unit: cleanUnitApiName(mainChampionApiName),
                cost: typeof mainChampionRaw?.cost === 'number' ? mainChampionRaw.cost : null,
            }
            : null;

        const finalCompRaw = typeof compRef.finalComp === 'number' ? resolve(compRef.finalComp) : null;
        const units = Array.isArray(finalCompRaw)
            ? finalCompRaw
                .map((u) => {
                    const apiName = u?.apiName;
                    const unit = apiName ? cleanUnitApiName(apiName) : null;
                    if (!unit) return null;
                    const stars = typeof u?.stars === 'number' ? u.stars : 1;
                    const items = Array.isArray(u?.items)
                        ? u.items.map(cleanItemApiName).filter(Boolean)
                        : [];
                    return { unit, stars, items };
                })
                .filter(Boolean)
            : [];

        const unitTokens = [...new Set(units.map((u) => `U:${u.unit}`))];
        const itemTokens = (() => {
            const out = new Set();
            for (const u of units) {
                for (const item of u.items) out.add(`I:${item}`);
            }
            return [...out];
        })();

        comps.push({
            slug: typeof compSlug === 'string' ? compSlug : null,
            title: typeof title === 'string' ? title : null,
            metaTitle: typeof metaTitle === 'string' ? metaTitle : null,
            tier: typeof tier === 'string' ? tier : null,
            style: typeof style === 'string' ? style : null,
            difficulty: typeof difficulty === 'string' ? difficulty : null,
            updated,
            mainChampion,
            units,
            unitTokens,
            itemTokens,
        });
    }

    const filtered = comps.filter((c) => typeof c?.slug === 'string' && c.slug);
    const deduped = [];
    const seen = new Set();
    for (const comp of filtered) {
        if (seen.has(comp.slug)) continue;
        seen.add(comp.slug);
        deduped.push(comp);
    }
    return deduped;
}

export async function fetchTFTAcademyMetaComps(options = {}) {
    const { force = false, signal = undefined } = options;

    if (!force && cached && Date.now() - cachedAtMs < CACHE_TTL_MS) {
        return cached;
    }

    if (!force && inflight) return inflight;

    inflight = (async () => {
        const response = await fetch(TFTACADEMY_TIERLIST_DATA_URL, {
            method: 'GET',
            headers: { accept: 'application/json' },
            signal,
        });

        if (!response.ok) {
            throw new Error(`TFTAcademy request failed (${response.status})`);
        }

        const json = await response.json();
        const nodes = json?.nodes;
        const data = {
            patch: extractPatch(nodes),
            lastUpdated: extractLastUpdated(nodes),
            comps: extractMetaComps(nodes),
        };

        cached = data;
        cachedAtMs = Date.now();
        return data;
    })().finally(() => {
        inflight = null;
    });

    return inflight;
}
