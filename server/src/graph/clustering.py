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
import threading
import time

import numpy as np
from pyroaring import BitMap

from .engine import GraphEngine


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
class _CacheKey:
    tokens: tuple[str, ...]
    params: ClusterParams


_CACHE_LOCK = threading.Lock()
_CACHE: OrderedDict[_CacheKey, tuple[float, dict]] = OrderedDict()
_CACHE_MAX = 24
_CACHE_TTL_S = 10 * 60


def _cache_get(key: _CacheKey) -> dict | None:
    now = time.time()
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
        if entry is None:
            return None
        ts, value = entry
        if (now - ts) > _CACHE_TTL_S:
            _CACHE.pop(key, None)
            return None
        _CACHE.move_to_end(key, last=True)
        return value


def _cache_set(key: _CacheKey, value: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = (time.time(), value)
        _CACHE.move_to_end(key, last=True)
        while len(_CACHE) > _CACHE_MAX:
            _CACHE.popitem(last=False)


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
    features: list[str] = []
    if params.use_units:
        # Only include base unit tokens (U:Unit). Star-level unit tokens (U:Unit:2)
        # are filter-only and would otherwise bloat/fragment cluster signatures.
        features.extend(
            t for t in engine.id_to_token if t.startswith("U:") and t.count(":") == 1
        )
    if params.use_traits:
        features.extend(
            t
            for t in engine.id_to_token
            if t.startswith("T:") and t.count(":") == 1
        )
    if params.use_items:
        features.extend(t for t in engine.id_to_token if t.startswith("I:"))

    # Filter by global frequency (keeps feature space stable across queries)
    filtered: list[str] = []
    for t in features:
        token_id = engine.token_to_id.get(t)
        if token_id is None:
            continue
        stats = engine.tokens.get(token_id)
        if stats is None:
            continue
        if stats.count >= params.min_token_freq:
            filtered.append(t)

    return filtered


def _build_sparse_feature_matrix(
    engine: GraphEngine,
    base: BitMap,
    base_ids: np.ndarray,
    features: list[str],
) -> tuple["csr_matrix", list[str], np.ndarray, list[np.ndarray]]:
    """
    Build a CSR sparse matrix X where rows are player_match_ids in `base_ids`
    and columns are feature tokens. X[i, j] = 1 if pm_id includes token.

    Returns:
        X: csr_matrix[int8] shape (n_rows, n_features_kept)
        kept_features: list[str]
        base_counts: np.ndarray[int32] (#base rows with each feature)
        feature_rows: list[np.ndarray[int32]] (row indices for each feature)
    """
    from scipy.sparse import csr_matrix

    n_rows = int(base_ids.size)
    if n_rows == 0 or not features:
        return csr_matrix((n_rows, 0), dtype=np.int8), [], np.zeros((0,), dtype=np.int32), []

    row_blocks: list[np.ndarray] = []
    col_blocks: list[np.ndarray] = []

    kept_features: list[str] = []
    base_counts: list[int] = []
    feature_rows: list[np.ndarray] = []

    col_idx = 0
    for token in features:
        token_id = engine.token_to_id.get(token)
        if token_id is None:
            continue
        token_stats = engine.tokens.get(token_id)
        if token_stats is None:
            continue

        ids_list = (base & token_stats.bitmap).to_array()
        if not ids_list:
            continue

        ids = np.array(ids_list, dtype=np.int64)
        rows = np.searchsorted(base_ids, ids).astype(np.int32, copy=False)

        kept_features.append(token)
        base_counts.append(int(rows.size))
        feature_rows.append(rows)

        row_blocks.append(rows)
        col_blocks.append(np.full(rows.shape, col_idx, dtype=np.int32))
        col_idx += 1

    n_cols = len(kept_features)
    if n_cols == 0:
        return csr_matrix((n_rows, 0), dtype=np.int8), [], np.zeros((0,), dtype=np.int32), []

    row = np.concatenate(row_blocks)
    col = np.concatenate(col_blocks)
    data = np.ones(row.shape[0], dtype=np.int8)

    X = csr_matrix((data, (row, col)), shape=(n_rows, n_cols), dtype=np.int8)
    return X, kept_features, np.array(base_counts, dtype=np.int32), feature_rows


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
    canonical_tokens = tuple(sorted(t.strip() for t in tokens if t.strip()))
    key = _CacheKey(tokens=canonical_tokens, params=params)

    cached = _cache_get(key)
    if cached is not None:
        return {**cached, "cached": True}

    t0 = time.perf_counter()

    base = engine.intersect(list(canonical_tokens))
    n_base = len(base)
    base_ids = np.array(base.to_array(), dtype=np.int64)
    base_avg = float(engine.avg_placement_for_bitmap(base)) if n_base > 0 else 4.5
    base_hist = _placement_hist(engine, base_ids)
    base_rates = _rates_from_hist(base_hist)

    result: dict = {
        "cached": False,
        "tokens": list(canonical_tokens),
        "base": {
            "n": int(n_base),
            "avg_placement": round(base_avg, 4),
            "placement_hist": base_hist,
            **{k: round(v, 6) for k, v in base_rates.items()},
        },
        "params": asdict(params),
        "clusters": [],
        "meta": {},
    }

    if n_base == 0:
        result["meta"] = {"warning": "No matches for the current filters."}
        _cache_set(key, result)
        return result

    if n_base < max(params.min_cluster_size * 2, params.n_clusters * 3):
        result["meta"] = {
            "warning": "Sample too small to cluster reliably. Try fewer filters or a smaller min cluster size."
        }
        _cache_set(key, result)
        return result

    features = _select_features(engine, params)
    X, kept_features, base_counts, feature_rows = _build_sparse_feature_matrix(
        engine, base, base_ids, features
    )

    if X.shape[1] < 2:
        result["meta"] = {
            "warning": "Not enough features in this sample (after filtering). Lower min token freq or broaden the sample."
        }
        _cache_set(key, result)
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
    for c in range(params.n_clusters):
        size = int(cluster_sizes[c])
        if size < params.min_cluster_size:
            continue

        row_idx = np.flatnonzero(labels == c)
        pm_ids = base_ids[row_idx]

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
        "features_requested": len(features),
        "features_used": int(X.shape[1]),
        "inertia": round(float(kmeans.inertia_), 6) if hasattr(kmeans, "inertia_") else None,
        "compute_ms": int((time.perf_counter() - t0) * 1000),
    }

    _cache_set(key, result)
    return result
