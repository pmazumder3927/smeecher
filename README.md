# smeecher

TFT match scraper and analysis tool with interactive filter graph visualization.

## Structure

```
smeecher/
├── src/
│   ├── scraper/          # Match scraping
│   │   ├── client.py     # Riot API client
│   │   ├── scraper.py    # Scraper logic
│   │   └── models.py     # Data models
│   ├── graph/            # Analysis & visualization
│   │   ├── index.py      # Inverted index builder
│   │   └── server.py     # Graph API server
│   └── db/               # Database
│       └── database.py   # SQLite storage
├── static/               # Web frontend
│   └── index.html        # Graph visualization
├── data/                 # Data files (gitignored)
│   ├── smeecher.db       # Match database
│   └── index.pkl         # Precomputed index
└── pyproject.toml
```

## Setup

```bash
# Install dependencies
uv sync

# Set up environment
cp .env.example .env
# Edit .env with your Riot API key
```

## Usage

### Scrape matches
```bash
uv run smeecher
```

### Build index
```bash
uv run smeecher-index
```

### Run graph server
```bash
uv run smeecher-graph
# Open http://localhost:8000
```

## Graph Features

- Search units/items to center the graph
- Click nodes to add constraints
- Edge thickness = placement impact
- Green = improves placement, Red = worsens placement
- Hover for detailed stats (avg placement, sample size)

## Data

Saves to `data/smeecher.db` (SQLite):

- `matches` - match metadata + lobby rank
- `player_matches` - placements, augments, traits
- `units` - units + items per player
- `ranks` - player rank snapshots
