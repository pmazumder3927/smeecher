"""Query server for the filter graph."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from .index import load_index


# Load index on startup
INDEX = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global INDEX
    INDEX = load_index("data/index.pkl")
    print(f"Loaded index with {len(INDEX['index'])} tokens")
    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


def avg_placement(pm_ids: set, placements: dict) -> float:
    """Compute average placement for a set of player match IDs."""
    if not pm_ids:
        return 4.5  # neutral default
    total = sum(placements[pm_id] for pm_id in pm_ids)
    return total / len(pm_ids)


def compute_base(tokens: list[str]) -> set:
    """Compute intersection of all token sets."""
    if not tokens:
        return set(INDEX["metadata"].keys())

    sets = []
    for token in tokens:
        if token in INDEX["index"]:
            sets.append(INDEX["index"][token])
        else:
            return set()  # Token not found, empty result

    if not sets:
        return set()

    result = sets[0].copy()
    for s in sets[1:]:
        result &= s
    return result


def get_token_type(token: str) -> str:
    """Return 'unit', 'item', or 'equipped'."""
    if token.startswith("U:"):
        return "unit"
    elif token.startswith("I:"):
        return "item"
    elif token.startswith("E:"):
        return "equipped"
    return "unknown"


def parse_token(token: str) -> dict:
    """Parse token into components."""
    token_type = get_token_type(token)
    if token_type == "unit":
        return {"type": "unit", "unit": token[2:]}
    elif token_type == "item":
        return {"type": "item", "item": token[2:]}
    elif token_type == "equipped":
        parts = token[2:].split("|")
        return {"type": "equipped", "unit": parts[0], "item": parts[1]}
    return {"type": "unknown"}


def score_candidates(base: set, placements: dict, candidates: list[str], min_sample: int = 10) -> list[dict]:
    """
    Score candidate tokens for edge weights using average placement.

    Returns list of {token, delta, avg_with, avg_base, n_with, n_base}
    Delta is negative when the candidate improves placement (lower is better).
    """
    n_base = len(base)
    if n_base == 0:
        return []

    avg_base = avg_placement(base, placements)

    results = []
    for token in candidates:
        if token not in INDEX["index"]:
            continue

        with_set = base & INDEX["index"][token]
        n_with = len(with_set)

        if n_with < min_sample:
            continue

        avg_with = avg_placement(with_set, placements)
        delta = avg_with - avg_base  # negative = better placement

        results.append({
            "token": token,
            "delta": round(delta, 2),
            "avg_with": round(avg_with, 2),
            "avg_base": round(avg_base, 2),
            "n_with": n_with,
            "n_base": n_base
        })

    return results


def get_center_info(tokens: list[str]) -> dict:
    """Determine center type from tokens."""
    if not tokens:
        return {"type": "empty", "units": [], "items": []}

    units = []
    items = []
    equipped = []

    for t in tokens:
        parsed = parse_token(t)
        if parsed["type"] == "unit":
            units.append(parsed["unit"])
        elif parsed["type"] == "item":
            items.append(parsed["item"])
        elif parsed["type"] == "equipped":
            units.append(parsed["unit"])
            items.append(parsed["item"])
            equipped.append((parsed["unit"], parsed["item"]))

    return {
        "type": "combo" if len(tokens) > 1 else get_token_type(tokens[0]),
        "units": list(set(units)),
        "items": list(set(items)),
        "equipped": equipped
    }


def generate_candidates(center_info: dict, current_tokens: list[str]) -> list[tuple[str, str]]:
    """
    Generate candidate tokens based on center type.
    Returns list of (token, edge_type) tuples.
    """
    candidates = []
    index = INDEX["index"]

    # Get all units and items from index
    all_units = [t[2:] for t in index if t.startswith("U:")]
    all_items = [t[2:] for t in index if t.startswith("I:")]

    center_units = set(center_info["units"])
    center_items = set(center_info["items"])

    if center_info["type"] == "empty":
        # Show most popular units and items
        for unit in all_units:
            candidates.append((f"U:{unit}", "cooccur"))
        for item in all_items:
            candidates.append((f"I:{item}", "cooccur"))

    elif center_info["type"] == "item" or (center_info["items"] and not center_info["units"]):
        # Item-centered: show units that equip these items
        for item in center_items:
            for unit in all_units:
                equipped_token = f"E:{unit}|{item}"
                if equipped_token in index:
                    candidates.append((equipped_token, "equipped"))

        # Also show co-occurring items
        for item in all_items:
            if item not in center_items:
                candidates.append((f"I:{item}", "cooccur"))

    elif center_info["type"] == "unit" or (center_info["units"] and not center_info["items"]):
        # Unit-centered: show items equipped on these units
        for unit in center_units:
            for item in all_items:
                equipped_token = f"E:{unit}|{item}"
                if equipped_token in index:
                    candidates.append((equipped_token, "equipped"))

        # Also show co-occurring units
        for unit in all_units:
            if unit not in center_units:
                candidates.append((f"U:{unit}", "cooccur"))

    else:
        # Combo (unit + item via equipped edge): show other items for same unit, and supporting units
        for unit in center_units:
            for item in all_items:
                if item not in center_items:
                    equipped_token = f"E:{unit}|{item}"
                    if equipped_token in index:
                        candidates.append((equipped_token, "equipped"))

        # Show supporting units
        for unit in all_units:
            if unit not in center_units:
                candidates.append((f"U:{unit}", "cooccur"))

    # Filter out tokens already in selection
    candidates = [(t, e) for t, e in candidates if t not in current_tokens]

    return candidates


@app.get("/graph")
def get_graph(
    tokens: str = Query(default="", description="Comma-separated tokens"),
    min_sample: int = Query(default=10, description="Minimum sample size"),
    top_k: int = Query(default=15, description="Max edges to return")
):
    """
    Get graph data for the given filter tokens.

    Example tokens:
    - I:InfinityEdge
    - U:Jinx
    - E:Jinx|InfinityEdge
    """
    token_list = [t.strip() for t in tokens.split(",") if t.strip()]

    # Compute base set
    base = compute_base(token_list)
    n_base = len(base)
    placements = INDEX["placements"]
    avg_base = avg_placement(base, placements) if n_base > 0 else 4.5

    # Get center info
    center_info = get_center_info(token_list)

    # Generate candidates
    candidates = generate_candidates(center_info, token_list)

    # Score candidates
    scored = []
    for token, edge_type in candidates:
        scores = score_candidates(base, placements, [token], min_sample)
        if scores:
            score = scores[0]
            score["edge_type"] = edge_type
            scored.append(score)

    # Sort by absolute delta and take top k
    scored.sort(key=lambda x: abs(x["delta"]), reverse=True)
    top_edges = scored[:top_k]

    # Build nodes
    nodes = []
    node_ids = set()

    # Add center nodes
    for t in token_list:
        parsed = parse_token(t)
        if parsed["type"] == "unit":
            node_id = f"U:{parsed['unit']}"
            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": parsed["unit"],
                    "type": "unit",
                    "isCenter": True
                })
                node_ids.add(node_id)
        elif parsed["type"] == "item":
            node_id = f"I:{parsed['item']}"
            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": parsed["item"],
                    "type": "item",
                    "isCenter": True
                })
                node_ids.add(node_id)
        elif parsed["type"] == "equipped":
            # Add both unit and item as center nodes
            unit_id = f"U:{parsed['unit']}"
            item_id = f"I:{parsed['item']}"
            if unit_id not in node_ids:
                nodes.append({
                    "id": unit_id,
                    "label": parsed["unit"],
                    "type": "unit",
                    "isCenter": True
                })
                node_ids.add(unit_id)
            if item_id not in node_ids:
                nodes.append({
                    "id": item_id,
                    "label": parsed["item"],
                    "type": "item",
                    "isCenter": True
                })
                node_ids.add(item_id)

    # Build edges and add neighbor nodes
    edges = []
    for score in top_edges:
        parsed = parse_token(score["token"])

        if parsed["type"] == "equipped":
            # Edge from unit to item
            from_id = f"U:{parsed['unit']}"
            to_id = f"I:{parsed['item']}"

            # Add nodes if not present
            if from_id not in node_ids:
                nodes.append({
                    "id": from_id,
                    "label": parsed["unit"],
                    "type": "unit",
                    "isCenter": False
                })
                node_ids.add(from_id)
            if to_id not in node_ids:
                nodes.append({
                    "id": to_id,
                    "label": parsed["item"],
                    "type": "item",
                    "isCenter": False
                })
                node_ids.add(to_id)

            edges.append({
                "from": from_id,
                "to": to_id,
                "token": score["token"],
                "type": "equipped",
                "delta": score["delta"],
                "avg_with": score["avg_with"],
                "avg_base": score["avg_base"],
                "n_with": score["n_with"],
                "n_base": score["n_base"]
            })

        elif parsed["type"] == "unit":
            # Co-occurrence edge to unit
            node_id = f"U:{parsed['unit']}"
            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": parsed["unit"],
                    "type": "unit",
                    "isCenter": False
                })
                node_ids.add(node_id)

            # Connect to first center node
            if token_list:
                center_parsed = parse_token(token_list[0])
                if center_parsed["type"] == "unit":
                    from_id = f"U:{center_parsed['unit']}"
                elif center_parsed["type"] == "item":
                    from_id = f"I:{center_parsed['item']}"
                elif center_parsed["type"] == "equipped":
                    from_id = f"U:{center_parsed['unit']}"
                else:
                    from_id = node_id
            else:
                from_id = node_id

            edges.append({
                "from": from_id,
                "to": node_id,
                "token": score["token"],
                "type": "cooccur",
                "delta": score["delta"],
                "avg_with": score["avg_with"],
                "avg_base": score["avg_base"],
                "n_with": score["n_with"],
                "n_base": score["n_base"]
            })

        elif parsed["type"] == "item":
            # Co-occurrence edge to item
            node_id = f"I:{parsed['item']}"
            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": parsed["item"],
                    "type": "item",
                    "isCenter": False
                })
                node_ids.add(node_id)

            # Connect to first center node
            if token_list:
                center_parsed = parse_token(token_list[0])
                if center_parsed["type"] == "unit":
                    from_id = f"U:{center_parsed['unit']}"
                elif center_parsed["type"] == "item":
                    from_id = f"I:{center_parsed['item']}"
                elif center_parsed["type"] == "equipped":
                    from_id = f"I:{center_parsed['item']}"
                else:
                    from_id = node_id
            else:
                from_id = node_id

            edges.append({
                "from": from_id,
                "to": node_id,
                "token": score["token"],
                "type": "cooccur",
                "delta": score["delta"],
                "avg_with": score["avg_with"],
                "avg_base": score["avg_base"],
                "n_with": score["n_with"],
                "n_base": score["n_base"]
            })

    return {
        "center": token_list,
        "base": {
            "n": n_base,
            "avg_placement": round(avg_base, 2)
        },
        "nodes": nodes,
        "edges": edges
    }


@app.get("/search")
def search_tokens(q: str = Query(..., description="Search query")):
    """Search for tokens matching query."""
    q = q.lower()
    results = []

    for token, label in INDEX["labels"].items():
        if q in label.lower():
            count = len(INDEX["index"][token])
            results.append({
                "token": token,
                "label": label,
                "type": get_token_type(token),
                "count": count
            })

    # Sort by count and limit
    results.sort(key=lambda x: x["count"], reverse=True)
    return results[:20]


# Serve static files
static_path = Path(__file__).parent.parent.parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/")
def read_root():
    return FileResponse(str(static_path / "index.html"))


def main():
    uvicorn.run(app, host="0.0.0.0", port=8000)


if __name__ == "__main__":
    main()
