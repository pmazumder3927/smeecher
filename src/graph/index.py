"""Build inverted index from match data for fast graph queries."""
import sqlite3
import json
import pickle
from pathlib import Path
from collections import defaultdict


def clean_unit_name(name: str) -> str:
    """Strip TFT16_ prefix and format nicely."""
    return name.replace("TFT16_", "").replace("TFT_", "")


def clean_item_name(item_id: str) -> str:
    """Strip TFT_Item_ prefix and format nicely."""
    return item_id.replace("TFT_Item_", "").replace("TFT16_Item_", "")


def build_index(db_path: str = "data/smeecher.db") -> dict:
    """
    Build inverted index from match data.

    Token types:
    - U:UnitName - unit exists on board
    - I:ItemName - item exists somewhere on board
    - E:UnitName|ItemName - item equipped on specific unit

    Returns dict with:
    - index: {token: set of player_match_ids}
    - placements: {player_match_id: placement (1-8)}
    - metadata: {player_match_id: {placement, match_id, puuid}}
    - labels: {token: display_label}
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()

    # Get all player matches
    c.execute("""
        SELECT pm.id, pm.match_id, pm.puuid, pm.placement
        FROM player_matches pm
    """)
    player_matches = c.fetchall()

    # Initialize structures
    index = defaultdict(set)  # token -> set of pm_ids
    placements = {}  # pm_id -> placement (1-8)
    metadata = {}  # pm_id -> {placement, match_id, puuid}
    labels = {}  # token -> display label

    for pm in player_matches:
        pm_id = pm["id"]
        placement = pm["placement"]

        metadata[pm_id] = {
            "placement": placement,
            "match_id": pm["match_id"],
            "puuid": pm["puuid"]
        }

        placements[pm_id] = placement

        # Get units for this player match
        c.execute("""
            SELECT name, items FROM units
            WHERE match_id = ? AND puuid = ?
        """, (pm["match_id"], pm["puuid"]))

        units = c.fetchall()
        board_items = set()  # track all items on board

        for unit in units:
            unit_name = clean_unit_name(unit["name"])
            items = json.loads(unit["items"]) if unit["items"] else []

            # Token A: unit presence
            unit_token = f"U:{unit_name}"
            index[unit_token].add(pm_id)
            labels[unit_token] = unit_name

            for item_id in items:
                item_name = clean_item_name(item_id)
                board_items.add(item_name)

                # Token C: equipped (unit|item)
                equipped_token = f"E:{unit_name}|{item_name}"
                index[equipped_token].add(pm_id)
                labels[equipped_token] = f"{unit_name} + {item_name}"

        # Token B: item presence anywhere on board
        for item_name in board_items:
            item_token = f"I:{item_name}"
            index[item_token].add(pm_id)
            labels[item_token] = item_name

    conn.close()

    # Convert defaultdict to regular dict for serialization
    result = {
        "index": dict(index),
        "placements": placements,
        "metadata": metadata,
        "labels": labels,
        "total_matches": len(player_matches)
    }

    return result


def save_index(index_data: dict, path: str = "data/index.pkl"):
    """Save index to disk."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(index_data, f)
    print(f"Saved index to {path}")


def load_index(path: str = "data/index.pkl") -> dict:
    """Load index from disk."""
    with open(path, "rb") as f:
        return pickle.load(f)


def print_stats(index_data: dict):
    """Print index statistics."""
    index = index_data["index"]
    placements = index_data["placements"]

    unit_tokens = [t for t in index if t.startswith("U:")]
    item_tokens = [t for t in index if t.startswith("I:")]
    equipped_tokens = [t for t in index if t.startswith("E:")]

    avg_placement = sum(placements.values()) / len(placements) if placements else 0

    print(f"Total player matches: {index_data['total_matches']}")
    print(f"Average placement: {avg_placement:.2f}")
    print(f"Unit tokens: {len(unit_tokens)}")
    print(f"Item tokens: {len(item_tokens)}")
    print(f"Equipped tokens: {len(equipped_tokens)}")
    print(f"Total tokens: {len(index)}")

    # Show most common units
    print("\nMost common units:")
    sorted_units = sorted(unit_tokens, key=lambda t: len(index[t]), reverse=True)[:10]
    for t in sorted_units:
        print(f"  {index_data['labels'][t]}: {len(index[t])} games")

    # Show most common items
    print("\nMost common items:")
    sorted_items = sorted(item_tokens, key=lambda t: len(index[t]), reverse=True)[:10]
    for t in sorted_items:
        print(f"  {index_data['labels'][t]}: {len(index[t])} games")


def main():
    """CLI entry point for building the index."""
    print("Building index...")
    index_data = build_index()
    print_stats(index_data)
    save_index(index_data)


if __name__ == "__main__":
    main()
