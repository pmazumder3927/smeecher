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
import json
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from pyroaring import BitMap

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
        'placements', 'tokens', 'token_to_id', 'id_to_token',
        'labels', 'total_matches', 'all_players'
    )

    def __init__(self):
        self.placements: np.ndarray = None  # int8 array
        self.tokens: dict[TokenId, TokenStats] = {}
        self.token_to_id: dict[str, TokenId] = {}
        self.id_to_token: list[str] = []
        self.labels: dict[TokenId, str] = {}
        self.total_matches: int = 0
        self.all_players: BitMap = BitMap()  # All player IDs for empty queries

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

        # Get max player_match_id for array sizing
        c.execute("SELECT MAX(id), COUNT(*) FROM player_matches")
        row = c.fetchone()
        max_id = row[0] or 0
        self.total_matches = row[1] or 0

        # Allocate placement array (int8 is enough for 1-8)
        self.placements = np.zeros(max_id + 1, dtype=np.int8)

        # Single JOIN query - streams everything in one pass
        c.execute("""
            SELECT
                pm.id as pm_id,
                pm.placement,
                u.name as unit_name,
                u.items
            FROM player_matches pm
            LEFT JOIN units u ON u.match_id = pm.match_id AND u.puuid = pm.puuid
            ORDER BY pm.id
        """)

        # Temporary accumulators: token_id -> (bitmap_builder, placement_sum)
        token_data: dict[TokenId, tuple[list[int], int]] = {}

        current_pm_id = None
        current_placement = None
        board_items: set[str] = set()

        def flush_board():
            """Process accumulated board items for current player match."""
            if current_pm_id is None:
                return

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

                # Store placement
                self.placements[pm_id] = placement
                self.all_players.add(pm_id)

            # Process unit (may be NULL from LEFT JOIN if player has no units)
            unit_name_raw = row["unit_name"]
            if unit_name_raw is None:
                continue

            unit_name = self._clean_unit_name(unit_name_raw)
            items_json = row["items"]
            items = json.loads(items_json) if items_json else []

            # Unit presence token
            unit_token = f"U:{unit_name}"
            token_id = self._get_or_create_token_id(unit_token)
            self.labels[token_id] = unit_name

            if token_id not in token_data:
                token_data[token_id] = ([], 0)
            ids, psum = token_data[token_id]
            ids.append(pm_id)
            token_data[token_id] = (ids, psum + placement)

            # Equipped tokens and track board items
            for item_id in items:
                item_name = self._clean_item_name(item_id)
                board_items.add(item_name)

                # Equipped token
                equipped_token = f"E:{unit_name}|{item_name}"
                token_id = self._get_or_create_token_id(equipped_token)
                self.labels[token_id] = f"{unit_name} + {item_name}"

                if token_id not in token_data:
                    token_data[token_id] = ([], 0)
                ids, psum = token_data[token_id]
                ids.append(pm_id)
                token_data[token_id] = (ids, psum + placement)

        # Flush last board
        flush_board()
        conn.close()

        # Convert accumulated data to roaring bitmaps
        for token_id, (ids, psum) in token_data.items():
            # Deduplicate IDs (units can appear multiple times per board)
            unique_ids = list(set(ids))
            # Recalculate sum for unique IDs (use int() to avoid numpy int8 overflow)
            actual_sum = sum(int(self.placements[pid]) for pid in unique_ids)
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
            f.write(struct.pack("<I", 1))  # Version 1

            # Counts
            f.write(struct.pack("<QQQ",
                len(self.placements),
                len(self.id_to_token),
                self.total_matches
            ))

            # Placements array (raw numpy bytes)
            f.write(self.placements.tobytes())

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
            if version != 1:
                raise ValueError(f"Unsupported engine version: {version}")

            # Counts
            placements_len, num_tokens, total_matches = struct.unpack("<QQQ", f.read(24))
            engine.total_matches = total_matches

            # Placements
            engine.placements = np.frombuffer(
                f.read(placements_len),
                dtype=np.int8
            ).copy()  # Copy to make writable

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

        print(f"Loaded engine: {num_tokens} tokens, {total_matches} matches")
        return engine

    def stats(self) -> dict:
        """Return engine statistics."""
        unit_tokens = [t for t in self.id_to_token if t.startswith("U:")]
        item_tokens = [t for t in self.id_to_token if t.startswith("I:")]
        equipped_tokens = [t for t in self.id_to_token if t.startswith("E:")]

        return {
            "total_matches": self.total_matches,
            "total_tokens": len(self.id_to_token),
            "unit_tokens": len(unit_tokens),
            "item_tokens": len(item_tokens),
            "equipped_tokens": len(equipped_tokens),
            "placements_size_mb": self.placements.nbytes / 1024 / 1024,
        }


def build_engine(db_path: str = "data/smeecher.db", save_path: str = "data/engine.bin"):
    """CLI entry point for building the engine."""
    print("Building optimized graph engine...")
    engine = GraphEngine()
    engine.build_from_db(db_path)

    stats = engine.stats()
    print(f"Total matches: {stats['total_matches']}")
    print(f"Total tokens: {stats['total_tokens']}")
    print(f"  Units: {stats['unit_tokens']}")
    print(f"  Items: {stats['item_tokens']}")
    print(f"  Equipped: {stats['equipped_tokens']}")
    print(f"Placements array: {stats['placements_size_mb']:.2f} MB")

    engine.save(save_path)
    return engine


if __name__ == "__main__":
    build_engine()
