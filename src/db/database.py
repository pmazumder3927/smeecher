"""SQLite database for TFT match data."""
import sqlite3
import json
from pathlib import Path
from typing import Optional

from ..scraper.models import PlayerRank, ScrapedMatch


class Database:
    """SQLite storage for TFT match data."""

    def __init__(self, db_path: str = "data/smeecher.db"):
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.row_factory = sqlite3.Row
        self._init()

    def _init(self):
        c = self.conn.cursor()

        c.execute("""CREATE TABLE IF NOT EXISTS matches (
            match_id TEXT PRIMARY KEY,
            game_datetime INTEGER,
            game_length REAL,
            game_version TEXT,
            queue_id INTEGER,
            tft_set_number INTEGER,
            tft_game_type TEXT,
            lobby_avg_rank TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS player_matches (
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
            UNIQUE(match_id, puuid)
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS units (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT,
            puuid TEXT,
            character_id TEXT,
            name TEXT,
            tier INTEGER,
            rarity INTEGER,
            items TEXT
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS ranks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            puuid TEXT,
            tier TEXT,
            rank TEXT,
            lp INTEGER,
            wins INTEGER,
            losses INTEGER,
            recorded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )""")

        c.execute("""CREATE TABLE IF NOT EXISTS queue (
            puuid TEXT PRIMARY KEY,
            priority INTEGER DEFAULT 0,
            scraped_at TIMESTAMP
        )""")

        c.execute("CREATE INDEX IF NOT EXISTS idx_pm_puuid ON player_matches(puuid)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_units_char ON units(character_id)")
        self.conn.commit()

    def close(self):
        self.conn.close()

    def match_exists(self, match_id: str) -> bool:
        c = self.conn.cursor()
        c.execute("SELECT 1 FROM matches WHERE match_id = ?", (match_id,))
        return c.fetchone() is not None

    def save_match(self, m: ScrapedMatch):
        c = self.conn.cursor()

        c.execute("""INSERT OR IGNORE INTO matches
            (match_id, game_datetime, game_length, game_version, queue_id, tft_set_number, tft_game_type, lobby_avg_rank)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (m.match_info.match_id, m.match_info.game_datetime, m.match_info.game_length,
             m.match_info.game_version, m.match_info.queue_id, m.match_info.tft_set_number,
             m.match_info.tft_game_type, m.lobby_avg_rank))

        for p in m.players:
            c.execute("""INSERT OR IGNORE INTO player_matches
                (match_id, puuid, placement, level, gold_left, last_round, time_eliminated, augments, traits)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (p.match_id, p.puuid, p.placement, p.level, p.gold_left,
                 p.last_round, p.time_eliminated, json.dumps(p.augments), json.dumps(p.traits)))

            for u in p.units:
                c.execute("""INSERT INTO units
                    (match_id, puuid, character_id, name, tier, rarity, items)
                    VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (p.match_id, p.puuid, u.character_id, u.name, u.tier, u.rarity, json.dumps(u.items)))

        self.conn.commit()

    def save_rank(self, r: PlayerRank):
        c = self.conn.cursor()
        c.execute("INSERT INTO ranks (puuid, tier, rank, lp, wins, losses) VALUES (?, ?, ?, ?, ?, ?)",
            (r.puuid, r.tier, r.rank, r.league_points, r.wins, r.losses))
        self.conn.commit()

    def add_to_queue(self, puuid: str, priority: int = 0):
        c = self.conn.cursor()
        c.execute("INSERT OR IGNORE INTO queue (puuid, priority) VALUES (?, ?)", (puuid, priority))
        self.conn.commit()

    def get_next(self) -> Optional[str]:
        c = self.conn.cursor()
        c.execute("""SELECT puuid FROM queue
            WHERE scraped_at IS NULL OR scraped_at < datetime('now', '-10 minutes')
            ORDER BY priority DESC, scraped_at ASC NULLS FIRST LIMIT 1""")
        row = c.fetchone()
        return row["puuid"] if row else None

    def mark_scraped(self, puuid: str):
        c = self.conn.cursor()
        c.execute("UPDATE queue SET scraped_at = CURRENT_TIMESTAMP WHERE puuid = ?", (puuid,))
        self.conn.commit()

    def queue_size(self) -> int:
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM queue WHERE scraped_at IS NULL")
        return c.fetchone()[0]

    def get_stats(self) -> dict:
        c = self.conn.cursor()
        c.execute("SELECT COUNT(*) FROM matches")
        matches = c.fetchone()[0]
        c.execute("SELECT COUNT(DISTINCT puuid) FROM player_matches")
        players = c.fetchone()[0]
        return {"total_matches": matches, "unique_players": players}
