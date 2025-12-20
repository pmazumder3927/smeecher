"""Continuous TFT match scraping server."""
import asyncio
import os
import signal
from datetime import datetime
from typing import Optional
from dotenv import load_dotenv

from client import TFTClient
from database import Database
from models import MatchInfo, PlayerMatch, ScrapedMatch


# Tier order for ranking calculation
TIER_ORDER = {
    "IRON": 0, "BRONZE": 1, "SILVER": 2, "GOLD": 3,
    "PLATINUM": 4, "EMERALD": 5, "DIAMOND": 6,
    "MASTER": 7, "GRANDMASTER": 8, "CHALLENGER": 9
}


def tier_to_numeric(tier: str, rank: str, lp: int) -> int:
    """Convert tier/rank to a numeric value for averaging."""
    base = TIER_ORDER.get(tier.upper(), 0) * 400
    rank_bonus = {"IV": 0, "III": 100, "II": 200, "I": 300}.get(rank, 0)
    return base + rank_bonus + lp


def numeric_to_tier(value: int) -> str:
    """Convert numeric value back to tier string."""
    tier_idx = min(value // 400, 9)
    tier_names = list(TIER_ORDER.keys())
    tier = tier_names[tier_idx]

    if tier in ["MASTER", "GRANDMASTER", "CHALLENGER"]:
        lp = value - (tier_idx * 400)
        return f"{tier} {lp}LP"

    remaining = value % 400
    rank_idx = remaining // 100
    ranks = ["IV", "III", "II", "I"]
    rank = ranks[min(rank_idx, 3)]
    lp = remaining % 100
    return f"{tier} {rank} {lp}LP"


class TFTScraper:
    """Continuous TFT match scraper."""

    def __init__(self, api_key: str, platform: str = "na1", db_path: str = "tft_matches.db"):
        self.client = TFTClient(api_key, platform)
        self.db = Database(db_path)
        self.running = False
        self.matches_scraped = 0
        self.start_time: Optional[datetime] = None

    async def seed_from_top_players(self):
        """Seed the scrape queue with top-ranked players."""
        print("Seeding scrape queue from top players...")

        # Get challenger, grandmaster, and master players
        for league_name, get_league in [
            ("Challenger", self.client.get_challenger_league),
            ("Grandmaster", self.client.get_grandmaster_league),
            ("Master", self.client.get_master_league),
        ]:
            print(f"Fetching {league_name} league...")
            league = await get_league()
            if league and "entries" in league:
                for entry in league["entries"][:100]:  # Top 100 from each
                    summoner = await self.client.get_summoner_by_puuid(entry.get("puuid", ""))
                    if summoner:
                        priority = TIER_ORDER.get(league_name.upper(), 0)
                        self.db.add_to_scrape_queue(summoner["puuid"], priority)
                print(f"Added {min(len(league['entries']), 100)} {league_name} players to queue")
                await asyncio.sleep(1)  # Respect rate limits

    async def scrape_player_matches(self, puuid: str) -> int:
        """Scrape recent matches for a player. Returns number of new matches."""
        match_ids = await self.client.get_match_ids(puuid, count=20)
        new_matches = 0

        for match_id in match_ids:
            if self.db.match_exists(match_id):
                continue

            match_data = await self.client.get_match(match_id)
            if not match_data:
                continue

            # Parse match data
            match_info, players = self.client.parse_match(match_data)

            # Get rank info for players and calculate lobby average
            ranks = []
            for player in players:
                rank = await self.client.get_player_rank(player.puuid)
                if rank:
                    self.db.save_player_rank(rank)
                    ranks.append(tier_to_numeric(rank.tier, rank.rank, rank.league_points))

                # Add new players to scrape queue
                self.db.add_to_scrape_queue(player.puuid, priority=0)

            # Calculate average lobby rank
            lobby_avg = numeric_to_tier(sum(ranks) // len(ranks)) if ranks else None

            # Save the match
            scraped = ScrapedMatch(
                match_info=match_info,
                players=players,
                lobby_avg_rank=lobby_avg,
            )
            self.db.save_match(scraped)
            new_matches += 1
            self.matches_scraped += 1

            print(f"[{datetime.now().strftime('%H:%M:%S')}] Scraped match {match_id[:20]}... | "
                  f"Lobby: {lobby_avg or 'Unknown'} | Total: {self.matches_scraped}")

        return new_matches

    async def run(self):
        """Run the continuous scraping loop."""
        self.running = True
        self.start_time = datetime.now()

        print("=" * 60)
        print("TFT Match Scraper Started")
        print("=" * 60)

        # Seed if queue is empty
        if self.db.get_next_to_scrape() is None:
            await self.seed_from_top_players()

        print("\nStarting continuous scrape loop...")
        print("Press Ctrl+C to stop\n")

        while self.running:
            puuid = self.db.get_next_to_scrape()

            if not puuid:
                print("Queue empty, re-seeding...")
                await self.seed_from_top_players()
                continue

            try:
                new_matches = await self.scrape_player_matches(puuid)
                self.db.mark_scraped(puuid)

                if new_matches == 0:
                    # No new matches, small delay
                    await asyncio.sleep(0.5)
                else:
                    # Found matches, continue quickly
                    await asyncio.sleep(0.1)

            except Exception as e:
                print(f"Error scraping {puuid[:8]}...: {e}")
                await asyncio.sleep(5)

        await self.shutdown()

    async def shutdown(self):
        """Clean shutdown."""
        print("\n" + "=" * 60)
        print("Shutting down...")
        stats = self.db.get_stats()
        runtime = datetime.now() - self.start_time if self.start_time else None

        print(f"Runtime: {runtime}")
        print(f"Matches scraped this session: {self.matches_scraped}")
        print(f"Total matches in database: {stats['total_matches']}")
        print(f"Unique players: {stats['unique_players']}")
        print(f"Total units recorded: {stats['total_units']}")
        print("=" * 60)

        await self.client.close()
        self.db.close()

    def stop(self):
        """Signal the scraper to stop."""
        self.running = False


async def main():
    load_dotenv()

    api_key = os.getenv("RIOT_API_KEY")
    if not api_key:
        print("Error: RIOT_API_KEY environment variable not set")
        print("Get your API key from: https://developer.riotgames.com/")
        print("Then create a .env file with: RIOT_API_KEY=your_key_here")
        return

    platform = os.getenv("TFT_PLATFORM", "na1")
    db_path = os.getenv("TFT_DB_PATH", "tft_matches.db")

    scraper = TFTScraper(api_key, platform, db_path)

    # Handle graceful shutdown
    loop = asyncio.get_event_loop()

    def signal_handler():
        print("\nReceived shutdown signal...")
        scraper.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, signal_handler)

    await scraper.run()


if __name__ == "__main__":
    asyncio.run(main())
