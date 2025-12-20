"""TFT API client for fetching match data."""
import asyncio
import httpx
from typing import Optional
from models import Unit, PlayerMatch, MatchInfo, PlayerRank


# Regional routing for match-v1 API
REGIONS = {
    "americas": ["na1", "br1", "la1", "la2"],
    "europe": ["euw1", "eun1", "tr1", "ru"],
    "asia": ["kr", "jp1"],
    "sea": ["oc1", "ph2", "sg2", "th2", "tw2", "vn2"],
}

# Reverse mapping: platform -> region
PLATFORM_TO_REGION = {}
for region, platforms in REGIONS.items():
    for platform in platforms:
        PLATFORM_TO_REGION[platform] = region


class RateLimiter:
    """Simple rate limiter for API requests."""

    def __init__(self, requests_per_second: float = 20, requests_per_two_minutes: int = 100):
        self.requests_per_second = requests_per_second
        self.requests_per_two_minutes = requests_per_two_minutes
        self.second_tokens = requests_per_second
        self.two_min_tokens = requests_per_two_minutes
        self.last_second_refill = asyncio.get_event_loop().time() if asyncio.get_event_loop().is_running() else 0
        self.last_two_min_refill = self.last_second_refill

    async def acquire(self):
        """Wait until a request can be made."""
        while True:
            now = asyncio.get_event_loop().time()

            # Refill second bucket
            elapsed_second = now - self.last_second_refill
            if elapsed_second >= 1.0:
                self.second_tokens = self.requests_per_second
                self.last_second_refill = now

            # Refill two-minute bucket
            elapsed_two_min = now - self.last_two_min_refill
            if elapsed_two_min >= 120.0:
                self.two_min_tokens = self.requests_per_two_minutes
                self.last_two_min_refill = now

            # Check if we can make a request
            if self.second_tokens >= 1 and self.two_min_tokens >= 1:
                self.second_tokens -= 1
                self.two_min_tokens -= 1
                return

            # Wait a bit before retrying
            await asyncio.sleep(0.1)


class TFTClient:
    """Client for interacting with Riot's TFT API."""

    def __init__(self, api_key: str, platform: str = "na1"):
        self.api_key = api_key
        self.platform = platform.lower()
        self.region = PLATFORM_TO_REGION.get(self.platform, "americas")
        self.rate_limiter = RateLimiter()
        self.client = httpx.AsyncClient(timeout=30.0)

    @property
    def headers(self) -> dict:
        return {"X-Riot-Token": self.api_key}

    async def close(self):
        await self.client.aclose()

    async def _get(self, url: str) -> Optional[dict]:
        """Make a rate-limited GET request."""
        await self.rate_limiter.acquire()
        try:
            response = await self.client.get(url, headers=self.headers)
            if response.status_code == 200:
                return response.json()
            elif response.status_code == 429:
                # Rate limited - wait and retry
                retry_after = int(response.headers.get("Retry-After", 10))
                print(f"Rate limited, waiting {retry_after}s...")
                await asyncio.sleep(retry_after)
                return await self._get(url)
            elif response.status_code == 404:
                return None
            else:
                print(f"API error {response.status_code}: {response.text}")
                return None
        except httpx.RequestError as e:
            print(f"Request error: {e}")
            return None

    async def get_summoner_by_name(self, game_name: str, tag_line: str) -> Optional[dict]:
        """Get account info by Riot ID (game name + tag)."""
        url = f"https://{self.region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/{game_name}/{tag_line}"
        return await self._get(url)

    async def get_summoner_by_puuid(self, puuid: str) -> Optional[dict]:
        """Get summoner info by PUUID."""
        url = f"https://{self.platform}.api.riotgames.com/tft/summoner/v1/summoners/by-puuid/{puuid}"
        return await self._get(url)

    async def get_match_ids(self, puuid: str, count: int = 20, start: int = 0) -> list[str]:
        """Get recent match IDs for a player."""
        url = f"https://{self.region}.api.riotgames.com/tft/match/v1/matches/by-puuid/{puuid}/ids?count={count}&start={start}"
        result = await self._get(url)
        return result if result else []

    async def get_match(self, match_id: str) -> Optional[dict]:
        """Get full match data by ID."""
        url = f"https://{self.region}.api.riotgames.com/tft/match/v1/matches/{match_id}"
        return await self._get(url)

    async def get_league_entries(self, summoner_id: str) -> Optional[list[dict]]:
        """Get ranked league entries for a summoner."""
        url = f"https://{self.platform}.api.riotgames.com/tft/league/v1/entries/by-summoner/{summoner_id}"
        return await self._get(url)

    async def get_challenger_league(self) -> Optional[dict]:
        """Get challenger league players (for seeding scraper)."""
        url = f"https://{self.platform}.api.riotgames.com/tft/league/v1/challenger"
        return await self._get(url)

    async def get_grandmaster_league(self) -> Optional[dict]:
        """Get grandmaster league players."""
        url = f"https://{self.platform}.api.riotgames.com/tft/league/v1/grandmaster"
        return await self._get(url)

    async def get_master_league(self) -> Optional[dict]:
        """Get master league players."""
        url = f"https://{self.platform}.api.riotgames.com/tft/league/v1/master"
        return await self._get(url)

    def parse_match(self, match_data: dict) -> tuple[MatchInfo, list[PlayerMatch]]:
        """Parse raw match data into our models."""
        info = match_data["info"]
        metadata = match_data["metadata"]

        match_info = MatchInfo(
            match_id=metadata["match_id"],
            game_datetime=info["game_datetime"],
            game_length=info["game_length"],
            game_version=info["game_version"],
            queue_id=info.get("queue_id", 0),
            tft_set_number=info.get("tft_set_number", 0),
            tft_game_type=info.get("tft_game_type", "standard"),
        )

        players = []
        for participant in info["participants"]:
            units = []
            for unit_data in participant.get("units", []):
                unit = Unit(
                    character_id=unit_data.get("character_id", ""),
                    name=unit_data.get("character_id", "").replace("TFT_", "").replace("TFT14_", ""),
                    tier=unit_data.get("tier", 1),
                    items=[str(item) for item in unit_data.get("itemNames", [])],
                    rarity=unit_data.get("rarity", 0),
                )
                units.append(unit)

            player = PlayerMatch(
                match_id=metadata["match_id"],
                puuid=participant["puuid"],
                placement=participant["placement"],
                level=participant.get("level", 0),
                gold_left=participant.get("gold_left", 0),
                last_round=participant.get("last_round", 0),
                time_eliminated=participant.get("time_eliminated", 0),
                units=units,
                traits=[
                    {"name": t["name"], "tier": t.get("tier_current", 0), "num_units": t.get("num_units", 0)}
                    for t in participant.get("traits", [])
                    if t.get("tier_current", 0) > 0
                ],
                augments=participant.get("augments", []),
            )
            players.append(player)

        return match_info, players

    async def get_player_rank(self, puuid: str) -> Optional[PlayerRank]:
        """Get a player's current ranked info."""
        summoner = await self.get_summoner_by_puuid(puuid)
        if not summoner:
            return None

        entries = await self.get_league_entries(summoner["id"])
        if not entries:
            return None

        # Find TFT ranked entry
        for entry in entries:
            if entry.get("queueType") == "RANKED_TFT":
                return PlayerRank(
                    puuid=puuid,
                    summoner_id=summoner["id"],
                    tier=entry["tier"],
                    rank=entry["rank"],
                    league_points=entry["leaguePoints"],
                    wins=entry["wins"],
                    losses=entry["losses"],
                )

        return None
