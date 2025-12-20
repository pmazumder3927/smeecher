"""TFT API client."""
import asyncio
import httpx
from typing import Optional
from models import Unit, PlayerMatch, MatchInfo, PlayerRank


PLATFORM_TO_REGION = {
    "na1": "americas", "br1": "americas", "la1": "americas", "la2": "americas",
    "euw1": "europe", "eun1": "europe", "tr1": "europe", "ru": "europe",
    "kr": "asia", "jp1": "asia",
    "oc1": "sea", "ph2": "sea", "sg2": "sea", "th2": "sea", "tw2": "sea", "vn2": "sea",
}


class TFTClient:
    """Client for Riot's TFT API."""

    def __init__(self, api_key: str, platform: str = "na1"):
        self.api_key = api_key
        self.platform = platform.lower()
        self.region = PLATFORM_TO_REGION.get(self.platform, "americas")
        self.client = httpx.AsyncClient(timeout=30.0)
        self._rate_limit_wait = 0.05

    async def close(self):
        await self.client.aclose()

    async def _get(self, url: str) -> Optional[dict]:
        await asyncio.sleep(self._rate_limit_wait)
        try:
            resp = await self.client.get(url, headers={"X-Riot-Token": self.api_key})
            if resp.status_code == 200:
                return resp.json()
            elif resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 10))
                await asyncio.sleep(wait)
                return await self._get(url)
            elif resp.status_code in (401, 403):
                raise Exception("Invalid API key")
            return None
        except httpx.RequestError:
            return None

    async def get_match_ids(self, puuid: str, count: int = 20) -> list[str]:
        url = f"https://{self.region}.api.riotgames.com/tft/match/v1/matches/by-puuid/{puuid}/ids?count={count}"
        return await self._get(url) or []

    async def get_match(self, match_id: str) -> Optional[dict]:
        url = f"https://{self.region}.api.riotgames.com/tft/match/v1/matches/{match_id}"
        return await self._get(url)

    async def get_league_entries(self, puuid: str) -> Optional[list[dict]]:
        url = f"https://{self.platform}.api.riotgames.com/tft/league/v1/by-puuid/{puuid}"
        return await self._get(url)

    async def get_challenger(self) -> Optional[dict]:
        url = f"https://{self.platform}.api.riotgames.com/tft/league/v1/challenger"
        return await self._get(url)

    async def get_grandmaster(self) -> Optional[dict]:
        url = f"https://{self.platform}.api.riotgames.com/tft/league/v1/grandmaster"
        return await self._get(url)

    async def get_master(self) -> Optional[dict]:
        url = f"https://{self.platform}.api.riotgames.com/tft/league/v1/master"
        return await self._get(url)

    def parse_match(self, data: dict) -> tuple[MatchInfo, list[PlayerMatch]]:
        info, meta = data["info"], data["metadata"]

        match_info = MatchInfo(
            match_id=meta["match_id"],
            game_datetime=info["game_datetime"],
            game_length=info["game_length"],
            game_version=info["game_version"],
            queue_id=info.get("queue_id", 0),
            tft_set_number=info.get("tft_set_number", 0),
            tft_game_type=info.get("tft_game_type", "standard"),
        )

        players = []
        for p in info["participants"]:
            units = [
                Unit(
                    character_id=u.get("character_id", ""),
                    name=u.get("character_id", "").replace("TFT_", "").replace("TFT14_", ""),
                    tier=u.get("tier", 1),
                    items=[str(i) for i in u.get("itemNames", [])],
                    rarity=u.get("rarity", 0),
                )
                for u in p.get("units", [])
            ]

            players.append(PlayerMatch(
                match_id=meta["match_id"],
                puuid=p["puuid"],
                placement=p["placement"],
                level=p.get("level", 0),
                gold_left=p.get("gold_left", 0),
                last_round=p.get("last_round", 0),
                time_eliminated=p.get("time_eliminated", 0),
                units=units,
                traits=[
                    {"name": t["name"], "tier": t.get("tier_current", 0), "num_units": t.get("num_units", 0)}
                    for t in p.get("traits", []) if t.get("tier_current", 0) > 0
                ],
                augments=p.get("augments", []),
            ))

        return match_info, players

    async def get_player_rank(self, puuid: str) -> Optional[PlayerRank]:
        entries = await self.get_league_entries(puuid)
        if not entries:
            return None

        for e in entries:
            if e.get("queueType") == "RANKED_TFT":
                return PlayerRank(
                    puuid=puuid,
                    summoner_id=e.get("summonerId", ""),
                    tier=e.get("tier", "UNRANKED"),
                    rank=e.get("rank", ""),
                    league_points=e.get("leaguePoints", 0),
                    wins=e.get("wins", 0),
                    losses=e.get("losses", 0),
                )
        return None
