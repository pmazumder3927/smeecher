"""Query utilities for analyzing scraped TFT data."""
import sqlite3
import json
from pathlib import Path


def get_db(db_path: str = "tft_matches.db"):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def get_stats(db_path: str = "tft_matches.db"):
    """Print database statistics."""
    conn = get_db(db_path)
    cur = conn.cursor()

    print("=" * 50)
    print("TFT Scraper Database Statistics")
    print("=" * 50)

    cur.execute("SELECT COUNT(*) FROM matches")
    print(f"Total matches: {cur.fetchone()[0]}")

    cur.execute("SELECT COUNT(DISTINCT puuid) FROM player_matches")
    print(f"Unique players: {cur.fetchone()[0]}")

    cur.execute("SELECT COUNT(*) FROM units")
    print(f"Total unit placements: {cur.fetchone()[0]}")

    cur.execute("SELECT COUNT(*) FROM player_ranks")
    print(f"Rank snapshots: {cur.fetchone()[0]}")

    conn.close()


def top_units(db_path: str = "tft_matches.db", limit: int = 20):
    """Show most played units."""
    conn = get_db(db_path)
    cur = conn.cursor()

    print(f"\nTop {limit} Most Played Units:")
    print("-" * 40)

    cur.execute("""
        SELECT name, COUNT(*) as plays, AVG(tier) as avg_tier
        FROM units
        GROUP BY character_id
        ORDER BY plays DESC
        LIMIT ?
    """, (limit,))

    for row in cur.fetchall():
        print(f"{row['name']:20} | Plays: {row['plays']:5} | Avg Stars: {row['avg_tier']:.2f}")

    conn.close()


def top_items(db_path: str = "tft_matches.db", limit: int = 20):
    """Show most used items."""
    conn = get_db(db_path)
    cur = conn.cursor()

    print(f"\nTop {limit} Most Used Items:")
    print("-" * 40)

    cur.execute("SELECT items FROM units WHERE items != '[]'")

    item_counts = {}
    for row in cur.fetchall():
        items = json.loads(row["items"])
        for item in items:
            item_name = item.replace("TFT_Item_", "").replace("TFT14_Item_", "")
            item_counts[item_name] = item_counts.get(item_name, 0) + 1

    sorted_items = sorted(item_counts.items(), key=lambda x: x[1], reverse=True)[:limit]
    for item, count in sorted_items:
        print(f"{item:30} | Uses: {count}")

    conn.close()


def placement_by_rank(db_path: str = "tft_matches.db"):
    """Show average placement by lobby rank."""
    conn = get_db(db_path)
    cur = conn.cursor()

    print("\nAverage Placement by Lobby Rank:")
    print("-" * 40)

    cur.execute("""
        SELECT m.lobby_avg_rank, AVG(pm.placement) as avg_placement, COUNT(*) as games
        FROM matches m
        JOIN player_matches pm ON m.match_id = pm.match_id
        WHERE m.lobby_avg_rank IS NOT NULL
        GROUP BY m.lobby_avg_rank
        ORDER BY avg_placement
    """)

    for row in cur.fetchall():
        print(f"{row['lobby_avg_rank']:25} | Avg Place: {row['avg_placement']:.2f} | Games: {row['games']}")

    conn.close()


def unit_winrates(db_path: str = "tft_matches.db", min_games: int = 10):
    """Show unit winrates (top 4 rate)."""
    conn = get_db(db_path)
    cur = conn.cursor()

    print(f"\nUnit Top 4 Rates (min {min_games} games):")
    print("-" * 50)

    cur.execute("""
        SELECT u.name,
               COUNT(*) as games,
               SUM(CASE WHEN pm.placement <= 4 THEN 1 ELSE 0 END) as top4s,
               CAST(SUM(CASE WHEN pm.placement <= 4 THEN 1 ELSE 0 END) AS FLOAT) / COUNT(*) as top4_rate,
               AVG(pm.placement) as avg_placement
        FROM units u
        JOIN player_matches pm ON u.match_id = pm.match_id AND u.puuid = pm.puuid
        GROUP BY u.character_id
        HAVING games >= ?
        ORDER BY top4_rate DESC
    """, (min_games,))

    for row in cur.fetchall():
        print(f"{row['name']:20} | Top4: {row['top4_rate']*100:.1f}% | "
              f"Avg: {row['avg_placement']:.2f} | Games: {row['games']}")

    conn.close()


if __name__ == "__main__":
    import sys
    db = sys.argv[1] if len(sys.argv) > 1 else "tft_matches.db"

    if not Path(db).exists():
        print(f"Database {db} not found. Run the scraper first.")
        exit(1)

    get_stats(db)
    top_units(db)
    top_items(db)
    unit_winrates(db)
