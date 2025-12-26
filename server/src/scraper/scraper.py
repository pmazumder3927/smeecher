"""Continuous TFT match scraper."""
import asyncio
import os
import signal
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from rich.console import Console
from rich.live import Live
from rich.table import Table
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn

from .client import TFTClient
from .models import ScrapedMatch
from ..db import Database


TIER_VALUE = {
    "IRON": 0, "BRONZE": 400, "SILVER": 800, "GOLD": 1200,
    "PLATINUM": 1600, "EMERALD": 2000, "DIAMOND": 2400,
    "MASTER": 2800, "GRANDMASTER": 3200, "CHALLENGER": 3600
}
TIER_NAMES = list(TIER_VALUE.keys())


def rank_to_str(tier: str, rank: str, lp: int) -> str:
    if tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        return f"{tier[:2]} {lp}LP"
    return f"{tier[:1]}{rank} {lp}LP"


def avg_rank_str(ranks: list) -> str:
    if not ranks:
        return "???"
    avg = sum(ranks) // len(ranks)
    tier_idx = min(avg // 400, 9)
    tier = TIER_NAMES[tier_idx]
    if tier in ("MASTER", "GRANDMASTER", "CHALLENGER"):
        return f"{tier[:2]} {avg - tier_idx * 400}LP"
    rank_idx = (avg % 400) // 100
    return f"{tier[:1]}{'IV III II I'.split()[rank_idx]}"


console = Console()


class Smeecher:
    """TFT match scraper with pretty output."""

    def __init__(self, api_key: str, platform: str = "na1", db_path: str = "../data/smeecher.db"):
        self.client = TFTClient(api_key, platform)
        self.db = Database(db_path)
        self.platform = platform.upper()
        self.running = False
        self.matches = 0
        self.players_scraped = 0
        self.start_time = None
        self.current_player = ""
        self.current_match = ""
        self.last_rank = ""

    def make_status_table(self) -> Table:
        table = Table(show_header=False, box=None, padding=(0, 1))
        table.add_column(style="cyan", width=12)
        table.add_column(style="white")

        runtime = str(datetime.now() - self.start_time).split(".")[0] if self.start_time else "0:00:00"
        rate = self.matches / max(1, (datetime.now() - self.start_time).total_seconds() / 60) if self.start_time else 0

        table.add_row("Runtime", runtime)
        table.add_row("Matches", f"[green]{self.matches}[/green]")
        table.add_row("Players", f"{self.players_scraped}")
        table.add_row("Rate", f"{rate:.1f}/min")
        table.add_row("Queue", f"{self.db.queue_size()}")
        table.add_row("", "")
        table.add_row("Player", f"[dim]{self.current_player[:12]}...[/dim]" if self.current_player else "[dim]---[/dim]")
        table.add_row("Match", f"[dim]{self.current_match[-12:]}[/dim]" if self.current_match else "[dim]---[/dim]")
        table.add_row("Rank", f"[yellow]{self.last_rank}[/yellow]" if self.last_rank else "[dim]---[/dim]")

        return table

    async def seed(self, progress: Progress, task_id):
        """Seed queue from top players."""
        leagues = [
            ("Challenger", self.client.get_challenger),
            ("Grandmaster", self.client.get_grandmaster),
            ("Master", self.client.get_master),
        ]

        total = 0
        for name, get_league in leagues:
            progress.update(task_id, description=f"[cyan]Fetching {name}...")
            league = await get_league()
            if league and "entries" in league:
                for entry in league["entries"][:50]:
                    if puuid := entry.get("puuid"):
                        # refresh=True ensures ladder players get high priority and are scraped immediately
                        self.db.add_to_queue(puuid, priority=TIER_VALUE.get(name.upper(), 0), refresh=True)
                        total += 1
            await asyncio.sleep(0.5)

        progress.update(task_id, description=f"[green]Seeded {total} players")
        return total

    async def scrape_player(self, puuid: str) -> int:
        """Scrape matches for a player."""
        self.current_player = puuid
        match_ids = await self.client.get_match_ids(puuid, count=20)
        new = 0

        for match_id in match_ids:
            if self.db.match_exists(match_id):
                continue

            self.current_match = match_id
            match_data = await self.client.get_match(match_id)
            if not match_data:
                continue

            match_info, players = self.client.parse_match(match_data)

            # Get ranks for first 3 players
            ranks = []
            for p in players[:3]:
                if rank := await self.client.get_player_rank(p.puuid):
                    self.db.save_rank(rank)
                    ranks.append(TIER_VALUE.get(rank.tier, 0) + rank.league_points)
                    self.last_rank = rank_to_str(rank.tier, rank.rank, rank.league_points)

            # Queue all players (batch insert)
            self.db.add_to_queue_batch([p.puuid for p in players])

            # Save match
            self.db.save_match(ScrapedMatch(
                match_info=match_info,
                players=players,
                lobby_avg_rank=avg_rank_str(ranks) if ranks else None,
            ))
            new += 1
            self.matches += 1

        self.players_scraped += 1
        self.db.mark_scraped(puuid)
        return new

    async def run(self):
        """Main scraping loop."""
        self.running = True
        self.start_time = datetime.now()

        console.print(Panel.fit(
            f"[bold cyan]smeecher[/bold cyan] [dim]v0.1.0[/dim]\n"
            f"[dim]Platform: {self.platform}[/dim]",
            border_style="cyan"
        ))

        # Always seed from ladder on startup to prioritize fresh top-ladder matches
        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            console=console,
        ) as progress:
            task = progress.add_task("[cyan]Seeding from ladder...", total=None)
            await self.seed(progress, task)

        console.print("[dim]Press Ctrl+C to stop[/dim]\n")

        # Main loop with live display
        prune_counter = 0
        with Live(self.make_status_table(), refresh_per_second=2, console=console) as live:
            while self.running:
                puuid = self.db.get_next()
                if not puuid:
                    with Progress(SpinnerColumn(), TextColumn("{task.description}"), console=console) as p:
                        task = p.add_task("[yellow]Queue empty, reseeding...")
                        await self.seed(p, task)
                    continue

                try:
                    await self.scrape_player(puuid)
                    live.update(self.make_status_table())

                    # Prune old queue entries every 100 players
                    prune_counter += 1
                    if prune_counter >= 100:
                        self.db.prune_queue(keep_hours=24)
                        prune_counter = 0
                except Exception as e:
                    console.print(f"[red]Error: {e}[/red]")
                    await asyncio.sleep(1)

        await self.shutdown()

    async def shutdown(self):
        console.print(f"\n[cyan]Shutting down...[/cyan]")
        stats = self.db.get_stats()
        console.print(Panel(
            f"[bold]Session Complete[/bold]\n\n"
            f"Matches scraped: [green]{self.matches}[/green]\n"
            f"Total in DB: [cyan]{stats['total_matches']}[/cyan]\n"
            f"Unique players: [cyan]{stats['unique_players']}[/cyan]",
            border_style="green"
        ))
        await self.client.close()
        self.db.close()

    def stop(self):
        self.running = False


async def _async_main():
    load_dotenv()

    api_key = os.getenv("RIOT_API_KEY")
    if not api_key:
        console.print("[red]Error: RIOT_API_KEY not set[/red]")
        console.print("[dim]Get your key from https://developer.riotgames.com/[/dim]")
        return

    platform = os.getenv("TFT_PLATFORM", "na1")
    data_dir = Path(os.environ.get("DATA_DIR", "../data"))
    db_path = str(data_dir / "smeecher.db")

    # Ensure data directory exists
    data_dir.mkdir(parents=True, exist_ok=True)

    scraper = Smeecher(api_key, platform, db_path)

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, scraper.stop)

    await scraper.run()


def main():
    """CLI entry point for the scraper."""
    asyncio.run(_async_main())


if __name__ == "__main__":
    main()
