"""
Ultra-optimized graph engine for TFT match analysis.

Designed to scale to millions of games with:
- Roaring bitmaps for O(1) set operations on sparse integer sets
- NumPy int8 arrays for placement data (1-8 fits in a byte)
- Precomputed aggregates (sum/count) per token - no iteration needed
- Integer token IDs instead of string keys
- Single SQL JOIN query for index building
- Memory-mapped binary serialization
"""
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import orjson
from pyroaring import BitMap

from .items import get_item_type

# Type aliases
TokenId = int
PlayerId = int  # player_match_id


@dataclass(slots=True)
class TokenStats:
    """Precomputed stats for a token - enables O(1) average calculation."""
    bitmap: BitMap           # Set of player_match_ids
    placement_sum: int       # Sum of all placements for quick avg
    count: int               # Number of matches (redundant with len(bitmap) but faster)

    @property
    def avg_placement(self) -> float:
        return self.placement_sum / self.count if self.count > 0 else 4.5


class GraphEngine:
    """
    High-performance graph query engine using roaring bitmaps.

    Memory layout:
    - placements: np.ndarray[int8] indexed by player_match_id
    - tokens: dict[TokenId, TokenStats] with roaring bitmaps
    - token_to_id: dict[str, TokenId] for string -> int mapping
    - id_to_token: list[str] for int -> string mapping
    - labels: dict[TokenId, str] for display names
    """

    __slots__ = (
        'placements',
        'tokens',
        'token_to_id',
        'id_to_token',
        'labels',
        'total_matches',
        'all_players',
        # Board-strength proxies (indexed by player_match_id)
        'item_count',
        'component_count',
        'completed_item_count',
        'unit_count',
        'two_star_count',
        'three_star_count',
        'unit_gold_value',
        # Precomputed causal "necessity" cache (engine.bin v3+)
        'necessity_top4_ready',
        'necessity_top4_tau',
        'necessity_top4_ci95_low',
        'necessity_top4_ci95_high',
        'necessity_top4_se',
        'necessity_top4_raw_tau',
        'necessity_top4_frac_trimmed',
        'necessity_top4_e_p01',
        'necessity_top4_e_p99',
        'necessity_top4_n_treated',
        'necessity_top4_n_control',
        'necessity_top4_n_used',
        'necessity_top4_scope_min_star',
    )

    def __init__(self):
        self.placements: np.ndarray = None  # int8 array
        self.tokens: dict[TokenId, TokenStats] = {}
        self.token_to_id: dict[str, TokenId] = {}
        self.id_to_token: list[str] = []
        self.labels: dict[TokenId, str] = {}
        self.total_matches: int = 0
        self.all_players: BitMap = BitMap()  # All player IDs for empty queries
        self.item_count: np.ndarray | None = None
        self.component_count: np.ndarray | None = None
        self.completed_item_count: np.ndarray | None = None
        self.unit_count: np.ndarray | None = None
        self.two_star_count: np.ndarray | None = None
        self.three_star_count: np.ndarray | None = None
        self.unit_gold_value: np.ndarray | None = None
        self.necessity_top4_ready: bool = False
        self.necessity_top4_tau: np.ndarray | None = None
        self.necessity_top4_ci95_low: np.ndarray | None = None
        self.necessity_top4_ci95_high: np.ndarray | None = None
        self.necessity_top4_se: np.ndarray | None = None
        self.necessity_top4_raw_tau: np.ndarray | None = None
        self.necessity_top4_frac_trimmed: np.ndarray | None = None
        self.necessity_top4_e_p01: np.ndarray | None = None
        self.necessity_top4_e_p99: np.ndarray | None = None
        self.necessity_top4_n_treated: np.ndarray | None = None
        self.necessity_top4_n_control: np.ndarray | None = None
        self.necessity_top4_n_used: np.ndarray | None = None
        self.necessity_top4_scope_min_star: np.ndarray | None = None

    def init_necessity_cache_top4(self) -> None:
        """Initialize in-memory arrays for the top4 necessity cache (engine.bin v3+)."""
        n_tokens = len(self.id_to_token)
        self.necessity_top4_tau = np.full((n_tokens,), np.nan, dtype=np.float32)
        self.necessity_top4_ci95_low = np.full((n_tokens,), np.nan, dtype=np.float32)
        self.necessity_top4_ci95_high = np.full((n_tokens,), np.nan, dtype=np.float32)
        self.necessity_top4_se = np.full((n_tokens,), np.nan, dtype=np.float32)
        self.necessity_top4_raw_tau = np.full((n_tokens,), np.nan, dtype=np.float32)
        self.necessity_top4_frac_trimmed = np.full((n_tokens,), np.nan, dtype=np.float32)
        self.necessity_top4_e_p01 = np.full((n_tokens,), np.nan, dtype=np.float32)
        self.necessity_top4_e_p99 = np.full((n_tokens,), np.nan, dtype=np.float32)
        self.necessity_top4_n_treated = np.zeros((n_tokens,), dtype=np.int32)
        self.necessity_top4_n_control = np.zeros((n_tokens,), dtype=np.int32)
        self.necessity_top4_n_used = np.zeros((n_tokens,), dtype=np.int32)
        # 0 = unknown/unset; 1..6 = min star level for unit scope used to compute the estimate.
        self.necessity_top4_scope_min_star = np.zeros((n_tokens,), dtype=np.uint8)

    def precompute_necessity_cache_top4(
        self,
        *,
        min_token_freq: int = 25,
        min_group: int = 100,
        overlap_min: float = 0.05,
        overlap_max: float = 0.95,
        n_splits: int = 2,
        max_rows_per_unit: int | None = 80_000,
        auto_unit_stars_min: bool = True,
        star2plus_share_threshold: float = 0.7,
        star2plus_min_rows: int = 2000,
        max_units: int | None = None,
        only_units: list[str] | None = None,
    ) -> None:
        """
        Precompute AIPW ΔTop4 "necessity" for each equipped token E:Unit|Item.

        This cache is intended to make the default "Most necessary" unit-items view fast
        without fitting thousands of causal models at request time.

        Notes:
          - Effects are computed in a base context of "unit present" (optionally restricted
            to 2★+ when 2★+ dominates the sample).
          - Only units/traits are used as token features (no items/equipped in X).
          - Board-strength proxies are adjusted to use rest-of-board item counts
            (subtracting all items equipped on the unit).
        """
        from .causal import AIPWConfig, OverlapError, aipw_ate, placements_to_outcome
        from .features import (
            TokenFeatureParams,
            build_sparse_feature_matrix,
            board_strength_features,
            select_feature_tokens,
        )

        from scipy.sparse import csr_matrix, hstack

        if self.placements is None or not self.id_to_token:
            raise ValueError("Engine must be built/loaded before precomputing necessity cache")

        self.init_necessity_cache_top4()

        # Global feature pool (filtered by global frequency); exclude the current unit per-scope.
        feature_pool = select_feature_tokens(
            self,
            TokenFeatureParams(
                use_units=True,
                use_traits=True,
                use_items=False,
                use_equipped=False,
                include_star_units=False,
                include_tier_traits=True,
                min_token_freq=min_token_freq,
            ),
            exclude=set(),
        )

        rng = np.random.default_rng(42)
        unit_tokens = [t for t in self.id_to_token if t.startswith("U:") and t.count(":") == 1]
        if only_units:
            allowed = {u.strip() for u in only_units if u and u.strip()}
            unit_tokens = [t for t in unit_tokens if t[2:] in allowed]
        unit_tokens.sort()
        if max_units is not None:
            unit_tokens = unit_tokens[: int(max_units)]

        total_units = len(unit_tokens)
        for i, unit_token in enumerate(unit_tokens, start=1):
            unit = unit_token[2:]
            base_bitmap_full = self.filter_bitmap([unit_token], [])
            if not base_bitmap_full:
                continue

            scope_min_star = 1
            star2plus_bm = BitMap()
            if auto_unit_stars_min:
                for s in range(2, 7):
                    star_tok = f"U:{unit}:{s}"
                    token_id = self.token_to_id.get(star_tok)
                    if token_id is None:
                        continue
                    stats = self.tokens.get(token_id)
                    if stats is None:
                        continue
                    star2plus_bm |= stats.bitmap

                if star2plus_bm:
                    n_all = len(base_bitmap_full)
                    n_2p = len(base_bitmap_full & star2plus_bm)
                    if n_all > 0 and n_2p >= star2plus_min_rows and (n_2p / float(n_all)) >= star2plus_share_threshold:
                        scope_min_star = 2

            if scope_min_star >= 2 and star2plus_bm:
                base_bitmap_full &= star2plus_bm

            n_base_full = int(len(base_bitmap_full))
            if n_base_full < max(500, min_group * 3):
                continue

            base_ids_full = np.array(base_bitmap_full.to_array(), dtype=np.int64)
            base_bitmap_model = base_bitmap_full
            base_ids = base_ids_full
            if max_rows_per_unit is not None and base_ids_full.size > int(max_rows_per_unit):
                sel = rng.choice(base_ids_full.size, size=int(max_rows_per_unit), replace=False)
                base_ids = np.sort(base_ids_full[sel])
                base_bitmap_model = BitMap(base_ids)

            placements = self.placements[base_ids].astype(np.int16, copy=False)
            y, kind = placements_to_outcome(placements, "top4")

            feature_tokens = [t for t in feature_pool if t != unit_token]
            X_tok, _, _, _ = build_sparse_feature_matrix(self, base_bitmap_model, base_ids, feature_tokens)

            # Board-strength proxy features (rest-of-board counts).
            Z_full = board_strength_features(self, base_ids).astype(np.float32, copy=False)
            unit_item_count = np.zeros((base_ids.size,), dtype=np.float32)
            unit_component_count = np.zeros((base_ids.size,), dtype=np.float32)
            unit_completed_count = np.zeros((base_ids.size,), dtype=np.float32)

            prefix = f"E:{unit}|"
            equipped_tokens = [t for t in self.id_to_token if t.startswith(prefix)]
            equipped_token_ids: list[int] = []
            for eq_tok in equipped_tokens:
                # Parse optional copy-count suffix: E:Unit|Item:2 / :3
                item_part = eq_tok.split("|", 1)[1] if "|" in eq_tok else ""
                item_name = item_part
                copies = 1
                if ":" in item_part:
                    base, maybe_copies = item_part.rsplit(":", 1)
                    try:
                        c = int(maybe_copies)
                    except ValueError:
                        c = None
                    if c is not None and c >= 2:
                        copies = c
                        item_name = base

                tok_id = self.token_to_id.get(eq_tok)
                tok_stats = self.tokens.get(tok_id) if tok_id is not None else None
                if tok_id is None or tok_stats is None:
                    continue
                # Only compute AIPW τ for the base ">=1 copy" equipped token.
                if copies == 1:
                    equipped_token_ids.append(int(tok_id))

                bm = base_bitmap_model & tok_stats.bitmap
                if not bm:
                    continue
                ids = np.array(bm.to_array(), dtype=np.int64)
                rows = np.searchsorted(base_ids, ids).astype(np.int64, copy=False)
                unit_item_count[rows] += 1.0

                if get_item_type(item_name) == "component":
                    unit_component_count[rows] += 1.0
                else:
                    unit_completed_count[rows] += 1.0

            other_item_count = np.clip(Z_full[:, 0] - unit_item_count, 0.0, None)
            other_component_count = np.clip(Z_full[:, 1] - unit_component_count, 0.0, None)
            other_completed_count = np.clip(Z_full[:, 2] - unit_completed_count, 0.0, None)
            Z = np.stack(
                [
                    other_item_count,
                    other_component_count,
                    other_completed_count,
                    Z_full[:, 3],
                    Z_full[:, 4],
                    Z_full[:, 5],
                    Z_full[:, 6],
                ],
                axis=1,
            )
            X = hstack([X_tok.astype(np.float32), csr_matrix(Z, dtype=np.float32)], format="csr")

            cfg = AIPWConfig(
                n_splits=n_splits,
                random_state=42,
                clip_eps=0.01,
                trim_low=overlap_min,
                trim_high=overlap_max,
            )

            computed = 0
            for token_id in equipped_token_ids:
                tok_stats = self.tokens.get(token_id)
                if tok_stats is None:
                    continue

                treated_bm_full = base_bitmap_full & tok_stats.bitmap
                n_treated_full = int(len(treated_bm_full))
                n_control_full = int(n_base_full - n_treated_full)
                if n_treated_full < min_group or n_control_full < min_group:
                    continue

                treated_bm = base_bitmap_model & tok_stats.bitmap
                treated_ids = np.array(treated_bm.to_array(), dtype=np.int64)
                T = np.zeros((base_ids.size,), dtype=np.int8)
                if treated_ids.size:
                    idxs = np.searchsorted(base_ids, treated_ids).astype(np.int64, copy=False)
                    T[idxs] = 1

                n_treated_model = int(T.sum())
                n_control_model = int(base_ids.size - n_treated_model)
                if n_treated_model < min_group or n_control_model < min_group:
                    continue

                raw_tau = float(y[T == 1].mean() - y[T == 0].mean())
                try:
                    est, _, _, _ = aipw_ate(X, T, y, kind=kind, cfg=cfg)
                except OverlapError:
                    continue
                except Exception:
                    continue

                self.necessity_top4_tau[token_id] = float(est.tau)
                self.necessity_top4_ci95_low[token_id] = float(est.ci95_low)
                self.necessity_top4_ci95_high[token_id] = float(est.ci95_high)
                self.necessity_top4_se[token_id] = float(est.se)
                self.necessity_top4_raw_tau[token_id] = float(raw_tau)
                self.necessity_top4_frac_trimmed[token_id] = float(est.frac_trimmed)
                self.necessity_top4_e_p01[token_id] = float(est.e_p01)
                self.necessity_top4_e_p99[token_id] = float(est.e_p99)
                self.necessity_top4_n_treated[token_id] = int(n_treated_full)
                self.necessity_top4_n_control[token_id] = int(n_control_full)
                self.necessity_top4_n_used[token_id] = int(est.n_used)
                self.necessity_top4_scope_min_star[token_id] = int(scope_min_star)
                computed += 1

            if computed:
                print(f"[{i}/{total_units}] {unit}: computed {computed} necessity estimates (n={n_base_full}, scope>= {scope_min_star}★)")

        self.necessity_top4_ready = bool(np.isfinite(self.necessity_top4_tau).any())

    def _get_or_create_token_id(self, token: str) -> TokenId:
        """Get existing token ID or create new one."""
        if token in self.token_to_id:
            return self.token_to_id[token]
        token_id = len(self.id_to_token)
        self.token_to_id[token] = token_id
        self.id_to_token.append(token)
        return token_id

    def build_from_db(self, db_path: str = "data/smeecher.db") -> None:
        """
        Build index from SQLite using a single JOIN query.

        This replaces the N+1 query pattern with one efficient query
        that streams all data in player_match_id order.
        """
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # Use transaction for consistent snapshot (prevents race with concurrent writes)
        c.execute("BEGIN")

        # Get max player_match_id for array sizing (filter by set 16 to match data queries)
        c.execute("""SELECT MAX(pm.id), COUNT(*) FROM player_matches pm
            JOIN matches m ON m.match_id = pm.match_id
            WHERE m.tft_set_number = 16""")
        row = c.fetchone()
        max_id = row[0] or 0
        self.total_matches = row[1] or 0

        # Allocate placement array (int8 is enough for 1-8)
        self.placements = np.zeros(max_id + 1, dtype=np.int8)
        # Board-strength proxy arrays (kept as small ints; indexed by pm.id)
        self.item_count = np.zeros(max_id + 1, dtype=np.int16)
        self.component_count = np.zeros(max_id + 1, dtype=np.int16)
        self.completed_item_count = np.zeros(max_id + 1, dtype=np.int16)
        self.unit_count = np.zeros(max_id + 1, dtype=np.int16)
        self.two_star_count = np.zeros(max_id + 1, dtype=np.int16)
        self.three_star_count = np.zeros(max_id + 1, dtype=np.int16)
        self.unit_gold_value = np.zeros(max_id + 1, dtype=np.int32)

        # Single JOIN query - streams everything in one pass
        c.execute("""
            SELECT
                pm.id as pm_id,
                pm.placement,
                u.name as unit_name,
                u.tier as unit_tier,
                u.rarity as unit_rarity,
                u.items
            FROM player_matches pm
            JOIN matches m ON m.match_id = pm.match_id
            LEFT JOIN units u ON u.match_id = pm.match_id AND u.puuid = pm.puuid
            WHERE m.tft_set_number = 16
            ORDER BY pm.id
        """)

        # Temporary accumulators: token_id -> (bitmap_builder, placement_sum)
        token_data: dict[TokenId, tuple[list[int], int]] = {}

        current_pm_id = None
        current_placement = None
        board_items: set[str] = set()
        # Board-strength accumulators (reset per pm_id)
        board_item_count = 0
        board_component_count = 0
        board_completed_count = 0
        board_unit_count = 0
        board_two_star = 0
        board_three_star = 0
        board_unit_gold = 0

        def flush_board():
            """Process accumulated board items for current player match."""
            if current_pm_id is None:
                return
            # Store board-strength proxies
            self.item_count[current_pm_id] = board_item_count
            self.component_count[current_pm_id] = board_component_count
            self.completed_item_count[current_pm_id] = board_completed_count
            self.unit_count[current_pm_id] = board_unit_count
            self.two_star_count[current_pm_id] = board_two_star
            self.three_star_count[current_pm_id] = board_three_star
            self.unit_gold_value[current_pm_id] = board_unit_gold

            # Add item presence tokens
            for item_name in board_items:
                item_token = f"I:{item_name}"
                token_id = self._get_or_create_token_id(item_token)
                self.labels[token_id] = item_name

                if token_id not in token_data:
                    token_data[token_id] = ([], 0)
                ids, psum = token_data[token_id]
                ids.append(current_pm_id)
                token_data[token_id] = (ids, psum + current_placement)

        for row in c:
            pm_id = row["pm_id"]
            placement = row["placement"]

            # New player match - flush previous board
            if pm_id != current_pm_id:
                flush_board()
                current_pm_id = pm_id
                current_placement = placement
                board_items = set()
                board_item_count = 0
                board_component_count = 0
                board_completed_count = 0
                board_unit_count = 0
                board_two_star = 0
                board_three_star = 0
                board_unit_gold = 0

                # Store placement
                self.placements[pm_id] = placement
                self.all_players.add(pm_id)

            # Process unit (may be NULL from LEFT JOIN if player has no units)
            unit_name_raw = row["unit_name"]
            if unit_name_raw is None:
                continue

            unit_name = self._clean_unit_name(unit_name_raw)
            unit_tier = row["unit_tier"]
            unit_rarity = row["unit_rarity"]
            items_json = row["items"]
            items = orjson.loads(items_json) if items_json else []

            board_unit_count += 1
            # 2★ / 3★ count proxies
            if isinstance(unit_tier, int) and unit_tier >= 2:
                board_two_star += 1
            if isinstance(unit_tier, int) and unit_tier >= 3:
                board_three_star += 1
            # Gold value proxy: cost * star multiplier (1->1, 2->3, 3->9, ...)
            if isinstance(unit_rarity, int) and unit_rarity >= 0 and isinstance(unit_tier, int) and unit_tier >= 1:
                cost = unit_rarity + 1
                star_multiplier = 3 ** (unit_tier - 1)
                board_unit_gold += int(cost * star_multiplier)

            # Unit presence token
            unit_token = f"U:{unit_name}"
            token_id = self._get_or_create_token_id(unit_token)
            self.labels[token_id] = unit_name

            if token_id not in token_data:
                token_data[token_id] = ([], 0)
            ids, psum = token_data[token_id]
            ids.append(pm_id)
            token_data[token_id] = (ids, psum + placement)

            # Unit star-level token (1★, 2★, 3★, ...) for filtering.
            # Base unit token represents any star level.
            if isinstance(unit_tier, int) and unit_tier >= 1:
                star_token = f"U:{unit_name}:{unit_tier}"
                token_id = self._get_or_create_token_id(star_token)
                self.labels[token_id] = f"{unit_name} {unit_tier}"

                if token_id not in token_data:
                    token_data[token_id] = ([], 0)
                ids, psum = token_data[token_id]
                ids.append(pm_id)
                token_data[token_id] = (ids, psum + placement)

            # Equipped tokens and track board items.
            #
            # Notes:
            # - E:Unit|Item represents "unit has >=1 copy of item".
            # - When duplicates exist, we also emit count tokens:
            #   - E:Unit|Item:2 => unit has >=2 copies
            #   - E:Unit|Item:3 => unit has >=3 copies
            item_counts: dict[str, int] = {}
            for item_id in items:
                item_name = self._clean_item_name(item_id)
                if item_name == "EmptyBag":  # Placeholder for Thief's Gloves random items
                    continue
                board_item_count += 1
                if get_item_type(item_name) == "component":
                    board_component_count += 1
                else:
                    board_completed_count += 1
                board_items.add(item_name)
                item_counts[item_name] = item_counts.get(item_name, 0) + 1

            def _add_equipped_token(token_str: str, label: str) -> None:
                token_id = self._get_or_create_token_id(token_str)
                self.labels[token_id] = label

                if token_id not in token_data:
                    token_data[token_id] = ([], 0)
                ids, psum = token_data[token_id]
                ids.append(pm_id)
                token_data[token_id] = (ids, psum + placement)

            for item_name, n_copies in item_counts.items():
                _add_equipped_token(
                    f"E:{unit_name}|{item_name}",
                    f"{unit_name} + {item_name}",
                )
                if n_copies >= 2:
                    _add_equipped_token(
                        f"E:{unit_name}|{item_name}:2",
                        f"{unit_name} + {item_name} x2",
                    )
                if n_copies >= 3:
                    _add_equipped_token(
                        f"E:{unit_name}|{item_name}:3",
                        f"{unit_name} + {item_name} x3",
                    )

        # Flush last board
        flush_board()

        # Second pass: Process traits from player_matches
        c.execute("""
            SELECT pm.id, pm.placement, pm.traits 
            FROM player_matches pm
            JOIN matches m ON m.match_id = pm.match_id
            WHERE pm.traits IS NOT NULL AND m.tft_set_number = 16
        """)
        # Track trait breakpoints: (trait_name, tier) -> minimum observed num_units.
        # Riot's API reports tiers as an index (1,2,3...) while the in-game UI
        # talks in unit counts (e.g. "Demacia 3"). We infer that mapping from data.
        trait_min_units: dict[tuple[str, int], int] = {}
        trait_tiers_seen: dict[str, set[int]] = {}
        for row in c:
            pm_id = row["id"]
            placement = row["placement"]
            traits_json = row["traits"]

            if not traits_json:
                continue

            traits = orjson.loads(traits_json)
            for trait in traits:
                trait_name = self._clean_trait_name(trait["name"])
                tier = int(trait.get("tier", 1) or 1)
                trait_tiers_seen.setdefault(trait_name, set()).add(tier)
                num_units = trait.get("num_units")
                if isinstance(num_units, int) and num_units > 0:
                    key = (trait_name, tier)
                    prev = trait_min_units.get(key)
                    if prev is None or num_units < prev:
                        trait_min_units[key] = num_units

                # Base trait token (any tier)
                trait_token = f"T:{trait_name}"
                token_id = self._get_or_create_token_id(trait_token)
                self.labels[token_id] = trait_name

                if token_id not in token_data:
                    token_data[token_id] = ([], 0)
                ids, psum = token_data[token_id]
                ids.append(pm_id)
                token_data[token_id] = (ids, psum + placement)

                # Tiered trait tokens: T:Brawler:2 means tier 2+ (inclusive).
                if tier >= 2:
                    for t_level in range(2, tier + 1):
                        tiered_token = f"T:{trait_name}:{t_level}"
                        token_id = self._get_or_create_token_id(tiered_token)
                        self.labels[token_id] = f"{trait_name} {t_level}"

                        if token_id not in token_data:
                            token_data[token_id] = ([], 0)
                        ids, psum = token_data[token_id]
                        ids.append(pm_id)
                        token_data[token_id] = (ids, psum + placement)

        # Update trait labels to show in-game unit breakpoints (e.g. Demacia 3/5/7...),
        # not Riot's tier index (1/2/3...).
        for (trait_name, tier), min_units in trait_min_units.items():
            token = f"T:{trait_name}" if tier == 1 else f"T:{trait_name}:{tier}"
            token_id = self.token_to_id.get(token)
            if token_id is None:
                continue
            # Only drop the breakpoint number when the trait has exactly one
            # breakpoint (e.g. "Chosen Wolves 2" should just be "Chosen Wolves").
            tiers = trait_tiers_seen.get(trait_name) or set()
            if len(tiers) <= 1:
                self.labels[token_id] = trait_name
            else:
                self.labels[token_id] = f"{trait_name} {min_units}"

        conn.close()

        # Convert accumulated data to roaring bitmaps
        for token_id, (ids, psum) in token_data.items():
            # Deduplicate IDs (units can appear multiple times per board)
            unique_ids = np.array(list(set(ids)), dtype=np.int64)
            # Vectorized sum using numpy indexing (much faster than Python loop)
            actual_sum = int(self.placements[unique_ids].astype(np.int32).sum())
            self.tokens[token_id] = TokenStats(
                bitmap=BitMap(unique_ids),
                placement_sum=actual_sum,
                count=len(unique_ids)
            )

    @staticmethod
    def _clean_unit_name(name: str) -> str:
        return name.replace("TFT16_", "").replace("TFT_", "")

    @staticmethod
    def _clean_item_name(item_id: str) -> str:
        return item_id.replace("TFT_Item_", "").replace("TFT16_Item_", "")

    @staticmethod
    def _clean_trait_name(name: str) -> str:
        return name.replace("TFT16_", "").replace("TFT_", "").replace("Set16_", "")

    # ─────────────────────────────────────────────────────────────────
    # Query Methods
    # ─────────────────────────────────────────────────────────────────

    def intersect(self, token_strs: list[str]) -> BitMap:
        """
        Compute intersection of token sets.
        Roaring bitmap intersection is highly optimized in C++.
        """
        if not token_strs:
            return self.all_players.copy()

        bitmaps = []
        for token_str in token_strs:
            token_id = self.token_to_id.get(token_str)
            if token_id is None or token_id not in self.tokens:
                return BitMap()  # Unknown token = empty result
            bitmaps.append(self.tokens[token_id].bitmap)

        if not bitmaps:
            return BitMap()

        # Roaring intersection is O(min(n,m)) and cache-friendly
        result = bitmaps[0].copy()
        for bm in bitmaps[1:]:
            result &= bm
        return result

    def filter_bitmap(self, include_tokens: list[str], exclude_tokens: list[str] | None = None) -> BitMap:
        """
        Compute the bitmap for include/exclude token constraints.

        Semantics:
          - include_tokens are intersected (AND)
          - exclude_tokens are unioned then subtracted (NOT)

        Unknown exclude tokens are ignored. Unknown include tokens yield an empty set.
        """
        base = self.intersect(include_tokens)
        if not base:
            return base
        if not exclude_tokens:
            return base

        exclude_bm = BitMap()
        for tok in exclude_tokens:
            token_id = self.token_to_id.get(tok)
            if token_id is None:
                continue
            stats = self.tokens.get(token_id)
            if stats is None:
                continue
            exclude_bm |= stats.bitmap

        if exclude_bm:
            base -= exclude_bm
        return base

    def avg_placement_for_bitmap(self, bitmap: BitMap) -> float:
        """
        Compute average placement for a set of player IDs.
        Uses numpy vectorized operations for speed.
        """
        if not bitmap:
            return 4.5

        # Convert bitmap to numpy array and index into placements
        ids = np.array(bitmap.to_array(), dtype=np.int64)
        return float(self.placements[ids].mean())

    def score_candidates(
        self,
        base: BitMap,
        candidates: list[str],
        min_sample: int = 10
    ) -> list[dict]:
        """
        Score candidate tokens by placement delta.

        Optimization: Uses precomputed sums where possible,
        falls back to bitmap intersection for filtered sets.
        """
        if not base:
            return []

        n_base = len(base)
        avg_base = self.avg_placement_for_bitmap(base)

        results = []
        for token_str in candidates:
            token_id = self.token_to_id.get(token_str)
            if token_id is None or token_id not in self.tokens:
                continue

            token_stats = self.tokens[token_id]

            # Intersection with base set
            with_bitmap = base & token_stats.bitmap
            n_with = len(with_bitmap)

            if n_with < min_sample:
                continue

            avg_with = self.avg_placement_for_bitmap(with_bitmap)
            delta = avg_with - avg_base

            results.append({
                "token": token_str,
                "delta": round(delta, 3),
                "avg_with": round(avg_with, 3),
                "avg_base": round(avg_base, 3),
                "n_with": n_with,
                "n_base": n_base
            })

        return results

    def get_all_tokens_by_type(self, prefix: str) -> list[str]:
        """Get all tokens starting with prefix (U:, I:, E:)."""
        return [t for t in self.id_to_token if t.startswith(prefix)]

    def get_token_count(self, token_str: str) -> int:
        """Get match count for a token."""
        token_id = self.token_to_id.get(token_str)
        if token_id is None or token_id not in self.tokens:
            return 0
        return self.tokens[token_id].count

    def get_label(self, token_str: str) -> str:
        """Get display label for a token."""
        token_id = self.token_to_id.get(token_str)
        if token_id is None:
            return token_str
        return self.labels.get(token_id, token_str)

    def apply_item_display_names(self, item_display_names: dict[str, str]) -> int:
        """
        Update labels for item (I:*) and equipped (E:*) tokens using a mapping of
        canonical item id -> in-game display name.

        This is intended to run at engine build time so the runtime server only
        needs access to `engine.bin`.
        """
        if not item_display_names:
            return 0

        updated = 0
        for token_id, token_str in enumerate(self.id_to_token):
            if token_str.startswith("I:"):
                item_id = token_str[2:]
                display = item_display_names.get(item_id.lower())
                if display:
                    self.labels[token_id] = display
                    updated += 1
                continue

            if token_str.startswith("E:"):
                rest = token_str[2:]
                if "|" not in rest:
                    continue
                unit, item_part = rest.split("|", 1)
                copies = 1
                item_id = item_part
                if ":" in item_part:
                    base, maybe_copies = item_part.rsplit(":", 1)
                    try:
                        c = int(maybe_copies)
                    except ValueError:
                        c = None
                    if c is not None and c >= 2:
                        copies = c
                        item_id = base

                display = item_display_names.get(item_id.lower())
                if display:
                    label = f"{unit} + {display}"
                    if copies >= 2:
                        label = f"{label} ×{copies}"
                    self.labels[token_id] = label
                    updated += 1
                continue

        return updated

    def apply_trait_display_names(self, trait_display_names: dict[str, str]) -> int:
        """
        Update labels for trait (T:*) tokens using a mapping of canonical trait id
        -> in-game display name.

        This is intended to run at engine build time so the runtime server only
        needs access to `engine.bin`.
        """
        if not trait_display_names:
            return 0

        import re

        updated = 0
        for token_id, token_str in enumerate(self.id_to_token):
            if not token_str.startswith("T:"):
                continue

            trait_id = token_str[2:].split(":", 1)[0]
            display = trait_display_names.get(trait_id.lower())
            if not display:
                continue

            label = self.labels.get(token_id) or token_str
            # Preserve inferred breakpoint number if present (e.g. "Demacia 5").
            m = re.search(r"(?:\s|:)(\d+)\s*$", str(label))
            if m:
                number = m.group(1)
                self.labels[token_id] = f"{display} {number}"
            else:
                self.labels[token_id] = display
            updated += 1

        return updated

    def apply_trait_breakpoints(self, trait_breakpoints: dict[str, list[int]]) -> int:
        """
        Update labels for trait (T:*) tokens to use in-game breakpoint numbers.

        `trait_breakpoints` maps canonical trait id -> ordered list of min unit
        breakpoints (e.g. "demacia" -> [3, 5, 7]).

        Behavior:
          - If a trait has exactly one breakpoint, omit the number entirely.
          - Otherwise, include the breakpoint number for *all* tiers, including the first.

        This is intended to run at engine build time so the runtime server only
        needs access to `engine.bin`.
        """
        if not trait_breakpoints:
            return 0

        updated = 0
        for token_id, token_str in enumerate(self.id_to_token):
            if not token_str.startswith("T:"):
                continue

            parts = token_str[2:].split(":")
            if not parts or not parts[0]:
                continue

            trait_id = parts[0]
            tier_idx = 1
            if len(parts) >= 2:
                try:
                    tier_idx = int(parts[1])
                except ValueError:
                    tier_idx = 1

            breakpoints = trait_breakpoints.get(trait_id.lower())
            if not breakpoints:
                continue

            if len(breakpoints) <= 1:
                self.labels[token_id] = trait_id
                updated += 1
                continue

            bp_i = tier_idx - 1
            if 0 <= bp_i < len(breakpoints):
                self.labels[token_id] = f"{trait_id} {breakpoints[bp_i]}"
                updated += 1

        return updated

    # ─────────────────────────────────────────────────────────────────
    # Serialization - Binary format for fast loading
    # ─────────────────────────────────────────────────────────────────

    def save(self, path: str = "data/engine.bin") -> None:
        """
        Save engine to optimized binary format.

        Format:
        - Header: magic, version, counts
        - Placements: raw numpy bytes
        - Token strings: length-prefixed UTF-8
        - Labels: token_id -> length-prefixed string
        - Bitmaps: serialized roaring bitmaps
        - Stats: placement_sum per token
        """
        Path(path).parent.mkdir(parents=True, exist_ok=True)

        with open(path, "wb") as f:
            # Magic + version
            f.write(b"SMEE")
            f.write(struct.pack("<I", 3))  # Version 3

            # Counts
            f.write(struct.pack("<QQQ",
                len(self.placements),
                len(self.id_to_token),
                self.total_matches
            ))

            # Placements array (raw numpy bytes)
            f.write(self.placements.tobytes())

            # Board-strength proxy arrays (Version 2+).
            # Keep these as simple numeric covariates for downstream modeling.
            n = len(self.placements)
            def _arr_or_zeros(arr: np.ndarray | None, dtype: np.dtype) -> np.ndarray:
                if arr is None or len(arr) != n:
                    return np.zeros(n, dtype=dtype)
                return arr.astype(dtype, copy=False)

            f.write(_arr_or_zeros(self.item_count, np.int16).tobytes())
            f.write(_arr_or_zeros(self.component_count, np.int16).tobytes())
            f.write(_arr_or_zeros(self.completed_item_count, np.int16).tobytes())
            f.write(_arr_or_zeros(self.unit_count, np.int16).tobytes())
            f.write(_arr_or_zeros(self.two_star_count, np.int16).tobytes())
            f.write(_arr_or_zeros(self.three_star_count, np.int16).tobytes())
            f.write(_arr_or_zeros(self.unit_gold_value, np.int32).tobytes())

            # All players bitmap
            all_players_bytes = self.all_players.serialize()
            f.write(struct.pack("<I", len(all_players_bytes)))
            f.write(all_players_bytes)

            # Token strings
            for token_str in self.id_to_token:
                token_bytes = token_str.encode("utf-8")
                f.write(struct.pack("<H", len(token_bytes)))
                f.write(token_bytes)

            # Labels
            for token_id in range(len(self.id_to_token)):
                label = self.labels.get(token_id, "")
                label_bytes = label.encode("utf-8")
                f.write(struct.pack("<H", len(label_bytes)))
                f.write(label_bytes)

            # Token stats (bitmap + sum + count)
            for token_id in range(len(self.id_to_token)):
                if token_id in self.tokens:
                    stats = self.tokens[token_id]
                    bitmap_bytes = stats.bitmap.serialize()
                    f.write(struct.pack("<I", len(bitmap_bytes)))
                    f.write(bitmap_bytes)
                    f.write(struct.pack("<qi", stats.placement_sum, stats.count))
                else:
                    # Empty token (shouldn't happen but handle it)
                    f.write(struct.pack("<I", 0))
                    f.write(struct.pack("<qi", 0, 0))

            # Precomputed necessity cache (Version 3+).
            n_tokens = len(self.id_to_token)

            def _float_arr(arr: np.ndarray | None) -> np.ndarray:
                if arr is None or arr.shape[0] != n_tokens:
                    return np.full((n_tokens,), np.nan, dtype=np.float32)
                return arr.astype(np.float32, copy=False)

            def _int_arr(arr: np.ndarray | None) -> np.ndarray:
                if arr is None or arr.shape[0] != n_tokens:
                    return np.zeros((n_tokens,), dtype=np.int32)
                return arr.astype(np.int32, copy=False)

            def _u8_arr(arr: np.ndarray | None) -> np.ndarray:
                if arr is None or arr.shape[0] != n_tokens:
                    return np.zeros((n_tokens,), dtype=np.uint8)
                return arr.astype(np.uint8, copy=False)

            f.write(_float_arr(self.necessity_top4_tau).tobytes())
            f.write(_float_arr(self.necessity_top4_ci95_low).tobytes())
            f.write(_float_arr(self.necessity_top4_ci95_high).tobytes())
            f.write(_float_arr(self.necessity_top4_se).tobytes())
            f.write(_float_arr(self.necessity_top4_raw_tau).tobytes())
            f.write(_float_arr(self.necessity_top4_frac_trimmed).tobytes())
            f.write(_float_arr(self.necessity_top4_e_p01).tobytes())
            f.write(_float_arr(self.necessity_top4_e_p99).tobytes())
            f.write(_int_arr(self.necessity_top4_n_treated).tobytes())
            f.write(_int_arr(self.necessity_top4_n_control).tobytes())
            f.write(_int_arr(self.necessity_top4_n_used).tobytes())
            f.write(_u8_arr(self.necessity_top4_scope_min_star).tobytes())

        print(f"Saved engine to {path} ({Path(path).stat().st_size / 1024 / 1024:.2f} MB)")

    @classmethod
    def load(cls, path: str = "data/engine.bin") -> "GraphEngine":
        """Load engine from binary format."""
        engine = cls()

        with open(path, "rb") as f:
            # Magic + version
            magic = f.read(4)
            if magic != b"SMEE":
                raise ValueError(f"Invalid engine file: bad magic {magic}")

            version = struct.unpack("<I", f.read(4))[0]
            if version != 3:
                raise ValueError(
                    f"Unsupported engine version: {version} (expected 3). Rebuild engine.bin with smeecher-build."
                )

            # Counts
            placements_len, num_tokens, total_matches = struct.unpack("<QQQ", f.read(24))
            engine.total_matches = total_matches

            # Placements
            engine.placements = np.frombuffer(
                f.read(placements_len),
                dtype=np.int8
            ).copy()  # Copy to make writable

            # Board-strength proxy arrays (Version 3).
            n = int(placements_len)
            engine.item_count = np.frombuffer(f.read(n * 2), dtype=np.int16).copy()
            engine.component_count = np.frombuffer(f.read(n * 2), dtype=np.int16).copy()
            engine.completed_item_count = np.frombuffer(f.read(n * 2), dtype=np.int16).copy()
            engine.unit_count = np.frombuffer(f.read(n * 2), dtype=np.int16).copy()
            engine.two_star_count = np.frombuffer(f.read(n * 2), dtype=np.int16).copy()
            engine.three_star_count = np.frombuffer(f.read(n * 2), dtype=np.int16).copy()
            engine.unit_gold_value = np.frombuffer(f.read(n * 4), dtype=np.int32).copy()

            # All players bitmap
            all_players_len = struct.unpack("<I", f.read(4))[0]
            engine.all_players = BitMap.deserialize(f.read(all_players_len))

            # Token strings
            for token_id in range(num_tokens):
                token_len = struct.unpack("<H", f.read(2))[0]
                token_str = f.read(token_len).decode("utf-8")
                engine.id_to_token.append(token_str)
                engine.token_to_id[token_str] = token_id

            # Labels
            for token_id in range(num_tokens):
                label_len = struct.unpack("<H", f.read(2))[0]
                label = f.read(label_len).decode("utf-8")
                if label:
                    engine.labels[token_id] = label

            # Token stats
            for token_id in range(num_tokens):
                bitmap_len = struct.unpack("<I", f.read(4))[0]
                if bitmap_len > 0:
                    bitmap = BitMap.deserialize(f.read(bitmap_len))
                    placement_sum, count = struct.unpack("<qi", f.read(12))
                    engine.tokens[token_id] = TokenStats(
                        bitmap=bitmap,
                        placement_sum=placement_sum,
                        count=count
                    )
                else:
                    f.read(12)  # Skip empty stats

            # Precomputed necessity cache (Version 3).
            n = int(num_tokens)
            engine.necessity_top4_tau = np.frombuffer(f.read(n * 4), dtype=np.float32).copy()
            engine.necessity_top4_ci95_low = np.frombuffer(f.read(n * 4), dtype=np.float32).copy()
            engine.necessity_top4_ci95_high = np.frombuffer(f.read(n * 4), dtype=np.float32).copy()
            engine.necessity_top4_se = np.frombuffer(f.read(n * 4), dtype=np.float32).copy()
            engine.necessity_top4_raw_tau = np.frombuffer(f.read(n * 4), dtype=np.float32).copy()
            engine.necessity_top4_frac_trimmed = np.frombuffer(f.read(n * 4), dtype=np.float32).copy()
            engine.necessity_top4_e_p01 = np.frombuffer(f.read(n * 4), dtype=np.float32).copy()
            engine.necessity_top4_e_p99 = np.frombuffer(f.read(n * 4), dtype=np.float32).copy()
            engine.necessity_top4_n_treated = np.frombuffer(f.read(n * 4), dtype=np.int32).copy()
            engine.necessity_top4_n_control = np.frombuffer(f.read(n * 4), dtype=np.int32).copy()
            engine.necessity_top4_n_used = np.frombuffer(f.read(n * 4), dtype=np.int32).copy()
            engine.necessity_top4_scope_min_star = np.frombuffer(f.read(n), dtype=np.uint8).copy()
            engine.necessity_top4_ready = bool(np.isfinite(engine.necessity_top4_tau).any())

        print(f"Loaded engine: {num_tokens} tokens, {total_matches} matches")
        return engine

    def stats(self) -> dict:
        """Return engine statistics."""
        unit_tokens = [t for t in self.id_to_token if t.startswith("U:")]
        item_tokens = [t for t in self.id_to_token if t.startswith("I:")]
        equipped_tokens = [t for t in self.id_to_token if t.startswith("E:")]
        trait_tokens = [t for t in self.id_to_token if t.startswith("T:")]

        return {
            "total_matches": self.total_matches,
            "total_tokens": len(self.id_to_token),
            "unit_tokens": len(unit_tokens),
            "item_tokens": len(item_tokens),
            "equipped_tokens": len(equipped_tokens),
            "trait_tokens": len(trait_tokens),
            "placements_size_mb": self.placements.nbytes / 1024 / 1024,
        }


