"""
Query server for the optimized graph engine.

Uses the high-performance GraphEngine with roaring bitmaps
for sub-millisecond query response times at scale.
"""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Query, UploadFile, File, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn

from .engine import GraphEngine, build_engine


ENGINE: GraphEngine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    import os
    global ENGINE
    data_dir = Path(os.environ.get("DATA_DIR", "../data"))
    engine_path = data_dir / "engine.bin"
    db_path = data_dir / "smeecher.db"

    if engine_path.exists():
        ENGINE = GraphEngine.load(str(engine_path))
        stats = ENGINE.stats()
        print(f"Engine ready: {stats['total_tokens']} tokens, {stats['total_matches']} matches")
    elif db_path.exists():
        print("Engine not found, building from database...")
        ENGINE = build_engine(str(db_path), str(engine_path))
        stats = ENGINE.stats()
        print(f"Engine ready: {stats['total_tokens']} tokens, {stats['total_matches']} matches")
    else:
        print(f"WARNING: No data found in {data_dir}")
        print("Upload smeecher.db via POST /upload-data, then restart the service.")
        ENGINE = None

    yield


app = FastAPI(lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────
# Temporary upload endpoint - DELETE THIS AFTER UPLOADING DATA
# ─────────────────────────────────────────────────────────────────
import os
import secrets

UPLOAD_SECRET = os.environ.get("UPLOAD_SECRET", secrets.token_urlsafe(32))
print(f"\n{'='*60}")
print(f"UPLOAD SECRET: {UPLOAD_SECRET}")
print(f"{'='*60}\n")


from fastapi import Request

@app.put("/upload-data/{filename}")
async def upload_data(
    filename: str,
    request: Request,
    x_upload_secret: str = Header(..., alias="X-Upload-Secret")
):
    """
    Upload database or engine file to the data volume.
    DELETE THIS ENDPOINT AFTER USE.
    """
    if x_upload_secret != UPLOAD_SECRET:
        raise HTTPException(status_code=403, detail="Invalid secret")

    if filename not in ("smeecher.db", "engine.bin"):
        raise HTTPException(status_code=400, detail="Only smeecher.db or engine.bin allowed")

    data_dir = Path(os.environ.get("DATA_DIR", "../data"))
    data_dir.mkdir(parents=True, exist_ok=True)

    dest = data_dir / filename

    # Stream to file to handle large uploads
    with open(dest, "wb") as f:
        async for chunk in request.stream():
            f.write(chunk)

    size_mb = dest.stat().st_size / 1024 / 1024
    return {"status": "ok", "file": filename, "size_mb": size_mb}
# ─────────────────────────────────────────────────────────────────


def get_token_type(token: str) -> str:
    """Return 'unit', 'item', 'equipped', or 'trait'."""
    if token.startswith("U:"):
        return "unit"
    elif token.startswith("I:"):
        return "item"
    elif token.startswith("E:"):
        return "equipped"
    elif token.startswith("T:"):
        return "trait"
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
    elif token_type == "trait":
        # Handle tiered traits like T:Brawler:2
        parts = token[2:].split(":")
        if len(parts) == 2:
            return {"type": "trait", "trait": parts[0], "tier": int(parts[1])}
        return {"type": "trait", "trait": parts[0], "tier": None}
    return {"type": "unknown"}


def get_center_info(tokens: list[str]) -> dict:
    """Determine center type from tokens."""
    if not tokens:
        return {"type": "empty", "units": [], "items": [], "traits": []}

    units = []
    items = []
    equipped = []
    traits = []

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
        elif parsed["type"] == "trait":
            traits.append(parsed["trait"])

    return {
        "type": "combo" if len(tokens) > 1 else get_token_type(tokens[0]),
        "units": list(set(units)),
        "items": list(set(items)),
        "equipped": equipped,
        "traits": list(set(traits))
    }


def generate_candidates(center_info: dict, current_tokens: list[str]) -> list[tuple[str, str]]:
    """
    Generate candidate tokens based on center type.
    Returns list of (token, edge_type) tuples.

    Optimized: Uses engine's indexed token lists instead of scanning.
    """
    candidates = []
    current_set = set(current_tokens)

    all_units = ENGINE.get_all_tokens_by_type("U:")
    all_items = ENGINE.get_all_tokens_by_type("I:")
    all_equipped = ENGINE.get_all_tokens_by_type("E:")
    all_traits = [t for t in ENGINE.get_all_tokens_by_type("T:") if ":" not in t[2:]]  # Base traits only

    center_units = set(center_info["units"])
    center_items = set(center_info["items"])
    center_traits = set(center_info.get("traits", []))

    if center_info["type"] == "empty":
        # Show most popular units, items, and traits
        for unit_token in all_units:
            if unit_token not in current_set:
                candidates.append((unit_token, "cooccur"))
        for item_token in all_items:
            if item_token not in current_set:
                candidates.append((item_token, "cooccur"))
        for trait_token in all_traits:
            if trait_token not in current_set:
                candidates.append((trait_token, "cooccur"))

    elif center_info["type"] == "trait":
        # Trait-centered: show co-occurring units and traits
        for unit_token in all_units:
            if unit_token not in current_set:
                candidates.append((unit_token, "cooccur"))
        for trait_token in all_traits:
            if trait_token not in current_set:
                trait_name = trait_token[2:]
                if trait_name not in center_traits:
                    candidates.append((trait_token, "cooccur"))

    elif center_info["type"] == "item" or (center_info["items"] and not center_info["units"]):
        # Item-centered: show units that equip these items
        for item in center_items:
            for eq_token in all_equipped:
                if eq_token not in current_set and f"|{item}" in eq_token:
                    candidates.append((eq_token, "equipped"))

        # Also show co-occurring items
        for item_token in all_items:
            if item_token not in current_set:
                item_name = item_token[2:]
                if item_name not in center_items:
                    candidates.append((item_token, "cooccur"))

        # Show co-occurring traits
        for trait_token in all_traits:
            if trait_token not in current_set:
                candidates.append((trait_token, "cooccur"))

    elif center_info["type"] == "unit" or (center_info["units"] and not center_info["items"]):
        # Unit-centered: show items equipped on these units
        for unit in center_units:
            prefix = f"E:{unit}|"
            for eq_token in all_equipped:
                if eq_token not in current_set and eq_token.startswith(prefix):
                    candidates.append((eq_token, "equipped"))

        # Also show co-occurring units
        for unit_token in all_units:
            if unit_token not in current_set:
                unit_name = unit_token[2:]
                if unit_name not in center_units:
                    candidates.append((unit_token, "cooccur"))

        # Show co-occurring traits
        for trait_token in all_traits:
            if trait_token not in current_set:
                candidates.append((trait_token, "cooccur"))

    else:
        # Combo (unit + item via equipped edge)
        for unit in center_units:
            prefix = f"E:{unit}|"
            for eq_token in all_equipped:
                if eq_token not in current_set and eq_token.startswith(prefix):
                    # Check item not in center
                    item_name = eq_token.split("|")[1]
                    if item_name not in center_items:
                        candidates.append((eq_token, "equipped"))

        # Show supporting units
        for unit_token in all_units:
            if unit_token not in current_set:
                unit_name = unit_token[2:]
                if unit_name not in center_units:
                    candidates.append((unit_token, "cooccur"))

        # Show co-occurring traits
        for trait_token in all_traits:
            if trait_token not in current_set:
                trait_name = trait_token[2:]
                if trait_name not in center_traits:
                    candidates.append((trait_token, "cooccur"))

    return candidates


@app.get("/graph")
def get_graph(
    tokens: str = Query(default="", description="Comma-separated tokens"),
    min_sample: int = Query(default=10, description="Minimum sample size"),
    top_k: int = Query(default=15, description="Max edges to return (0 = unlimited)")
):
    """
    Get graph data for the given filter tokens.

    Uses roaring bitmap intersections for O(n) performance
    where n is the size of the smallest set.
    """
    token_list = [t.strip() for t in tokens.split(",") if t.strip()]

    if not token_list:
        # Special case: return all root nodes (units, items, base traits)
        all_units = ENGINE.get_all_tokens_by_type("U:")
        all_items = ENGINE.get_all_tokens_by_type("I:")
        all_traits = [t for t in ENGINE.get_all_tokens_by_type("T:") if ":" not in t[2:]]

        nodes = []
        for t in all_units:
            nodes.append({"id": t, "label": t[2:], "type": "unit", "isCenter": False})
        for t in all_items:
            nodes.append({"id": t, "label": t[2:], "type": "item", "isCenter": False})
        for t in all_traits:
            nodes.append({"id": t, "label": t[2:], "type": "trait", "isCenter": False})

        return {
            "center": [],
            "base": {
                "n": ENGINE.total_matches,
                "avg_placement": 4.5
            },
            "nodes": nodes,
            "edges": []
        }

    # Compute base set using roaring bitmap intersection
    base = ENGINE.intersect(token_list)
    n_base = len(base)
    avg_base = ENGINE.avg_placement_for_bitmap(base) if n_base > 0 else 4.5

    # Get center info
    center_info = get_center_info(token_list)

    # Generate candidates
    candidates = generate_candidates(center_info, token_list)

    # Extract just token strings for scoring
    candidate_tokens = [t for t, _ in candidates]
    edge_types = {t: e for t, e in candidates}

    # Score candidates using optimized engine
    scored = ENGINE.score_candidates(base, candidate_tokens, min_sample)

    # Add edge types
    for score in scored:
        score["edge_type"] = edge_types.get(score["token"], "cooccur")

    # Sort by absolute delta (most impactful first)
    scored.sort(key=lambda x: abs(x["delta"]), reverse=True)

    # Apply top_k limit if specified
    if top_k > 0:
        scored = scored[:top_k]

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
        elif parsed["type"] == "trait":
            trait_label = parsed["trait"]
            if parsed.get("tier"):
                node_id = f"T:{parsed['trait']}:{parsed['tier']}"
                trait_label = f"{parsed['trait']} {parsed['tier']}"
            else:
                node_id = f"T:{parsed['trait']}"
            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": trait_label,
                    "type": "trait",
                    "isCenter": True
                })
                node_ids.add(node_id)
        elif parsed["type"] == "equipped":
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
    for score in scored:
        parsed = parse_token(score["token"])

        if parsed["type"] == "equipped":
            from_id = f"U:{parsed['unit']}"
            to_id = f"I:{parsed['item']}"

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
            node_id = f"U:{parsed['unit']}"
            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": parsed["unit"],
                    "type": "unit",
                    "isCenter": False
                })
                node_ids.add(node_id)

            if token_list:
                center_parsed = parse_token(token_list[0])
                if center_parsed["type"] == "unit":
                    from_id = f"U:{center_parsed['unit']}"
                elif center_parsed["type"] == "item":
                    from_id = f"I:{center_parsed['item']}"
                elif center_parsed["type"] == "equipped":
                    from_id = f"U:{center_parsed['unit']}"
                elif center_parsed["type"] == "trait":
                    from_id = f"T:{center_parsed['trait']}"
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
            node_id = f"I:{parsed['item']}"
            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": parsed["item"],
                    "type": "item",
                    "isCenter": False
                })
                node_ids.add(node_id)

            if token_list:
                center_parsed = parse_token(token_list[0])
                if center_parsed["type"] == "unit":
                    from_id = f"U:{center_parsed['unit']}"
                elif center_parsed["type"] == "item":
                    from_id = f"I:{center_parsed['item']}"
                elif center_parsed["type"] == "equipped":
                    from_id = f"I:{center_parsed['item']}"
                elif center_parsed["type"] == "trait":
                    from_id = f"T:{center_parsed['trait']}"
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

        elif parsed["type"] == "trait":
            trait_label = parsed["trait"]
            if parsed.get("tier"):
                node_id = f"T:{parsed['trait']}:{parsed['tier']}"
                trait_label = f"{parsed['trait']} {parsed['tier']}"
            else:
                node_id = f"T:{parsed['trait']}"

            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": trait_label,
                    "type": "trait",
                    "isCenter": False
                })
                node_ids.add(node_id)

            if token_list:
                center_parsed = parse_token(token_list[0])
                if center_parsed["type"] == "unit":
                    from_id = f"U:{center_parsed['unit']}"
                elif center_parsed["type"] == "item":
                    from_id = f"I:{center_parsed['item']}"
                elif center_parsed["type"] == "equipped":
                    from_id = f"U:{center_parsed['unit']}"
                elif center_parsed["type"] == "trait":
                    from_id = f"T:{center_parsed['trait']}"
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
            "avg_placement": round(avg_base, 3)
        },
        "nodes": nodes,
        "edges": edges
    }


@app.get("/search")
def search_tokens(q: str = Query(..., description="Search query")):
    """Search for tokens matching query."""
    q_lower = q.lower()
    results = []

    for token_str in ENGINE.id_to_token:
        label = ENGINE.get_label(token_str)
        if q_lower in label.lower():
            count = ENGINE.get_token_count(token_str)
            results.append({
                "token": token_str,
                "label": label,
                "type": get_token_type(token_str),
                "count": count
            })

    results.sort(key=lambda x: x["count"], reverse=True)
    return results[:20]


@app.get("/stats")
def get_stats():
    """Return engine statistics."""
    return ENGINE.stats()


# Serve static files (Vite build output)
# server/src/graph/server.py -> server/src/graph -> server/src -> server -> root
static_path = Path(__file__).parent.parent.parent.parent / "static"

# Serve assets directory for JS/CSS bundles
assets_path = static_path / "assets"
if assets_path.exists():
    app.mount("/assets", StaticFiles(directory=str(assets_path)), name="assets")

# Also mount static for backwards compatibility
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.get("/riot.txt")
def riot_verification():
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse("78b19ad2-1989-43e3-93e6-96e803af504c")


@app.get("/")
def read_root():
    return FileResponse(str(static_path / "index.html"))


def main():
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
