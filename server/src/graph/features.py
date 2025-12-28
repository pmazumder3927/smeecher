"""
Sparse feature extraction utilities over the GraphEngine token index.

These are shared by clustering and causal estimation code paths.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from pyroaring import BitMap

from .engine import GraphEngine


@dataclass(frozen=True, slots=True)
class TokenFeatureParams:
    use_units: bool = True
    use_traits: bool = True
    use_items: bool = True
    use_equipped: bool = False
    include_star_units: bool = False
    include_tier_traits: bool = True
    min_token_freq: int = 1


def select_feature_tokens(
    engine: GraphEngine,
    params: TokenFeatureParams,
    *,
    exclude: set[str] | None = None,
) -> list[str]:
    exclude = exclude or set()
    features: list[str] = []

    if params.use_units:
        for t in engine.id_to_token:
            if not t.startswith("U:"):
                continue
            # Star-level unit tokens are encoded as U:UnitName:2.
            star_level = t.count(":") == 2
            if star_level and not params.include_star_units:
                continue
            if (not star_level) and t.count(":") != 1:
                continue
            features.append(t)

    if params.use_traits:
        for t in engine.id_to_token:
            if not t.startswith("T:"):
                continue
            tiered = t.count(":") == 2
            if tiered and not params.include_tier_traits:
                continue
            if (not tiered) and t.count(":") != 1:
                continue
            features.append(t)

    if params.use_items:
        features.extend(t for t in engine.id_to_token if t.startswith("I:"))

    if params.use_equipped:
        features.extend(t for t in engine.id_to_token if t.startswith("E:"))

    filtered: list[str] = []
    for t in features:
        if t in exclude:
            continue
        token_id = engine.token_to_id.get(t)
        if token_id is None:
            continue
        stats = engine.tokens.get(token_id)
        if stats is None:
            continue
        if stats.count >= params.min_token_freq:
            filtered.append(t)

    return filtered


def build_sparse_feature_matrix(
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


def board_strength_features(engine: GraphEngine, ids: np.ndarray) -> np.ndarray:
    """
    Return numeric "board strength proxy" features aligned to `ids`.

    Shape: (n_rows, 7)
    """
    n = int(ids.size)
    if n == 0:
        return np.zeros((0, 7), dtype=np.float32)

    def take(arr: np.ndarray | None, dtype: np.dtype) -> np.ndarray:
        if arr is None:
            return np.zeros((n,), dtype=dtype)
        return arr[ids].astype(dtype, copy=False)

    return np.stack(
        [
            take(engine.item_count, np.float32),
            take(engine.component_count, np.float32),
            take(engine.completed_item_count, np.float32),
            take(engine.unit_count, np.float32),
            take(engine.two_star_count, np.float32),
            take(engine.three_star_count, np.float32),
            take(engine.unit_gold_value, np.float32),
        ],
        axis=1,
    )