def build_engine(db_path: str = None, save_path: str = None):
    """CLI entry point for building the engine."""
    import os
    import re

    data_dir = Path(os.environ.get("DATA_DIR", "../data"))

    if db_path is None:
        db_path = str(data_dir / "smeecher.db")
    if save_path is None:
        save_path = str(data_dir / "engine.bin")

    def _strip_html(text: str) -> str:
        return re.sub(r"<[^>]*>", "", text or "").strip()

    def _load_item_display_names_from_cdragon() -> dict[str, str]:
        """
        Load TFT item display names from Community Dragon.

        Returns a mapping of cleaned item ids (e.g. "RunaansHurricane") to
        current in-game display names (e.g. "Kraken's Fury").
        """
        cdragon_path = os.environ.get("CDRAGON_TFTITEMS_PATH")
        cdragon_url = os.environ.get(
            "CDRAGON_TFTITEMS_URL",
            "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/tftitems.json",
        )

        try:
            if cdragon_path and Path(cdragon_path).exists():
                raw = Path(cdragon_path).read_bytes()
                items = orjson.loads(raw)
            else:
                import httpx

                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(cdragon_url)
                    resp.raise_for_status()
                    items = resp.json()

            mapping: dict[str, str] = {}
            for item in items or []:
                name_id = str(item.get("nameId") or "")
                if not name_id:
                    continue
                display = _strip_html(str(item.get("name") or ""))
                if not display:
                    continue

                # Keep the raw id too (helps when engine tokens still include TFT*_Item_ prefixes).
                mapping[name_id.lower()] = display

                cleaned = re.sub(r"^TFT\d*_Item_", "", name_id, flags=re.IGNORECASE)
                cleaned = re.sub(r"^TFT_Item_", "", cleaned, flags=re.IGNORECASE)
                if not cleaned:
                    continue
                mapping[cleaned.lower()] = display

            return mapping
        except Exception as e:
            print(f"Warning: failed to load CDragon item names ({e}); using canonical ids.")
            return {}

    def _load_trait_metadata_from_cdragon() -> tuple[dict[str, str], dict[str, list[int]]]:
        """
        Load TFT trait metadata from Community Dragon.

        Returns:
          - display name mapping: cleaned trait id -> in-game display name
          - breakpoint mapping: cleaned trait id -> ordered list of min unit breakpoints
        """
        cdragon_path = os.environ.get("CDRAGON_TFTTRAITS_PATH")
        cdragon_url = os.environ.get(
            "CDRAGON_TFTTRAITS_URL",
            "https://raw.communitydragon.org/latest/plugins/rcp-be-lol-game-data/global/default/v1/tfttraits.json",
        )

        try:
            if cdragon_path and Path(cdragon_path).exists():
                raw = Path(cdragon_path).read_bytes()
                traits = orjson.loads(raw)
            else:
                import httpx

                with httpx.Client(timeout=10.0) as client:
                    resp = client.get(cdragon_url)
                    resp.raise_for_status()
                    traits = resp.json()

            display_mapping: dict[str, str] = {}
            breakpoint_mapping: dict[str, list[int]] = {}
            for trait in traits or []:
                trait_id = str(trait.get("trait_id") or trait.get("traitId") or "")
                if not trait_id:
                    continue
                set_id = str(trait.get("set") or "")
                if set_id and set_id != "TFTSet16":
                    continue
                mins = []
                trait_sets = (
                    trait.get("conditional_trait_sets")
                    or trait.get("conditionalTraitSets")
                    or []
                )
                innate_sets = (
                    trait.get("innate_trait_sets")
                    or trait.get("innateTraitSets")
                    or []
                )

                for eff in list(trait_sets) + list(innate_sets):
                    try:
                        mu = eff.get("min_units") if isinstance(eff, dict) else None
                        if mu is None and isinstance(eff, dict):
                            mu = eff.get("minUnits")
                        if mu is None:
                            continue
                        mu_i = int(mu)
                        if mu_i > 0:
                            mins.append(mu_i)
                    except Exception:
                        continue
                breakpoints = sorted(set(mins))

                # Keep the raw id too (helps if tokens ever include TFT*_ prefixes).
                if breakpoints:
                    breakpoint_mapping[trait_id.lower()] = breakpoints

                display = _strip_html(str(trait.get("display_name") or trait.get("displayName") or ""))
                if display:
                    display_mapping[trait_id.lower()] = display

                cleaned = re.sub(r"^TFT\d*_", "", trait_id, flags=re.IGNORECASE)
                cleaned = re.sub(r"^TFT_", "", cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r"^Set\d*_", "", cleaned, flags=re.IGNORECASE)
                cleaned = re.sub(r"^Set_", "", cleaned, flags=re.IGNORECASE)
                if not cleaned:
                    continue
                if breakpoints:
                    breakpoint_mapping[cleaned.lower()] = breakpoints
                if display:
                    display_mapping[cleaned.lower()] = display

            return display_mapping, breakpoint_mapping
        except Exception as e:
            print(f"Warning: failed to load CDragon trait metadata ({e}); using canonical ids.")
            return {}, {}

    print(f"Building optimized graph engine from {db_path}...")
    engine = GraphEngine()
    engine.build_from_db(db_path)

    item_display_names = _load_item_display_names_from_cdragon()
    if item_display_names:
        updated = engine.apply_item_display_names(item_display_names)
        print(f"Applied CDragon display names to {updated} item/equipped tokens")

    trait_display_names, trait_breakpoints = _load_trait_metadata_from_cdragon()
    if trait_breakpoints:
        updated = engine.apply_trait_breakpoints(trait_breakpoints)
        print(f"Applied CDragon breakpoints to {updated} trait tokens")
    if trait_display_names:
        updated = engine.apply_trait_display_names(trait_display_names)
        print(f"Applied CDragon display names to {updated} trait tokens")

    stats = engine.stats()
    print(f"Total matches: {stats['total_matches']}")
    print(f"Total tokens: {stats['total_tokens']}")
    print(f"  Units: {stats['unit_tokens']}")
    print(f"  Items: {stats['item_tokens']}")
    print(f"  Equipped: {stats['equipped_tokens']}")
    print(f"  Traits: {stats['trait_tokens']}")
    print(f"Placements array: {stats['placements_size_mb']:.2f} MB")

    print("Precomputing AIPW necessity cache (top4)...")
    engine.precompute_necessity_cache_top4()

    engine.save(save_path)
    return engine


if __name__ == "__main__":
    build_engine()
