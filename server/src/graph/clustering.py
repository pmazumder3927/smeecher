"""
Comp archetype discovery via clustering.

This is a productionized version of the exploratory script in `playground/`.
It clusters player matches (comps) into archetypes using a binary feature
vector of token presence (units / traits / items), then summarizes each cluster
with the stats a TFT nerd actually wants.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import asdict, dataclass
import hashlib
import threading
import time

import numpy as np
import orjson
from pyroaring import BitMap

from .engine import GraphEngine
from .features import TokenFeatureParams, build_sparse_feature_matrix, select_feature_tokens


@dataclass(frozen=True, slots=True)
class ClusterParams:
    n_clusters: int = 15
    use_units: bool = True
    use_traits: bool = True
    use_items: bool = False
    min_token_freq: int = 100
    min_cluster_size: int = 50
    top_k_tokens: int = 10
    random_state: int = 42


@dataclass(frozen=True, slots=True)
class TokenReportParams:
    use_units: bool = True
    use_traits: bool = True
    use_items: bool = True
    min_token_freq: int = 100
    top_k_tokens: int = 10


@dataclass(frozen=True, slots=True)
class _CacheKey:
    include_tokens: tuple[str, ...]
    exclude_tokens: tuple[str, ...]
    params: ClusterParams


_CACHE_LOCK = threading.Lock()
_CACHE: OrderedDict[_CacheKey, tuple[float, dict]] = OrderedDict()
_MEMBERS_CACHE: OrderedDict[_CacheKey, tuple[float, dict[int, BitMap]]] = OrderedDict()
_CACHE_MAX = 24
_CACHE_TTL_S = 10 * 60

_GLOBAL_BASE_LOCK = threading.Lock()
_GLOBAL_BASE_CACHE: tuple[tuple[int, int], dict] | None = None


def _run_id(key: _CacheKey) -> str:
    payload = {
        "include_tokens": key.include_tokens,
        "exclude_tokens": key.exclude_tokens,
        "params": asdict(key.params),
    }
    # Deterministic within and across processes (unlike Python's hash()).
    return hashlib.sha1(orjson.dumps(payload)).hexdigest()


def _cache_get(key: _CacheKey) -> dict | None:
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        ts, value = entry
        if (now - ts) > _CACHE_TTL_S:
            _CACHE.pop(key, None)
            _MEMBERS_CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key, last=True)
        if key in _MEMBERS_CACHE:
            _MEMBERS_CACHE.move_to_end(key, last=True)
        return value


def _cache_set(key: _CacheKey, value: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)
        _CACHE.move_to_end(key, last=True)
        while len(_CACHE) > _CACHE_MAX:
            evicted_key, _ = _CACHE.popitem(last=False)
            _MEMBERS_CACHE.pop(evicted_key, None)


def _members_get(key: _CacheKey) -> dict[int, BitMap] | None:
    now = time.time()
    with _CACHE_LOCK:
        entry = _MEMBERS_CACHE.get(key)
        if entry is None:
            return None
        ts, members = entry
        if (now - ts) > _CACHE_TTL_S:
            _MEMBERS_CACHE.pop(key, None)
            _CACHE.pop(key, None)
            return None
        _MEMBERS_CACHE.move_to_end(key, last=True)
        if key in _CACHE:
            _CACHE.move_to_end(key, last=True)
        return members


def _members_set(key: _CacheKey, members: dict[int, BitMap]) -> None:
    with _CACHE_LOCK:
        _MEMBERS_CACHE[key] = (time.time(), members)
        _MEMBERS_CACHE.move_to_end(key, last=True)
        while len(_MEMBERS_CACHE) > _CACHE_MAX:
            evicted_key, _ = _MEMBERS_CACHE.popitem(last=False)
            _CACHE.pop(evicted_key, None)


def _global_base_stats(engine: GraphEngine) -> dict:
    """
    Cached global baseline stats for delta/share computations in token reports.

    This is cached per-process and invalidated if the engine placements array changes.
    """
    global _GLOBAL_BASE_CACHE

    key = (id(engine.placements), len(engine.all_players))
    cached = _GLOBAL_BASE_CACHE
    if cached is not None and cached[0] == key:
        return cached[1]

    with _GLOBAL_BASE_LOCK:
        cached = _GLOBAL_BASE_CACHE
        if cached is not None and cached[0] == key:
            return cached[1]

        n = int(len(engine.all_players) or 0)
        if n <= 0:
            out = {
                "n": 0,
                "avg_placement": 4.5,
                "placement_hist": [0] * 8,
                **{k: 0.0 for k in _rates_from_hist([0] * 8).keys()},
            }
            _GLOBAL_BASE_CACHE = (key, out)
            return out

        if n == int(engine.placements.size):
            placements = engine.placements.astype(np.int16, copy=False)
        else:
            ids = np.array(engine.all_players.to_array(), dtype=np.int64)
            placements = engine.placements[ids].astype(np.int16, copy=False)

        placements = np.clip(placements, 0, 8)
        counts = np.bincount(placements, minlength=9)
        hist = counts[1:9].astype(int).tolist()
        avg = float(placements.mean()) if placements.size else 4.5
        rates = _rates_from_hist(hist)

        out = {
            "n": n,
            "avg_placement": round(avg, 4),
            "placement_hist": hist,
            **{k: round(v, 6) for k, v in rates.items()},
        }
        _GLOBAL_BASE_CACHE = (key, out)
        return out


def _placement_hist(engine: GraphEngine, ids: np.ndarray) -> list[int]:
    if ids.size == 0:
        return [0] * 8
    placements = engine.placements[ids].astype(np.int16, copy=False)
    placements = np.clip(placements, 0, 8)
    counts = np.bincount(placements, minlength=9)
    return counts[1:9].astype(int).tolist()


def _rates_from_hist(hist: list[int]) -> dict[str, float]:
    n = sum(hist)
    if n <= 0:
        return {
            "win_rate": 0.0,
            "top4_rate": 0.0,
            "bot4_rate": 0.0,
            "eighth_rate": 0.0,
        }
    win = hist[0]
    top4 = sum(hist[:4])
    bot4 = sum(hist[4:])
    eighth = hist[7]
    return {
        "win_rate": win / n,
        "top4_rate": top4 / n,
        "bot4_rate": bot4 / n,
        "eighth_rate": eighth / n,
    }


def _select_features(engine: GraphEngine, params: ClusterParams) -> list[str]:
    # Keep cluster signatures stable by using only base unit/trait tokens.
    return select_feature_tokens(
        engine,
        TokenFeatureParams(
            use_units=params.use_units,
            use_traits=params.use_traits,
            use_items=params.use_items,
            use_equipped=False,
            include_star_units=False,
            include_tier_traits=False,
            min_token_freq=params.min_token_freq,
        ),
    )


def _signature_tokens(
    kept_features: list[str],
    cluster_freq: np.ndarray,
    base_freq: np.ndarray,
    *,
    max_units: int = 4,
    max_traits: int = 3,
    max_items: int = 3,
) -> list[str]:
    eps = 1e-9
    lift = cluster_freq / np.maximum(base_freq, eps)
    score = cluster_freq * np.log2(np.maximum(lift, 1.0))

    def pick(prefix: str, k: int) -> list[str]:
        idx = [i for i, t in enumerate(kept_features) if t.startswith(prefix)]
        if not idx:
            return []
        idx_sorted = sorted(idx, key=lambda i: float(score[i]), reverse=True)
        picked: list[str] = []
        for i in idx_sorted:
            if cluster_freq[i] < 0.2:
                continue
            picked.append(kept_features[i])
            if len(picked) >= k:
                break
        return picked

    tokens: list[str] = []
    tokens.extend(pick("U:", max_units))
    tokens.extend(pick("T:", max_traits))
    tokens.extend(pick("I:", max_items))

    # De-dupe while preserving order
    seen: set[str] = set()
    out: list[str] = []
    for t in tokens:
        if t in seen:
            continue
        seen.add(t)
        out.append(t)
    return out


def compute_clusters(engine: GraphEngine, tokens: list[str], params: ClusterParams) -> dict:
    """
    Cluster comps within the intersection of `tokens` (empty => all matches).

    Returns JSON-serializable dict suitable for the frontend.
    """
    raw_tokens = [t.strip() for t in tokens if t and t.strip()]
    include_tokens: list[str] = []
    exclude_tokens: list[str] = []
    for t in raw_tokens:
        if t.startswith("-") or t.startswith("!"):
            raw = t.lstrip("-!")
            if raw:
                exclude_tokens.append(raw)
        else:
            include_tokens.append(t)

    canonical_include = tuple(sorted(include_tokens))
    canonical_exclude = tuple(sorted(exclude_tokens))
    key = _CacheKey(
        include_tokens=canonical_include,
        exclude_tokens=canonical_exclude,
        params=params,
    )
    run_id = _run_id(key)

    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "cached": True}

    t0 = time.perf_counter()

    base = engine.filter_bitmap(list(canonical_include), list(canonical_exclude))
    n_base = len(base)
    base_ids = np.array(base.to_array(), dtype=np.int64)
    base_avg = float(engine.avg_placement_for_bitmap(base)) if n_base > 0 else 4.5
    base_hist = _placement_hist(engine, base_ids)
    base_rates = _rates_from_hist(base_hist)

    result: dict = {
        "cached": False,
        "tokens": list(canonical_include) + [f"-{t}" for t in canonical_exclude],
        "base": {
            "n": int(n_base),
            "avg_placement": round(base_avg, 4),
            "placement_hist": base_hist,
            **{k: round(v, 6) for k, v in base_rates.items()},
        },
        "params": asdict(params),
        "clusters": [],
        "meta": {"run_id": run_id},
    }

    if n_base == 0:
        result["meta"] = {"run_id": run_id, "warning": "No matches for the current filters."}
        _cache_set(key, result)
        _members_set(key, {})
        return result

    if n_base < max(params.min_cluster_size * 2, params.n_clusters * 3):
        result["meta"] = {
            "run_id": run_id,
            "warning": "Sample too small to cluster reliably. Try fewer filters or a smaller min cluster size."
        }
        _cache_set(key, result)
        _members_set(key, {})
        return result

    features = _select_features(engine, params)
    X, kept_features, base_counts, feature_rows = build_sparse_feature_matrix(
        engine, base, base_ids, features
    )

    if X.shape[1] < 2:
        result["meta"] = {
            "run_id": run_id,
            "warning": "Not enough features in this sample (after filtering). Lower min token freq or broaden the sample."
        }
        _cache_set(key, result)
        _members_set(key, {})
        return result

    from sklearn.cluster import MiniBatchKMeans

    kmeans = MiniBatchKMeans(
        n_clusters=params.n_clusters,
        random_state=params.random_state,
        batch_size=2048,
        n_init=3,
        reassignment_ratio=0.01,
    )
    labels = kmeans.fit_predict(X)

    cluster_sizes = np.bincount(labels, minlength=params.n_clusters).astype(np.int32, copy=False)
    base_freq = base_counts.astype(np.float32) / float(n_base)

    # Precompute counts(feature present) per cluster for each feature.
    feature_cluster_counts = np.zeros((params.n_clusters, len(kept_features)), dtype=np.int32)
    for j, rows in enumerate(feature_rows):
        feature_cluster_counts[:, j] = np.bincount(labels[rows], minlength=params.n_clusters)

    clusters: list[dict] = []
    members_by_cluster_id: dict[int, BitMap] = {}
    for c in range(params.n_clusters):
        size = int(cluster_sizes[c])
        if size < params.min_cluster_size:
            continue

        row_idx = np.flatnonzero(labels == c)
        pm_ids = base_ids[row_idx]
        members_by_cluster_id[int(c)] = BitMap(pm_ids)

        placements = engine.placements[pm_ids].astype(np.int16, copy=False)
        avg_placement = float(placements.mean()) if placements.size else 4.5
        hist = _placement_hist(engine, pm_ids)
        rates = _rates_from_hist(hist)
        delta_vs_base = avg_placement - base_avg

        cluster_counts = feature_cluster_counts[c].astype(np.float32, copy=False)
        cluster_freq = cluster_counts / float(size)
        with np.errstate(divide="ignore", invalid="ignore"):
            lift = np.where(base_freq > 0, cluster_freq / base_freq, 0.0)

        def top_tokens(prefix: str) -> list[dict]:
            idx = [i for i, t in enumerate(kept_features) if t.startswith(prefix)]
            if not idx:
                return []
            idx_sorted = sorted(idx, key=lambda i: float(cluster_freq[i]), reverse=True)[: params.top_k_tokens]
            out: list[dict] = []
            for i in idx_sorted:
                out.append(
                    {
                        "token": kept_features[i],
                        "pct": round(float(cluster_freq[i]), 6),
                        "base_pct": round(float(base_freq[i]), 6),
                        "lift": round(float(lift[i]), 6) if base_freq[i] > 0 else None,
                    }
                )
            return out

        # Defining units: common here AND much more common than base.
        defining_units: list[dict] = []
        for i, tok in enumerate(kept_features):
            if not tok.startswith("U:"):
                continue
            if base_freq[i] <= 0.01:
                continue
            if cluster_freq[i] <= 0.3:
                continue
            if lift[i] <= 2.0:
                continue
            defining_units.append(
                {
                    "token": tok,
                    "pct": round(float(cluster_freq[i]), 6),
                    "base_pct": round(float(base_freq[i]), 6),
                    "lift": round(float(lift[i]), 6),
                }
            )
        defining_units.sort(key=lambda d: float(d["lift"]), reverse=True)
        defining_units = defining_units[:5]

        signature = _signature_tokens(kept_features, cluster_freq, base_freq)

        clusters.append(
            {
                "cluster_id": int(c),
                "size": size,
                "share": round(size / float(n_base), 6),
                "avg_placement": round(avg_placement, 4),
                "delta_vs_base": round(delta_vs_base, 4),
                "placement_hist": hist,
                **{k: round(v, 6) for k, v in rates.items()},
                "defining_units": defining_units,
                "top_units": top_tokens("U:"),
                "top_traits": top_tokens("T:"),
                "top_items": top_tokens("I:"),
                "signature_tokens": signature,
            }
        )

    clusters.sort(key=lambda c: (c["avg_placement"], -c["size"]))
    result["clusters"] = clusters
    result["meta"] = {
        "run_id": run_id,
        "features_requested": len(features),
        "features_used": int(X.shape[1]),
        "inertia": round(float(kmeans.inertia_), 6) if hasattr(kmeans, "inertia_") else None,
        "compute_ms": int((time.perf_counter() - t0) * 1000),
    }

    _cache_set(key, result)
    _members_set(key, members_by_cluster_id)
    return result


def compute_cluster_playbook(
    engine: GraphEngine,
    tokens: list[str],
    params: ClusterParams,
    cluster_id: int,
    *,
    min_with: int = 30,
    min_without: int = 30,
    max_drivers: int = 12,
    max_killers: int = 12,
) -> dict:
    """
    Compute a "playbook" for a selected cluster: which tokens most strongly
    correlate with wins (1st) vs losses *within* the archetype.
    """
    raw_tokens = [t.strip() for t in tokens if t and t.strip()]
    include_tokens: list[str] = []
    exclude_tokens: list[str] = []
    for t in raw_tokens:
        if t.startswith("-") or t.startswith("!"):
            raw = t.lstrip("-!")
            if raw:
                exclude_tokens.append(raw)
        else:
            include_tokens.append(t)

    key = _CacheKey(
        include_tokens=tuple(sorted(include_tokens)),
        exclude_tokens=tuple(sorted(exclude_tokens)),
        params=params,
    )
    run_id = _run_id(key)

    # Ensure clusters + members are cached (so playbook calls don't re-run KMeans
    # when invoked right after /clusters).
    clusters_result = _cache_get(key)
    if clusters_result is None:
        clusters_result = compute_clusters(engine, list(key.include_tokens) + [f"-{t}" for t in key.exclude_tokens], params)
        # compute_clusters will populate both caches.

    members = _members_get(key)
    if members is None:
        # Fall back to recomputing clusters (populates members cache).
        clusters_result = compute_clusters(engine, list(key.include_tokens) + [f"-{t}" for t in key.exclude_tokens], params)
        members = _members_get(key) or {}

    cluster = next((c for c in (clusters_result.get("clusters") or []) if c.get("cluster_id") == int(cluster_id)), None)
    if cluster is None:
        return {
            "run_id": run_id,
            "cluster_id": int(cluster_id),
            "meta": {"warning": "Cluster not found (did you change filters or parameters?)."},
            "drivers": [],
            "killers": [],
        }

    cluster_bm = members.get(int(cluster_id))
    if cluster_bm is None or not cluster_bm:
        return {
            "run_id": run_id,
            "cluster_id": int(cluster_id),
            "meta": {"warning": "Cluster membership unavailable (cache expired). Re-run clustering and try again."},
            "drivers": [],
            "killers": [],
        }

    cluster_ids = np.array(cluster_bm.to_array(), dtype=np.int64)
    placements = engine.placements[cluster_ids].astype(np.int16, copy=False)
    placements = np.clip(placements, 0, 8)
    n = int(placements.size)
    if n <= 0:
        return {
            "run_id": run_id,
            "cluster_id": int(cluster_id),
            "meta": {"warning": "Empty cluster."},
            "drivers": [],
            "killers": [],
        }

    sum_places = int(placements.sum())
    win_count = int((placements == 1).sum())
    top4_count = int((placements <= 4).sum())
    eighth_count = int((placements == 8).sum())

    base_avg = float(sum_places / n)
    base_win = float(win_count / n)
    base_top4 = float(top4_count / n)
    base_eighth = float(eighth_count / n)

    # Pseudo-count shrinkage to reduce small-sample noise (tuned to cluster size).
    prior_w = float(max(25, min(200, int(n * 0.06))))

    def shrink_rate(successes: int, trials: int, prior_rate: float) -> float:
        if trials <= 0:
            return prior_rate
        return (successes + prior_rate * prior_w) / (trials + prior_w)

    def shrink_avg(avg: float, trials: int, prior_mean: float) -> float:
        if trials <= 0:
            return prior_mean
        return (avg * trials + prior_mean * prior_w) / (trials + prior_w)

    # Candidate tokens: focus on likely "win conditions" (stars, trait breakpoints, BIS).
    candidates: list[str] = []

    def add_candidate(tok: str) -> None:
        if not tok:
            return
        if tok not in engine.token_to_id:
            return
        candidates.append(tok)

    sig = cluster.get("signature_tokens") or []
    top_units = [t.get("token") for t in (cluster.get("top_units") or []) if isinstance(t, dict)]
    top_traits = [t.get("token") for t in (cluster.get("top_traits") or []) if isinstance(t, dict)]
    top_items = [t.get("token") for t in (cluster.get("top_items") or []) if isinstance(t, dict)]
    defining_units = [t.get("token") for t in (cluster.get("defining_units") or []) if isinstance(t, dict)]

    sig_units = [t for t in sig if isinstance(t, str) and t.startswith("U:")]
    sig_traits = [t for t in sig if isinstance(t, str) and t.startswith("T:")]
    sig_items = [t for t in sig if isinstance(t, str) and t.startswith("I:")]

    units = [*(sig_units[:6]), *(defining_units[:6]), *(top_units[:12])]
    traits = [*(sig_traits[:6]), *(top_traits[:10])]
    items = [*(sig_items[:8]), *(top_items[:12])]

    # Base units/items/traits (these can include tech units that correlate with wins).
    for tok in units:
        add_candidate(tok)
    for tok in items:
        add_candidate(tok)

    # Trait breakpoints (tiered tokens).
    trait_ids: set[str] = set()
    for t in traits:
        if not isinstance(t, str) or not t.startswith("T:"):
            continue
        trait_ids.add(t[2:].split(":", 1)[0])
    for trait_id in trait_ids:
        prefix = f"T:{trait_id}:"
        for tok in engine.id_to_token:
            if tok.startswith(prefix):
                add_candidate(tok)

    # Star-level tokens (2★, 3★) for key units.
    unit_ids: set[str] = set()
    for u in units:
        if not isinstance(u, str) or not u.startswith("U:"):
            continue
        unit_ids.add(u[2:].split(":", 1)[0])
    for unit_id in unit_ids:
        for stars in (2, 3):
            add_candidate(f"U:{unit_id}:{stars}")

    # Equipped items on likely carries/tanks (limit to a few units to control runtime).
    equipped_units = []
    for u in sig_units:
        unit_id = u[2:].split(":", 1)[0]
        if unit_id not in equipped_units:
            equipped_units.append(unit_id)
        if len(equipped_units) >= 3:
            break
    if not equipped_units:
        for u in (defining_units[:3] or top_units[:3]):
            if not isinstance(u, str) or not u.startswith("U:"):
                continue
            unit_id = u[2:].split(":", 1)[0]
            if unit_id not in equipped_units:
                equipped_units.append(unit_id)
            if len(equipped_units) >= 3:
                break
    for unit_id in equipped_units:
        prefix = f"E:{unit_id}|"
        for tok in engine.id_to_token:
            if tok.startswith(prefix):
                add_candidate(tok)

    # De-dupe while preserving order.
    seen: set[str] = set()
    deduped: list[str] = []
    for tok in candidates:
        if tok in seen:
            continue
        seen.add(tok)
        deduped.append(tok)

    rows: list[dict] = []
    for tok in deduped:
        token_id = engine.token_to_id.get(tok)
        if token_id is None:
            continue
        stats = engine.tokens.get(token_id)
        if stats is None:
            continue

        with_bm = cluster_bm & stats.bitmap
        n_with = int(len(with_bm))
        n_without = n - n_with

        if n_with < min_with or n_without < min_without:
            continue

        with_ids = np.array(with_bm.to_array(), dtype=np.int64)
        with_places = engine.placements[with_ids].astype(np.int16, copy=False)
        with_places = np.clip(with_places, 0, 8)

        with_sum = int(with_places.sum())
        with_win = int((with_places == 1).sum())
        with_top4 = int((with_places <= 4).sum())
        with_eighth = int((with_places == 8).sum())

        without_sum = sum_places - with_sum
        without_win = win_count - with_win
        without_top4 = top4_count - with_top4
        without_eighth = eighth_count - with_eighth

        with_avg_raw = float(with_sum / n_with) if n_with > 0 else base_avg
        without_avg_raw = float(without_sum / n_without) if n_without > 0 else base_avg

        with_win_adj = shrink_rate(with_win, n_with, base_win)
        without_win_adj = shrink_rate(without_win, n_without, base_win)
        with_top4_adj = shrink_rate(with_top4, n_with, base_top4)
        without_top4_adj = shrink_rate(without_top4, n_without, base_top4)
        with_eighth_adj = shrink_rate(with_eighth, n_with, base_eighth)
        without_eighth_adj = shrink_rate(without_eighth, n_without, base_eighth)

        with_avg_adj = shrink_avg(with_avg_raw, n_with, base_avg)
        without_avg_adj = shrink_avg(without_avg_raw, n_without, base_avg)

        rows.append(
            {
                "token": tok,
                "type": "equipped" if tok.startswith("E:") else "unit" if tok.startswith("U:") else "trait" if tok.startswith("T:") else "item" if tok.startswith("I:") else "unknown",
                "n_with": n_with,
                "pct_in_cluster": round(n_with / float(n), 6),
                "delta_win": round(with_win_adj - without_win_adj, 6),
                "delta_top4": round(with_top4_adj - without_top4_adj, 6),
                "delta_eighth": round(with_eighth_adj - without_eighth_adj, 6),
                "delta_avg": round(with_avg_adj - without_avg_adj, 4),
                "win_with": round(with_win_adj, 6),
                "win_without": round(without_win_adj, 6),
                "top4_with": round(with_top4_adj, 6),
                "top4_without": round(without_top4_adj, 6),
                "avg_with": round(with_avg_adj, 4),
                "avg_without": round(without_avg_adj, 4),
            }
        )

    # Sort by biggest win rate deltas.
    drivers = sorted(rows, key=lambda r: (r["delta_win"], r["delta_top4"], -r["delta_avg"]), reverse=True)
    killers = sorted(rows, key=lambda r: (r["delta_win"], r["delta_top4"], -r["delta_avg"]))

    # ─────────────────────────────────────────────────────────────────
    # Comp view helpers (traits active + best item holders on-board)

    def _bm_count(tok: str) -> int:
        token_id = engine.token_to_id.get(tok)
        if token_id is None:
            return 0
        stats = engine.tokens.get(token_id)
        if stats is None:
            return 0
        return int(len(cluster_bm & stats.bitmap))

    # Trait "active" tier summary using inclusive tier tokens (T:Trait:2 means tier 2+).
    trait_order: list[str] = []
    seen_traits: set[str] = set()
    for t in [*sig_traits, *top_traits]:
        if not isinstance(t, str) or not t.startswith("T:"):
            continue
        trait_id = t[2:].split(":", 1)[0]
        if not trait_id or trait_id in seen_traits:
            continue
        seen_traits.add(trait_id)
        trait_order.append(trait_id)

    trait_summaries: list[dict] = []
    for trait_id in trait_order:
        base_tok = f"T:{trait_id}"
        n_ge_1 = _bm_count(base_tok)
        if n_ge_1 <= 0:
            continue

        # Collect tiered tokens that actually appear in the cluster.
        max_tier = 1
        n_ge: dict[int, int] = {1: n_ge_1}
        for tier in range(2, 10):
            tok = f"T:{trait_id}:{tier}"
            c = _bm_count(tok)
            if c <= 0:
                continue
            n_ge[tier] = c
            max_tier = max(max_tier, tier)

        # Compute tier "mode" via exact tier mass: P(tier==k) = P(tier>=k) - P(tier>=k+1).
        n_exact: dict[int, int] = {}
        for tier in range(1, max_tier + 1):
            ge = int(n_ge.get(tier, 0))
            ge_next = int(n_ge.get(tier + 1, 0)) if tier < max_tier else 0
            n_exact[tier] = max(0, ge - ge_next)

        active_tier = max(n_exact.keys(), key=lambda t: (n_exact[t], t))
        active_tok = base_tok if active_tier == 1 else f"T:{trait_id}:{active_tier}"

        levels: list[dict] = []
        for tier in range(1, max_tier + 1):
            tok = base_tok if tier == 1 else f"T:{trait_id}:{tier}"
            n_at_least = int(n_ge.get(tier, 0))
            n_at_exact = int(n_exact.get(tier, 0))
            levels.append(
                {
                    "tier": tier,
                    "token": tok,
                    "label": engine.get_label(tok) or tok[2:],
                    "n_at_least": n_at_least,
                    "pct_at_least": round(n_at_least / float(n), 6) if n > 0 else 0.0,
                    "n_exact": n_at_exact,
                    "pct_exact": round(n_at_exact / float(n), 6) if n > 0 else 0.0,
                }
            )

        active_level = next((lv for lv in levels if lv.get("token") == active_tok), None) or {}
        trait_summaries.append(
            {
                "trait_id": trait_id,
                "token": base_tok,
                "label": engine.get_label(base_tok) or trait_id,
                "n": int(n_ge_1),
                "pct": round(n_ge_1 / float(n), 6) if n > 0 else 0.0,
                "active_tier": int(active_tier),
                "active_token": active_tok,
                "active_label": active_level.get("label") or (engine.get_label(active_tok) or trait_id),
                "active_pct_at_least": float(active_level.get("pct_at_least") or 0.0),
                "active_pct_exact": float(active_level.get("pct_exact") or 0.0),
                "levels": levels,
            }
        )

    # Best equipped unit holders for each key item, within the cluster.
    item_order: list[str] = []
    seen_items: set[str] = set()
    for t in [*sig_items, *top_items]:
        if not isinstance(t, str) or not t.startswith("I:"):
            continue
        item_id = t[2:].split(":", 1)[0]
        if not item_id or item_id in seen_items:
            continue
        seen_items.add(item_id)
        item_order.append(item_id)

    unit_order: list[str] = []
    seen_units: set[str] = set()
    for t in [*sig_units, *defining_units, *top_units]:
        if not isinstance(t, str) or not t.startswith("U:"):
            continue
        unit_id = t[2:].split(":", 1)[0]
        if not unit_id or unit_id in seen_units:
            continue
        seen_units.add(unit_id)
        unit_order.append(unit_id)
        if len(unit_order) >= 12:
            break

    item_summaries: list[dict] = []
    best_items_by_unit: dict[str, list[dict]] = {}
    for item_id in item_order[:8]:
        item_tok = f"I:{item_id}"
        holders: list[dict] = []
        for unit_id in unit_order:
            eq_tok = f"E:{unit_id}|{item_id}"
            n_with = _bm_count(eq_tok)
            if n_with <= 0:
                continue
            holders.append(
                {
                    "unit": unit_id,
                    "token": eq_tok,
                    "n_with": int(n_with),
                    "pct_in_cluster": round(n_with / float(n), 6) if n > 0 else 0.0,
                }
            )

        holders.sort(key=lambda h: (-(h.get("pct_in_cluster") or 0.0), -(h.get("n_with") or 0)))
        best = holders[0] if holders else None
        if best:
            best_items_by_unit.setdefault(best["unit"], []).append(
                {
                    "item": item_id,
                    "token": best["token"],
                    "n_with": best["n_with"],
                    "pct_in_cluster": best["pct_in_cluster"],
                }
            )

        item_summaries.append(
            {
                "item": item_id,
                "token": item_tok,
                "label": engine.get_label(item_tok) or item_id,
                "best_holder": best,
                "top_holders": holders[:3],
            }
        )

    for unit_id, items_for_unit in best_items_by_unit.items():
        items_for_unit.sort(key=lambda r: (-(r.get("pct_in_cluster") or 0.0), r.get("item") or ""))
        best_items_by_unit[unit_id] = items_for_unit[:3]

    return {
        "run_id": run_id,
        "cluster_id": int(cluster_id),
        "base": {
            "n": int(n),
            "avg_placement": round(base_avg, 4),
            "win_rate": round(base_win, 6),
            "top4_rate": round(base_top4, 6),
            "eighth_rate": round(base_eighth, 6),
        },
        "drivers": drivers[: max_drivers],
        "killers": killers[: max_killers],
        "comp_view": {
            "traits": trait_summaries,
            "items": item_summaries,
            "best_items_by_unit": best_items_by_unit,
        },
        "meta": {
            "prior_weight": prior_w,
            "candidates_scored": len(rows),
        },
    }


def _run_id_token_report(include_tokens: tuple[str, ...], exclude_tokens: tuple[str, ...], params: TokenReportParams) -> str:
    payload = {
        "include_tokens": include_tokens,
        "exclude_tokens": exclude_tokens,
        "params": asdict(params),
    }
    return hashlib.sha1(orjson.dumps(payload)).hexdigest()


_UNIT_SUFFIX_ALIASES: tuple[str, ...] = (
    # TFTAcademy (and occasionally Riot match data) may include summoned helpers as units.
    # When possible, map them back to the parent champion token.
    "Soldier",
    "Turret",
    "Tentacle",
    "Dummy",
)


def _normalize_report_token(engine: GraphEngine, token: str) -> tuple[str | None, str | None]:
    """
    Normalize a token to one known by the engine.

    Returns (normalized_token, alias_from). If alias_from is not None, the token was
    rewritten (e.g. U:AzirSoldier -> U:Azir).
    """
    if not token:
        return None, None
    if token in engine.token_to_id:
        return token, None

    if token.startswith("U:"):
        rest = token[2:]
        if not rest:
            return None, None

        unit_part, sep, suffix = rest.partition(":")  # keep star scope suffix (e.g. :2) if present
        unit_clean = unit_part.replace("TFT16_", "").replace("TFT_", "").replace("Set16_", "")

        candidate = f"U:{unit_clean}{sep}{suffix}" if sep else f"U:{unit_clean}"
        if candidate in engine.token_to_id:
            return candidate, token

        for suffix_alias in _UNIT_SUFFIX_ALIASES:
            if not unit_clean.endswith(suffix_alias) or len(unit_clean) <= len(suffix_alias):
                continue
            base_unit = unit_clean[: -len(suffix_alias)]
            candidate = f"U:{base_unit}{sep}{suffix}" if sep else f"U:{base_unit}"
            if candidate in engine.token_to_id:
                return candidate, token
        return None, None

    if token.startswith("T:"):
        rest = token[2:]
        if not rest:
            return None, None
        trait, sep, tier = rest.partition(":")
        trait_clean = trait.replace("TFT16_", "").replace("TFT_", "").replace("Set16_", "")
        candidate = f"T:{trait_clean}{sep}{tier}" if sep else f"T:{trait_clean}"
        if candidate in engine.token_to_id:
            return candidate, token
        return None, None

    if token.startswith("I:"):
        item = token[2:]
        if not item:
            return None, None
        item_clean = item.replace("TFT16_Item_", "").replace("TFT_Item_", "")
        candidate = f"I:{item_clean}"
        if candidate in engine.token_to_id:
            return candidate, token
        return None, None

    if token.startswith("E:"):
        rest = token[2:]
        if not rest:
            return None, None
        pair, sep, count = rest.partition(":")
        unit_raw, sep2, item_raw = pair.partition("|")
        if not sep2:
            return None, None
        unit_clean = unit_raw.replace("TFT16_", "").replace("TFT_", "").replace("Set16_", "")
        item_clean = item_raw.replace("TFT16_Item_", "").replace("TFT_Item_", "")
        candidate = f"E:{unit_clean}|{item_clean}{sep}{count}" if sep else f"E:{unit_clean}|{item_clean}"
        if candidate in engine.token_to_id:
            return candidate, token
        return None, None

    return None, None


def _sanitize_report_tokens(
    engine: GraphEngine,
    include_tokens: list[str],
    exclude_tokens: list[str],
) -> tuple[list[str], list[str], dict]:
    dropped_include: list[str] = []
    dropped_exclude: list[str] = []
    aliases: dict[str, str] = {}

    normalized_include: list[str] = []
    seen_include: set[str] = set()
    for tok in include_tokens:
        norm, alias_from = _normalize_report_token(engine, tok)
        if norm is None:
            dropped_include.append(tok)
            continue
        if alias_from and alias_from != norm:
            aliases[alias_from] = norm
        if norm in seen_include:
            continue
        seen_include.add(norm)
        normalized_include.append(norm)

    normalized_exclude: list[str] = []
    seen_exclude: set[str] = set()
    for tok in exclude_tokens:
        norm, alias_from = _normalize_report_token(engine, tok)
        if norm is None:
            dropped_exclude.append(tok)
            continue
        if alias_from and alias_from != norm:
            aliases[alias_from] = norm
        if norm in seen_exclude:
            continue
        seen_exclude.add(norm)
        normalized_exclude.append(norm)

    meta: dict = {}
    if dropped_include:
        meta["dropped_include_tokens"] = dropped_include
    if dropped_exclude:
        meta["dropped_exclude_tokens"] = dropped_exclude
    if aliases:
        meta["aliases"] = aliases

    return normalized_include, normalized_exclude, meta


def compute_token_stats(engine: GraphEngine, tokens: list[str]) -> dict:
    """
    Fast stats for an arbitrary token filter without running clustering.

    Returned shape mirrors a subset of the cluster summary fields so the frontend
    can render list entries like clusters (avg/top4/win/hist/etc).
    """
    raw_tokens = [t.strip() for t in tokens if t and t.strip()]
    include_tokens: list[str] = []
    exclude_tokens: list[str] = []
    for t in raw_tokens:
        if t.startswith("-") or t.startswith("!"):
            raw = t.lstrip("-!")
            if raw:
                exclude_tokens.append(raw)
        else:
            include_tokens.append(t)

    had_include = bool(include_tokens)
    include_tokens, exclude_tokens, meta = _sanitize_report_tokens(engine, include_tokens, exclude_tokens)

    canonical_include = tuple(sorted(include_tokens))
    canonical_exclude = tuple(sorted(exclude_tokens))
    token_list = list(canonical_include) + [f"-{t}" for t in canonical_exclude]

    if had_include and not canonical_include:
        base = BitMap()
    else:
        base = engine.filter_bitmap(list(canonical_include), list(canonical_exclude))
    n_base = int(len(base))
    base_ids = np.array(base.to_array(), dtype=np.int64) if n_base > 0 else np.array([], dtype=np.int64)
    base_avg = float(engine.avg_placement_for_bitmap(base)) if n_base > 0 else 4.5
    base_hist = _placement_hist(engine, base_ids)
    base_rates = _rates_from_hist(base_hist)

    global_stats = _global_base_stats(engine)
    global_n = int(global_stats.get("n") or 0) or 0
    global_avg = float(global_stats.get("avg_placement") or 4.5)
    share = round(n_base / float(global_n), 6) if global_n > 0 else 0.0
    delta_vs_base = round(base_avg - global_avg, 4) if n_base > 0 else 0.0

    out = {
        "tokens": token_list,
        "size": n_base,
        "share": share,
        "avg_placement": round(base_avg, 4),
        "delta_vs_base": delta_vs_base,
        "placement_hist": base_hist,
        **{k: round(v, 6) for k, v in base_rates.items()},
    }

    if n_base <= 0:
        meta = {**meta, "warning": "No matches for the current filters."} if meta else {"warning": "No matches for the current filters."}
    if meta:
        out["meta"] = meta
    return out


def _token_cluster_summary(engine: GraphEngine, base: BitMap, base_ids: np.ndarray, params: TokenReportParams, run_id: str) -> dict:
    global_stats = _global_base_stats(engine)
    global_n = int(global_stats.get("n") or 0) or 0
    global_avg = float(global_stats.get("avg_placement") or 4.5)

    n_base = int(len(base))
    base_avg = float(engine.avg_placement_for_bitmap(base)) if n_base > 0 else 4.5
    base_hist = _placement_hist(engine, base_ids)
    base_rates = _rates_from_hist(base_hist)

    share = round(n_base / float(global_n), 6) if global_n > 0 else 0.0
    delta_vs_base = round(base_avg - global_avg, 4)

    cluster_like: dict = {
        "cluster_id": 0,
        "run_id": run_id,
        "size": n_base,
        "share": share,
        "avg_placement": round(base_avg, 4),
        "delta_vs_base": delta_vs_base,
        "placement_hist": base_hist,
        **{k: round(v, 6) for k, v in base_rates.items()},
        "defining_units": [],
        "top_units": [],
        "top_traits": [],
        "top_items": [],
        "signature_tokens": [],
    }

    if n_base <= 0:
        return cluster_like

    features = select_feature_tokens(
        engine,
        TokenFeatureParams(
            use_units=params.use_units,
            use_traits=params.use_traits,
            use_items=params.use_items,
            use_equipped=False,
            include_star_units=False,
            include_tier_traits=False,
            min_token_freq=params.min_token_freq,
        ),
        exclude=set(),
    )

    X, kept_features, base_counts, _feature_rows = build_sparse_feature_matrix(engine, base, base_ids, features)
    if X.shape[1] <= 0 or not kept_features:
        return cluster_like

    cluster_freq = base_counts.astype(np.float32, copy=False) / float(n_base)

    denom = float(global_n) if global_n > 0 else 1.0
    base_freq = np.zeros((len(kept_features),), dtype=np.float32)
    for i, tok in enumerate(kept_features):
        token_id = engine.token_to_id.get(tok)
        stats = engine.tokens.get(token_id) if token_id is not None else None
        base_freq[i] = float(stats.count) / denom if stats is not None else 0.0

    eps = 1e-9
    with np.errstate(divide="ignore", invalid="ignore"):
        lift = np.where(base_freq > eps, cluster_freq / base_freq, 0.0)

    def top_tokens(prefix: str) -> list[dict]:
        idx = [i for i, t in enumerate(kept_features) if t.startswith(prefix)]
        if not idx:
            return []
        idx_sorted = sorted(idx, key=lambda i: float(cluster_freq[i]), reverse=True)[: params.top_k_tokens]
        out: list[dict] = []
        for i in idx_sorted:
            out.append(
                {
                    "token": kept_features[i],
                    "pct": round(float(cluster_freq[i]), 6),
                    "base_pct": round(float(base_freq[i]), 6),
                    "lift": round(float(lift[i]), 6) if base_freq[i] > 0 else None,
                }
            )
        return out

    defining_units: list[dict] = []
    for i, tok in enumerate(kept_features):
        if not tok.startswith("U:"):
            continue
        if base_freq[i] <= 0.01:
            continue
        if cluster_freq[i] <= 0.3:
            continue
        if lift[i] <= 2.0:
            continue
        defining_units.append(
            {
                "token": tok,
                "pct": round(float(cluster_freq[i]), 6),
                "base_pct": round(float(base_freq[i]), 6),
                "lift": round(float(lift[i]), 6),
            }
        )
    defining_units.sort(key=lambda d: float(d["lift"]), reverse=True)
    defining_units = defining_units[:5]

    signature = _signature_tokens(kept_features, cluster_freq, base_freq)

    cluster_like["defining_units"] = defining_units
    cluster_like["top_units"] = top_tokens("U:")
    cluster_like["top_traits"] = top_tokens("T:")
    cluster_like["top_items"] = top_tokens("I:")
    cluster_like["signature_tokens"] = signature
    return cluster_like


def compute_token_playbook(
    engine: GraphEngine,
    tokens: list[str],
    params: TokenReportParams,
    *,
    min_with: int = 30,
    min_without: int = 30,
    max_drivers: int = 12,
    max_killers: int = 12,
) -> dict:
    """
    Compute a "playbook" for an arbitrary token filter (treating the filtered set
    as the "cluster").

    This powers first-class meta comps exploration without running k-means.
    """
    raw_tokens = [t.strip() for t in tokens if t and t.strip()]
    include_tokens: list[str] = []
    exclude_tokens: list[str] = []
    for t in raw_tokens:
        if t.startswith("-") or t.startswith("!"):
            raw = t.lstrip("-!")
            if raw:
                exclude_tokens.append(raw)
        else:
            include_tokens.append(t)

    had_include = bool(include_tokens)
    include_tokens, exclude_tokens, meta = _sanitize_report_tokens(engine, include_tokens, exclude_tokens)

    canonical_include = tuple(sorted(include_tokens))
    canonical_exclude = tuple(sorted(exclude_tokens))
    token_list = list(canonical_include) + [f"-{t}" for t in canonical_exclude]
    run_id = _run_id_token_report(canonical_include, canonical_exclude, params)

    if had_include and not canonical_include:
        base = BitMap()
    else:
        base = engine.filter_bitmap(list(canonical_include), list(canonical_exclude))
    n_base = int(len(base))
    if n_base <= 0:
        return {
            "run_id": run_id,
            "tokens": token_list,
            "meta": meta,
            "cluster": {
                "cluster_id": 0,
                "run_id": run_id,
                "size": 0,
                "share": 0.0,
                "avg_placement": 4.5,
                "delta_vs_base": 0.0,
                "placement_hist": [0] * 8,
                "win_rate": 0.0,
                "top4_rate": 0.0,
                "bot4_rate": 0.0,
                "eighth_rate": 0.0,
                "defining_units": [],
                "top_units": [],
                "top_traits": [],
                "top_items": [],
                "signature_tokens": [],
            },
            "playbook": {
                "run_id": run_id,
                "cluster_id": 0,
                "meta": {"warning": "No matches for the current filters."},
                "drivers": [],
                "killers": [],
            },
        }

    base_ids = np.array(base.to_array(), dtype=np.int64)
    cluster = _token_cluster_summary(engine, base, base_ids, params, run_id)

    cluster_ids = base_ids
    placements = engine.placements[cluster_ids].astype(np.int16, copy=False)
    placements = np.clip(placements, 0, 8)
    n = int(placements.size)
    if n <= 0:
        return {
            "run_id": run_id,
            "tokens": token_list,
            "meta": meta,
            "cluster": cluster,
            "playbook": {
                "run_id": run_id,
                "cluster_id": 0,
                "meta": {"warning": "Empty selection."},
                "drivers": [],
                "killers": [],
            },
        }

    sum_places = int(placements.sum())
    win_count = int((placements == 1).sum())
    top4_count = int((placements <= 4).sum())
    eighth_count = int((placements == 8).sum())

    base_avg = float(sum_places / n)
    base_win = float(win_count / n)
    base_top4 = float(top4_count / n)
    base_eighth = float(eighth_count / n)

    prior_w = float(max(25, min(200, int(n * 0.06))))

    def shrink_rate(successes: int, trials: int, prior_rate: float) -> float:
        if trials <= 0:
            return prior_rate
        return (successes + prior_rate * prior_w) / (trials + prior_w)

    def shrink_avg(avg: float, trials: int, prior_mean: float) -> float:
        if trials <= 0:
            return prior_mean
        return (avg * trials + prior_mean * prior_w) / (trials + prior_w)

    candidates: list[str] = []

    def add_candidate(tok: str) -> None:
        if not tok:
            return
        if tok not in engine.token_to_id:
            return
        candidates.append(tok)

    sig = cluster.get("signature_tokens") or []
    top_units = [t.get("token") for t in (cluster.get("top_units") or []) if isinstance(t, dict)]
    top_traits = [t.get("token") for t in (cluster.get("top_traits") or []) if isinstance(t, dict)]
    top_items = [t.get("token") for t in (cluster.get("top_items") or []) if isinstance(t, dict)]
    defining_units = [t.get("token") for t in (cluster.get("defining_units") or []) if isinstance(t, dict)]

    sig_units = [t for t in sig if isinstance(t, str) and t.startswith("U:")]
    sig_traits = [t for t in sig if isinstance(t, str) and t.startswith("T:")]
    sig_items = [t for t in sig if isinstance(t, str) and t.startswith("I:")]

    units = [*(sig_units[:6]), *(defining_units[:6]), *(top_units[:12])]
    traits = [*(sig_traits[:6]), *(top_traits[:10])]
    items = [*(sig_items[:8]), *(top_items[:12])]

    for tok in units:
        add_candidate(tok)
    for tok in items:
        add_candidate(tok)

    trait_ids: set[str] = set()
    for t in traits:
        if not isinstance(t, str) or not t.startswith("T:"):
            continue
        trait_ids.add(t[2:].split(":", 1)[0])
    for trait_id in trait_ids:
        prefix = f"T:{trait_id}:"
        for tok in engine.id_to_token:
            if tok.startswith(prefix):
                add_candidate(tok)

    unit_ids: set[str] = set()
    for u in units:
        if not isinstance(u, str) or not u.startswith("U:"):
            continue
        unit_ids.add(u[2:].split(":", 1)[0])
    for unit_id in unit_ids:
        for stars in (2, 3):
            add_candidate(f"U:{unit_id}:{stars}")

    equipped_units = []
    for u in sig_units:
        unit_id = u[2:].split(":", 1)[0]
        if unit_id not in equipped_units:
            equipped_units.append(unit_id)
        if len(equipped_units) >= 3:
            break
    if not equipped_units:
        for u in (defining_units[:3] or top_units[:3]):
            if not isinstance(u, str) or not u.startswith("U:"):
                continue
            unit_id = u[2:].split(":", 1)[0]
            if unit_id not in equipped_units:
                equipped_units.append(unit_id)
            if len(equipped_units) >= 3:
                break
    for unit_id in equipped_units:
        prefix = f"E:{unit_id}|"
        for tok in engine.id_to_token:
            if tok.startswith(prefix):
                add_candidate(tok)

    seen: set[str] = set()
    deduped: list[str] = []
    for tok in candidates:
        if tok in seen:
            continue
        seen.add(tok)
        deduped.append(tok)

    rows: list[dict] = []
    for tok in deduped:
        token_id = engine.token_to_id.get(tok)
        if token_id is None:
            continue
        stats = engine.tokens.get(token_id)
        if stats is None:
            continue

        with_bm = base & stats.bitmap
        n_with = int(len(with_bm))
        n_without = n - n_with

        if n_with < min_with or n_without < min_without:
            continue

        with_ids = np.array(with_bm.to_array(), dtype=np.int64)
        with_places = engine.placements[with_ids].astype(np.int16, copy=False)
        with_places = np.clip(with_places, 0, 8)

        with_sum = int(with_places.sum())
        with_win = int((with_places == 1).sum())
        with_top4 = int((with_places <= 4).sum())
        with_eighth = int((with_places == 8).sum())

        without_sum = sum_places - with_sum
        without_win = win_count - with_win
        without_top4 = top4_count - with_top4
        without_eighth = eighth_count - with_eighth

        with_avg_raw = float(with_sum / n_with) if n_with > 0 else base_avg
        without_avg_raw = float(without_sum / n_without) if n_without > 0 else base_avg

        with_win_adj = shrink_rate(with_win, n_with, base_win)
        without_win_adj = shrink_rate(without_win, n_without, base_win)
        with_top4_adj = shrink_rate(with_top4, n_with, base_top4)
        without_top4_adj = shrink_rate(without_top4, n_without, base_top4)
        with_eighth_adj = shrink_rate(with_eighth, n_with, base_eighth)
        without_eighth_adj = shrink_rate(without_eighth, n_without, base_eighth)

        with_avg_adj = shrink_avg(with_avg_raw, n_with, base_avg)
        without_avg_adj = shrink_avg(without_avg_raw, n_without, base_avg)

        rows.append(
            {
                "token": tok,
                "type": "equipped" if tok.startswith("E:") else "unit" if tok.startswith("U:") else "trait" if tok.startswith("T:") else "item" if tok.startswith("I:") else "unknown",
                "n_with": n_with,
                "pct_in_cluster": round(n_with / float(n), 6),
                "delta_win": round(with_win_adj - without_win_adj, 6),
                "delta_top4": round(with_top4_adj - without_top4_adj, 6),
                "delta_eighth": round(with_eighth_adj - without_eighth_adj, 6),
                "delta_avg": round(with_avg_adj - without_avg_adj, 4),
                "win_with": round(with_win_adj, 6),
                "win_without": round(without_win_adj, 6),
                "top4_with": round(with_top4_adj, 6),
                "top4_without": round(without_top4_adj, 6),
                "avg_with": round(with_avg_adj, 4),
                "avg_without": round(without_avg_adj, 4),
            }
        )

    drivers = sorted(rows, key=lambda r: (r["delta_win"], r["delta_top4"], -r["delta_avg"]), reverse=True)
    killers = sorted(rows, key=lambda r: (r["delta_win"], r["delta_top4"], -r["delta_avg"]))

    def _bm_count(tok: str) -> int:
        token_id = engine.token_to_id.get(tok)
        if token_id is None:
            return 0
        stats = engine.tokens.get(token_id)
        if stats is None:
            return 0
        return int(len(base & stats.bitmap))

    trait_order: list[str] = []
    seen_traits: set[str] = set()
    for t in [*sig_traits, *top_traits]:
        if not isinstance(t, str) or not t.startswith("T:"):
            continue
        trait_id = t[2:].split(":", 1)[0]
        if not trait_id or trait_id in seen_traits:
            continue
        seen_traits.add(trait_id)
        trait_order.append(trait_id)

    trait_summaries: list[dict] = []
    for trait_id in trait_order:
        base_tok = f"T:{trait_id}"
        n_ge_1 = _bm_count(base_tok)
        if n_ge_1 <= 0:
            continue

        max_tier = 1
        n_ge: dict[int, int] = {1: n_ge_1}
        for tier in range(2, 10):
            tok = f"T:{trait_id}:{tier}"
            c = _bm_count(tok)
            if c <= 0:
                continue
            n_ge[tier] = c
            max_tier = max(max_tier, tier)

        n_exact: dict[int, int] = {}
        for tier in range(1, max_tier + 1):
            ge = int(n_ge.get(tier, 0))
            ge_next = int(n_ge.get(tier + 1, 0)) if tier < max_tier else 0
            n_exact[tier] = max(0, ge - ge_next)

        active_tier = max(n_exact.keys(), key=lambda t: (n_exact[t], t))
        active_tok = base_tok if active_tier == 1 else f"T:{trait_id}:{active_tier}"

        levels: list[dict] = []
        for tier in range(1, max_tier + 1):
            tok = base_tok if tier == 1 else f"T:{trait_id}:{tier}"
            n_at_least = int(n_ge.get(tier, 0))
            n_at_exact = int(n_exact.get(tier, 0))
            levels.append(
                {
                    "tier": tier,
                    "token": tok,
                    "label": engine.get_label(tok) or tok[2:],
                    "n_at_least": n_at_least,
                    "pct_at_least": round(n_at_least / float(n), 6) if n > 0 else 0.0,
                    "n_exact": n_at_exact,
                    "pct_exact": round(n_at_exact / float(n), 6) if n > 0 else 0.0,
                }
            )

        active_level = next((lv for lv in levels if lv.get("token") == active_tok), None) or {}
        trait_summaries.append(
            {
                "trait_id": trait_id,
                "token": base_tok,
                "label": engine.get_label(base_tok) or trait_id,
                "n": int(n_ge_1),
                "pct": round(n_ge_1 / float(n), 6) if n > 0 else 0.0,
                "active_tier": int(active_tier),
                "active_token": active_tok,
                "active_label": active_level.get("label") or (engine.get_label(active_tok) or trait_id),
                "active_pct_at_least": float(active_level.get("pct_at_least") or 0.0),
                "active_pct_exact": float(active_level.get("pct_exact") or 0.0),
                "levels": levels,
            }
        )

    item_order: list[str] = []
    seen_items: set[str] = set()
    for t in [*sig_items, *top_items]:
        if not isinstance(t, str) or not t.startswith("I:"):
            continue
        item_id = t[2:].split(":", 1)[0]
        if not item_id or item_id in seen_items:
            continue
        seen_items.add(item_id)
        item_order.append(item_id)

    unit_order: list[str] = []
    seen_units: set[str] = set()
    for t in [*sig_units, *defining_units, *top_units]:
        if not isinstance(t, str) or not t.startswith("U:"):
            continue
        unit_id = t[2:].split(":", 1)[0]
        if not unit_id or unit_id in seen_units:
            continue
        seen_units.add(unit_id)
        unit_order.append(unit_id)
        if len(unit_order) >= 12:
            break

    item_summaries: list[dict] = []
    best_items_by_unit: dict[str, list[dict]] = {}
    for item_id in item_order[:8]:
        item_tok = f"I:{item_id}"
        holders: list[dict] = []
        for unit_id in unit_order:
            eq_tok = f"E:{unit_id}|{item_id}"
            n_with = _bm_count(eq_tok)
            if n_with <= 0:
                continue
            holders.append(
                {
                    "unit": unit_id,
                    "token": eq_tok,
                    "n_with": int(n_with),
                    "pct_in_cluster": round(n_with / float(n), 6) if n > 0 else 0.0,
                }
            )

        holders.sort(key=lambda h: (-(h.get("pct_in_cluster") or 0.0), -(h.get("n_with") or 0)))
        best = holders[0] if holders else None
        if best:
            best_items_by_unit.setdefault(best["unit"], []).append(
                {
                    "item": item_id,
                    "token": best["token"],
                    "n_with": best["n_with"],
                    "pct_in_cluster": best["pct_in_cluster"],
                }
            )

        item_summaries.append(
            {
                "item": item_id,
                "token": item_tok,
                "label": engine.get_label(item_tok) or item_id,
                "best_holder": best,
                "top_holders": holders[:3],
            }
        )

    for unit_id, items_for_unit in best_items_by_unit.items():
        items_for_unit.sort(key=lambda r: (-(r.get("pct_in_cluster") or 0.0), r.get("item") or ""))
        best_items_by_unit[unit_id] = items_for_unit[:3]

    playbook = {
        "run_id": run_id,
        "cluster_id": 0,
        "base": {
            "n": int(n),
            "avg_placement": round(base_avg, 4),
            "win_rate": round(base_win, 6),
            "top4_rate": round(base_top4, 6),
            "eighth_rate": round(base_eighth, 6),
        },
        "drivers": drivers[: max_drivers],
        "killers": killers[: max_killers],
        "comp_view": {
            "traits": trait_summaries,
            "items": item_summaries,
            "best_items_by_unit": best_items_by_unit,
        },
        "meta": {
            "prior_weight": prior_w,
            "candidates_scored": len(rows),
        },
    }

    return {
        "run_id": run_id,
        "tokens": token_list,
        "meta": meta,
        "cluster": cluster,
        "playbook": playbook,
    }
