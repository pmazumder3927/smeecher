"""SQLite database for storing scraped TFT data."""
import sqlite3
import json
from pathlib import Path
from typing import Optional
from models import MatchInfo, PlayerMatch, PlayerRank, ScrapedMatch


class Database:
    """SQLite database wrapper for TFT match data."""

    def __init__(self, db_path: str = "tft_matches.db"):
        self.db_path = Path(db_path)
        self.conn: Optional[sqlite3.Connection] = None
        self._init_db()

    def _init_db(self):
        """Initialize database tables."""
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        cursor = self.conn.cursor()

        # Matches table
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS matches (
                match_id TEXT PRIMARY KEY,
                game_datetime INTEGER,
                game_length REAL,
                game_version TEXT,
                queue_id INTEGER,
                tft_set_number INTEGER,
                tft_game_type TEXT,
                lobby_avg_rank TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Player matches table (one row per player per match)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS player_matches (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT,
                puuid TEXT,
                placement INTEGER,
                level INTEGER,
                gold_left INTEGER,
                last_round INTEGER,
                time_eliminated REAL,
                augments TEXT,
                traits TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (match_id) REFERENCES matches(match_id),
                UNIQUE(match_id, puuid)
            )
        """)

        # Units table (one row per unit per player per match)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS units (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                match_id TEXT,
                puuid TEXT,
                character_id TEXT,
                name TEXT,
                tier INTEGER,
                rarity INTEGER,
                items TEXT,
                FOREIGN KEY (match_id) REFERENCES matches(match_id)
            )
        """)

        # Player ranks table (tracks rank at time of match)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS player_ranks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                puuid TEXT,
                summoner_id TEXT,
                tier TEXT,
                rank TEXT,
                league_points INTEGER,
                wins INTEGER,
                losses INTEGER,
                recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Scraped players queue (for continuous scraping)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS scrape_queue (
                puuid TEXT PRIMARY KEY,
                last_scraped_at TIMESTAMP,
                priority INTEGER DEFAULT 0
            )
        """)

        # Create indexes for common queries
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_player_matches_puuid ON player_matches(puuid)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_player_matches_placement ON player_matches(placement)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_units_character ON units(character_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_units_match ON units(match_id)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_matches_datetime ON matches(game_datetime)")

        self.conn.commit()

    def close(self):
        """Close database connection."""
        if self.conn:
            self.conn.close()

    def match_exists(self, match_id: str) -> bool:
        """Check if a match has already been scraped."""
        cursor = self.conn.cursor()
        cursor.execute("SELECT 1 FROM matches WHERE match_id = ?", (match_id,))
        return cursor.fetchone() is not None

    def save_match(self, scraped: ScrapedMatch):
        """Save a complete match with all player data."""
        cursor = self.conn.cursor()

        # Insert match info
        cursor.execute("""
            INSERT OR IGNORE INTO matches
            (match_id, game_datetime, game_length, game_version, queue_id, tft_set_number, tft_game_type, lobby_avg_rank)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            scraped.match_info.match_id,
            scraped.match_info.game_datetime,
            scraped.match_info.game_length,
            scraped.match_info.game_version,
            scraped.match_info.queue_id,
            scraped.match_info.tft_set_number,
            scraped.match_info.tft_game_type,
            scraped.lobby_avg_rank,
        ))

        # Insert player matches and units
        for player in scraped.players:
            cursor.execute("""
                INSERT OR IGNORE INTO player_matches
                (match_id, puuid, placement, level, gold_left, last_round, time_eliminated, augments, traits)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                player.match_id,
                player.puuid,
                player.placement,
                player.level,
                player.gold_left,
                player.last_round,
                player.time_eliminated,
                json.dumps(player.augments),
                json.dumps(player.traits),
            ))

            # Insert units
            for unit in player.units:
                cursor.execute("""
                    INSERT INTO units
                    (match_id, puuid, character_id, name, tier, rarity, items)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (
                    player.match_id,
                    player.puuid,
                    unit.character_id,
                    unit.name,
                    unit.tier,
                    unit.rarity,
                    json.dumps(unit.items),
                ))

        self.conn.commit()

    def save_player_rank(self, rank: PlayerRank):
        """Save a player's current rank."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT INTO player_ranks
            (puuid, summoner_id, tier, rank, league_points, wins, losses)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            rank.puuid,
            rank.summoner_id,
            rank.tier,
            rank.rank,
            rank.league_points,
            rank.wins,
            rank.losses,
        ))
        self.conn.commit()

    def add_to_scrape_queue(self, puuid: str, priority: int = 0):
        """Add a player to the scrape queue."""
        cursor = self.conn.cursor()
        cursor.execute("""
            INSERT OR REPLACE INTO scrape_queue (puuid, priority)
            VALUES (?, ?)
        """, (puuid, priority))
        self.conn.commit()

    def get_next_to_scrape(self) -> Optional[str]:
        """Get the next PUUID to scrape."""
        cursor = self.conn.cursor()
        cursor.execute("""
            SELECT puuid FROM scrape_queue
            WHERE last_scraped_at IS NULL
               OR last_scraped_at < datetime('now', '-5 minutes')
            ORDER BY priority DESC, last_scraped_at ASC NULLS FIRST
            LIMIT 1
        """)
        row = cursor.fetchone()
        return row["puuid"] if row else None

    def mark_scraped(self, puuid: str):
        """Mark a player as scraped."""
        cursor = self.conn.cursor()
        cursor.execute("""
            UPDATE scrape_queue
            SET last_scraped_at = CURRENT_TIMESTAMP
            WHERE puuid = ?
        """, (puuid,))
        self.conn.commit()

    def get_stats(self) -> dict:
        """Get database statistics."""
        cursor = self.conn.cursor()
        stats = {}

        cursor.execute("SELECT COUNT(*) as count FROM matches")
        stats["total_matches"] = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(DISTINCT puuid) as count FROM player_matches")
        stats["unique_players"] = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count FROM units")
        stats["total_units"] = cursor.fetchone()["count"]

        cursor.execute("SELECT COUNT(*) as count FROM scrape_queue")
        stats["queue_size"] = cursor.fetchone()["count"]

        return stats
