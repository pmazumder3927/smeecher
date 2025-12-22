"""Data models for TFT match data."""
from pydantic import BaseModel
from typing import Optional


class Unit(BaseModel):
    character_id: str
    name: str
    tier: int
    items: list[str]
    rarity: int


class PlayerMatch(BaseModel):
    match_id: str
    puuid: str
    placement: int
    level: int
    gold_left: int
    last_round: int
    time_eliminated: float
    units: list[Unit]
    traits: list[dict]
    augments: list[str]


class MatchInfo(BaseModel):
    match_id: str
    game_datetime: int
    game_length: float
    game_version: str
    queue_id: int
    tft_set_number: int
    tft_game_type: str


class PlayerRank(BaseModel):
    puuid: str
    summoner_id: str
    tier: str
    rank: str
    league_points: int
    wins: int
    losses: int


class ScrapedMatch(BaseModel):
    match_info: MatchInfo
    players: list[PlayerMatch]
    lobby_avg_rank: Optional[str] = None
