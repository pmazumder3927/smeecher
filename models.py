"""Data models for TFT match data."""
from pydantic import BaseModel
from typing import Optional


class Item(BaseModel):
    """An item equipped on a unit."""
    item_id: str
    name: str


class Unit(BaseModel):
    """A unit in a player's composition."""
    character_id: str
    name: str
    tier: int  # Star level (1, 2, or 3)
    items: list[str]  # List of item IDs
    rarity: int  # Cost tier


class PlayerMatch(BaseModel):
    """A player's performance in a match."""
    match_id: str
    puuid: str
    placement: int  # 1-8
    level: int
    gold_left: int
    last_round: int
    time_eliminated: float
    units: list[Unit]
    traits: list[dict]  # Active traits with tier info
    augments: list[str]  # Chosen augments


class MatchInfo(BaseModel):
    """Metadata about a match."""
    match_id: str
    game_datetime: int  # Unix timestamp in ms
    game_length: float  # Seconds
    game_version: str
    queue_id: int
    tft_set_number: int
    tft_game_type: str


class PlayerRank(BaseModel):
    """A player's ranked information."""
    puuid: str
    summoner_id: str
    tier: str  # IRON, BRONZE, SILVER, etc.
    rank: str  # I, II, III, IV
    league_points: int
    wins: int
    losses: int


class ScrapedMatch(BaseModel):
    """Complete scraped match data with all players and lobby rank info."""
    match_info: MatchInfo
    players: list[PlayerMatch]
    lobby_avg_rank: Optional[str] = None  # Estimated lobby rank
