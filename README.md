# smeecher

tft match scraper. Continuously scrapes games from Riot's API, storing units, items, placements, and lobby ranks.

## Setup

```bash
uv venv && uv sync
cp .env.example .env
# Add your API key from https://developer.riotgames.com/
```

## Run

```bash
uv run python main.py
```

## Data

Saves to `smeecher.db` (SQLite):

- `matches` - match metadata + lobby rank
- `player_matches` - placements, augments, traits
- `units` - units + items per player
- `ranks` - player rank snapshots
