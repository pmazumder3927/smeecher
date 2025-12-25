"""
Query server for the optimized graph engine.

Uses the high-performance GraphEngine with roaring bitmaps
for sub-millisecond query response times at scale.
"""
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # Load .env file

from fastapi import FastAPI, Query, UploadFile, File, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import anthropic
import httpx
import websockets
import base64
import asyncio

from .engine import GraphEngine, build_engine
from .clustering import ClusterParams, compute_clusters

# Initialize Anthropic client (uses ANTHROPIC_API_KEY env var)
ANTHROPIC_CLIENT = None
if os.environ.get("ANTHROPIC_API_KEY"):
    try:
        ANTHROPIC_CLIENT = anthropic.Anthropic()
        print("Anthropic client initialized")
    except Exception as e:
        print(f"Failed to initialize Anthropic client: {e}")

# OpenAI API key for voice transcription and parsing
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if OPENAI_API_KEY:
    print("OpenAI API key loaded for voice transcription")
else:
    print("Warning: OPENAI_API_KEY not set - voice features disabled")


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
    top_k: int = Query(default=15, description="Max edges to return (0 = unlimited)"),
    types: str = Query(default="unit,item,trait", description="Comma-separated types to include"),
    sort_mode: str = Query(default="impact", description="Sort mode: impact (abs delta), helpful (most negative delta), harmful (most positive delta)")
):
    """
    Get graph data for the given filter tokens.

    Uses roaring bitmap intersections for O(n) performance
    where n is the size of the smallest set.
    """
    token_list = [t.strip() for t in tokens.split(",") if t.strip()]
    active_types = set(t.strip().lower() for t in types.split(",") if t.strip())

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

    # Filter by active types before applying top_k
    # equipped edges are included if either unit or item is in active_types
    def matches_type_filter(score_entry):
        token_type = get_token_type(score_entry["token"])
        if token_type == "equipped":
            # Include equipped edges if either unit or item type is active
            return "unit" in active_types or "item" in active_types
        return token_type in active_types

    scored = [s for s in scored if matches_type_filter(s)]

    # Sort based on sort_mode
    if sort_mode == "helpful":
        # Most helpful first (most negative delta = improves placement most)
        scored.sort(key=lambda x: x["delta"])
    elif sort_mode == "harmful":
        # Most harmful first (most positive delta = worsens placement most)
        scored.sort(key=lambda x: x["delta"], reverse=True)
    else:
        # Default: impact (abs delta, most impactful first)
        scored.sort(key=lambda x: abs(x["delta"]), reverse=True)

    # Apply top_k limit if specified (now applied to filtered results)
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


@app.get("/clusters")
def get_clusters(
    tokens: str = Query(default="", description="Comma-separated tokens (filters)"),
    n_clusters: int = Query(default=15, ge=2, le=50),
    use_units: bool = Query(default=True),
    use_traits: bool = Query(default=True),
    use_items: bool = Query(default=False),
    min_token_freq: int = Query(default=100, ge=1),
    min_cluster_size: int = Query(default=50, ge=1),
    top_k_tokens: int = Query(default=10, ge=1, le=30),
    random_state: int = Query(default=42),
):
    """
    Cluster comps (player matches) into archetypes within the current filters.

    This is intended for interactive exploration in the frontend.
    """
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    token_list = [t.strip() for t in tokens.split(",") if t.strip()]
    params = ClusterParams(
        n_clusters=n_clusters,
        use_units=use_units,
        use_traits=use_traits,
        use_items=use_items,
        min_token_freq=min_token_freq,
        min_cluster_size=min_cluster_size,
        top_k_tokens=top_k_tokens,
        random_state=random_state,
    )
    return compute_clusters(ENGINE, token_list, params)


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


# Cache for voice parsing vocabulary context
_voice_vocab_cache = None


def _get_voice_vocab():
    """Get cached vocabulary for voice parsing."""
    global _voice_vocab_cache
    if _voice_vocab_cache is None and ENGINE is not None:
        units = [t[2:] for t in ENGINE.id_to_token if t.startswith("U:")]
        traits = [t[2:] for t in ENGINE.id_to_token if t.startswith("T:") and ":" not in t[2:]]

        # Build unit name -> token lookup for case-insensitive matching
        unit_lookup = {}
        for t in ENGINE.id_to_token:
            if t.startswith("U:"):
                name = t[2:]
                unit_lookup[name.lower()] = t

        # Build item label -> token lookup for fast matching
        item_lookup = {}
        items = []
        for t in ENGINE.id_to_token:
            if t.startswith("I:"):
                label = ENGINE.get_label(t)
                key = label.lower().replace(" ", "")
                if key not in item_lookup:
                    item_lookup[key] = t
                    items.append(label)

        # Build trait name -> token lookup
        trait_lookup = {}
        for t in ENGINE.id_to_token:
            if t.startswith("T:") and ":" not in t[2:]:
                name = t[2:]
                trait_lookup[name.lower()] = t

        _voice_vocab_cache = {
            "units": sorted(units),
            "items": sorted(set(items)),
            "traits": sorted(traits),
            "unit_lookup": unit_lookup,
            "item_lookup": item_lookup,
            "trait_lookup": trait_lookup,
        }
    return _voice_vocab_cache


def _fuzzy_lookup(name: str, lookup: dict) -> str | None:
    """Try to find a match with fuzzy matching for plurals and common variations."""
    key = name.lower().replace(" ", "")

    # Try exact match first
    if key in lookup:
        return lookup[key]

    # Try removing trailing 's' (plurals)
    if key.endswith('s') and key[:-1] in lookup:
        return lookup[key[:-1]]

    # Try removing trailing 'es' (plurals like "cashes" -> "cash")
    if key.endswith('es') and key[:-2] in lookup:
        return lookup[key[:-2]]

    return None


def _validate_voice_tokens(tool_args: dict, vocab: dict) -> list:
    """Validate and convert tool call arguments to tokens."""
    tokens = []
    seen_tokens = set()
    unit_lookup = vocab.get("unit_lookup", {})
    item_lookup = vocab.get("item_lookup", {})
    trait_lookup = vocab.get("trait_lookup", {})

    def add_token(token, label, token_type):
        if token not in seen_tokens:
            seen_tokens.add(token)
            tokens.append({"token": token, "label": label, "type": token_type})

    # Handle units - fuzzy lookup with cross-category fallback
    for unit_name in tool_args.get("units", []):
        unit_token = _fuzzy_lookup(unit_name, unit_lookup)
        if unit_token:
            add_token(unit_token, ENGINE.get_label(unit_token), "unit")
        else:
            # Try as trait (model might miscategorize)
            trait_token = _fuzzy_lookup(unit_name, trait_lookup)
            if trait_token:
                add_token(trait_token, trait_token[2:], "trait")

    # Handle items - fuzzy lookup with cross-category fallback
    for item_name in tool_args.get("items", []):
        item_token = _fuzzy_lookup(item_name, item_lookup)
        if item_token:
            add_token(item_token, ENGINE.get_label(item_token), "item")
        else:
            # Try as trait
            trait_token = _fuzzy_lookup(item_name, trait_lookup)
            if trait_token:
                add_token(trait_token, trait_token[2:], "trait")

    # Handle traits - fuzzy lookup with tier support and cross-category fallback
    for trait in tool_args.get("traits", []):
        name = trait.get("name", "") if isinstance(trait, dict) else str(trait)
        tier = trait.get("tier") if isinstance(trait, dict) else None
        base_token = _fuzzy_lookup(name, trait_lookup)

        if base_token:
            trait_name = base_token[2:]  # Remove "T:" prefix
            if tier and tier >= 2:
                token = f"T:{trait_name}:{tier}"
                label = f"{trait_name} {tier}"
            else:
                token = base_token
                label = trait_name
            add_token(token, label, "trait")
        else:
            # Try as unit (model might miscategorize)
            unit_token = _fuzzy_lookup(name, unit_lookup)
            if unit_token:
                add_token(unit_token, ENGINE.get_label(unit_token), "unit")

    return tokens


@app.post("/voice-parse")
async def parse_voice_input(audio: UploadFile = File(...)):
    """
    Parse voice audio into TFT tokens using OpenAI Realtime API.

    Uses gpt-realtime with tool calling to transcribe and parse in one step.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Voice features unavailable (no OpenAI API key)")

    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    vocab = _get_voice_vocab()
    if not vocab:
        raise HTTPException(status_code=503, detail="Vocabulary not loaded")

    # Read audio data
    audio_data = await audio.read()
    if len(audio_data) < 100:
        raise HTTPException(status_code=400, detail="Audio file too small")

    # Build system instructions - vocab is in tool enums
    instructions = """You are a TFT (Teamfight Tactics) voice command parser. Extract ALL game entities the user mentions.

ITEM ABBREVIATIONS (expand before calling tool):
hoj=Hand of Justice, jg=Jeweled Gauntlet, tg=Thief's Gloves, ie=Infinity Edge, bt=Bloodthirster, gs=Giant Slayer, lw=Last Whisper, db=Deathblade, ga=Guardian Angel, qss=Quicksilver, rfc=Rapid Firecannon, rb=Guinsoo's Rageblade, tr=Titan's Resolve, dcap=Rabadon's Deathcap, shiv=Statikk Shiv, shojin=Spear of Shojin, blue=Blue Buff, nashors=Nashor's Tooth, archangels=Archangel's Staff, morello=Morellonomicon, sunfire=Sunfire Cape, ionic=Ionic Spark, shroud=Shroud of Stillness, eon=Edge of Night, gargoyle=Gargoyle Stoneplate, bramble=Bramble Vest, dclaw=Dragon's Claw, warmogs=Warmog's Armor, zzrot=Zz'Rot Portal, hullbreaker=Hullcrusher, cg=Crownguard

RULES:
1. Extract EVERY entity - do not miss any
2. Numbers before/after traits = tier (e.g., "5 demacia" = Demacia tier 5)
3. Match phonetically similar words to the closest enum value
4. Always call add_search_filters with everything detected"""

    # Define the tool with enums to constrain model output
    tool_definition = {
        "type": "function",
        "name": "add_search_filters",
        "description": "Add TFT game filters. Use ONLY values from the provided enums.",
        "parameters": {
            "type": "object",
            "properties": {
                "units": {
                    "type": "array",
                    "items": {"type": "string", "enum": vocab['units']},
                    "description": "Champion/unit names"
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string", "enum": vocab['items']},
                    "description": "Item names (expand abbreviations first)"
                },
                "traits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "enum": vocab['traits']},
                            "tier": {"type": "integer", "description": "Trait tier level (2-9)"}
                        },
                        "required": ["name"]
                    },
                    "description": "Trait filters with optional tier"
                }
            }
        }
    }

    try:
        # Connect to OpenAI Realtime API
        url = "wss://api.openai.com/v1/realtime?model=gpt-realtime"
        headers = {
            "Authorization": f"Bearer {OPENAI_API_KEY}",
            "OpenAI-Beta": "realtime=v1",
        }

        transcript = ""
        tool_args = None

        async with websockets.connect(url, additional_headers=headers) as ws:
            # Send session configuration with tool
            session_update = {
                "type": "session.update",
                "session": {
                    "modalities": ["text", "audio"],
                    "instructions": instructions,
                    "input_audio_format": "pcm16",
                    "input_audio_transcription": {"model": "whisper-1"},
                    "tools": [tool_definition],
                    "tool_choice": "required",
                    "temperature": 0.6,
                }
            }
            await ws.send(json.dumps(session_update))

            # Wait for session.updated confirmation
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
                if msg.get("type") == "session.updated":
                    break
                if msg.get("type") == "error":
                    raise HTTPException(status_code=502, detail=f"OpenAI error: {msg}")

            # Send audio data as base64 PCM chunks
            # Note: Browser sends WebM, we need to convert or use a compatible format
            # For now, send as-is and let OpenAI handle it
            audio_b64 = base64.b64encode(audio_data).decode()

            # Append audio buffer
            await ws.send(json.dumps({
                "type": "input_audio_buffer.append",
                "audio": audio_b64
            }))

            # Commit the audio buffer
            await ws.send(json.dumps({
                "type": "input_audio_buffer.commit"
            }))

            # Request a response
            await ws.send(json.dumps({
                "type": "response.create"
            }))

            # Wait for function call or completion
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15.0))
                msg_type = msg.get("type", "")

                # Capture transcript if available
                if msg_type == "conversation.item.input_audio_transcription.completed":
                    transcript = msg.get("transcript", "")

                # Check for function call
                if msg_type == "response.function_call_arguments.done":
                    args_str = msg.get("arguments", "{}")
                    tool_args = json.loads(args_str)
                    break

                # Check for response completion without function call
                if msg_type == "response.done":
                    break

                # Handle errors
                if msg_type == "error":
                    raise HTTPException(status_code=502, detail=f"OpenAI error: {msg.get('error', {})}")

        # Validate tokens from tool call
        if tool_args:
            tokens = _validate_voice_tokens(tool_args, vocab)
            return {"tokens": tokens, "transcript": transcript, "raw_parse": tool_args}
        else:
            return {"tokens": [], "transcript": transcript, "error": "No entities detected"}

    except asyncio.TimeoutError:
        raise HTTPException(status_code=504, detail="Voice processing timed out")
    except websockets.exceptions.WebSocketException as e:
        raise HTTPException(status_code=502, detail=f"WebSocket error: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/stats")
def get_stats():
    """Return engine statistics."""
    return ENGINE.stats()


@app.get("/unit-build")
def get_unit_build(
    unit: str = Query(..., description="Unit name (e.g., MissFortune)"),
    tokens: str = Query(default="", description="Additional filter tokens (comma-separated)"),
    min_sample: int = Query(default=30, description="Minimum sample size for inclusion"),
    slots: int = Query(default=3, ge=1, le=3, description="Number of item slots to fill")
):
    """
    Get multiple optimal item builds for a unit.

    This endpoint explores different starting items and finds the best
    complete build path from each, returning multiple build options
    ranked by final average placement.
    """
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    # Parse additional filter tokens
    filter_tokens = [t.strip() for t in tokens.split(",") if t.strip()]

    # Build the unit token
    unit_token = f"U:{unit}"

    if unit_token not in ENGINE.token_to_id:
        raise HTTPException(status_code=404, detail=f"Unit '{unit}' not found")

    # Track which items are already in filters
    existing_items_base = set()
    for t in filter_tokens:
        parsed = parse_token(t)
        if parsed["type"] == "item":
            existing_items_base.add(parsed["item"])
        elif parsed["type"] == "equipped" and parsed["unit"] == unit:
            existing_items_base.add(parsed["item"])

    # Get base stats
    base_tokens = [unit_token] + filter_tokens
    base_bitmap = ENGINE.intersect(base_tokens)
    n_base = len(base_bitmap)
    avg_base = ENGINE.avg_placement_for_bitmap(base_bitmap) if n_base > 0 else 4.5

    if n_base < min_sample:
        return {
            "unit": unit,
            "filters": filter_tokens,
            "base": {"n": n_base, "avg_placement": round(avg_base, 3)},
            "builds": []
        }

    # Find all equipped tokens for this unit
    prefix = f"E:{unit}|"
    all_equipped = [t for t in ENGINE.id_to_token if t.startswith(prefix)]

    def find_best_item(current_bitmap, used_items):
        """Find the best item given current state."""
        n_current = len(current_bitmap)
        avg_current = ENGINE.avg_placement_for_bitmap(current_bitmap) if n_current > 0 else 4.5

        best = None
        for eq_token in all_equipped:
            item_name = eq_token.split("|")[1]
            if item_name in used_items:
                continue

            token_id = ENGINE.token_to_id.get(eq_token)
            if token_id is None or token_id not in ENGINE.tokens:
                continue

            token_stats = ENGINE.tokens[token_id]
            with_bitmap = current_bitmap & token_stats.bitmap
            n_with = len(with_bitmap)

            if n_with < min_sample:
                continue

            avg_with = ENGINE.avg_placement_for_bitmap(with_bitmap)
            delta = avg_with - avg_current

            if best is None or delta < best["delta"]:
                best = {
                    "item": item_name,
                    "token": eq_token,
                    "delta": round(delta, 3),
                    "avg_placement": round(avg_with, 3),
                    "n": n_with,
                    "bitmap": with_bitmap
                }

        return best

    def build_from_start(start_item, start_bitmap, start_stats):
        """Build a complete item set starting from a specific item."""
        build_items = [start_stats]
        current_bitmap = start_bitmap
        used = existing_items_base | {start_item}

        for slot_idx in range(1, slots):
            next_item = find_best_item(current_bitmap, used)
            if next_item is None:
                break
            build_items.append({
                "item": next_item["item"],
                "token": next_item["token"],
                "delta": next_item["delta"],
                "avg_placement": next_item["avg_placement"],
                "n": next_item["n"],
                "slot": slot_idx + 1
            })
            used.add(next_item["item"])
            current_bitmap = next_item["bitmap"]

        # Calculate final stats
        final_avg = build_items[-1]["avg_placement"] if build_items else avg_base
        final_n = build_items[-1]["n"] if build_items else n_base

        return {
            "items": [{**item, "slot": i + 1} for i, item in enumerate(build_items)],
            "final_avg": final_avg,
            "final_n": final_n,
            "total_delta": round(final_avg - avg_base, 3),
            "num_items": len(build_items)
        }

    # Find all starting items (slot 1 candidates)
    starting_candidates = []

    for eq_token in all_equipped:
        item_name = eq_token.split("|")[1]
        if item_name in existing_items_base:
            continue

        token_id = ENGINE.token_to_id.get(eq_token)
        if token_id is None or token_id not in ENGINE.tokens:
            continue

        token_stats = ENGINE.tokens[token_id]
        with_bitmap = base_bitmap & token_stats.bitmap
        n_with = len(with_bitmap)

        if n_with < min_sample:
            continue

        avg_with = ENGINE.avg_placement_for_bitmap(with_bitmap)
        delta = avg_with - avg_base

        starting_candidates.append({
            "item": item_name,
            "token": eq_token,
            "delta": round(delta, 3),
            "avg_placement": round(avg_with, 3),
            "n": n_with,
            "bitmap": with_bitmap,
            "slot": 1
        })

    # Sort by delta (best first)
    starting_candidates.sort(key=lambda x: x["delta"])

    # Build complete builds from each starting point
    all_builds = []
    seen_build_keys = set()

    for start in starting_candidates:
        build = build_from_start(start["item"], start["bitmap"], {
            "item": start["item"],
            "token": start["token"],
            "delta": start["delta"],
            "avg_placement": start["avg_placement"],
            "n": start["n"],
            "slot": 1
        })

        # Only include builds with all slots filled
        if build["num_items"] < slots:
            continue

        # Deduplicate by item set (order doesn't matter for dedup)
        build_key = tuple(sorted(item["item"] for item in build["items"]))
        if build_key in seen_build_keys:
            continue
        seen_build_keys.add(build_key)

        all_builds.append(build)

    # Sort by final average placement (best first)
    all_builds.sort(key=lambda x: x["final_avg"])

    return {
        "unit": unit,
        "filters": filter_tokens,
        "base": {
            "n": n_base,
            "avg_placement": round(avg_base, 3)
        },
        "builds": all_builds
    }


@app.get("/unit-items")
def get_unit_items(
    unit: str = Query(..., description="Unit name (e.g., MissFortune)"),
    tokens: str = Query(default="", description="Additional filter tokens (comma-separated)"),
    min_sample: int = Query(default=30, description="Minimum sample size for inclusion"),
    top_k: int = Query(default=0, description="Max items to return (0 = unlimited)"),
    sort_mode: str = Query(default="helpful", description="Sort mode: helpful (best avg), harmful (worst avg), impact (abs delta)")
):
    """
    Get best items for a specific unit given current filters.

    This endpoint returns all items that can be equipped on the unit,
    ranked by their impact on placement when equipped on this specific unit.

    The key insight is that we use E:{unit}|{item} (equipped) tokens which
    track actual item-on-unit performance, not just co-occurrence.
    """
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    # Parse additional filter tokens
    filter_tokens = [t.strip() for t in tokens.split(",") if t.strip()]

    # Build the unit token
    unit_token = f"U:{unit}"

    # Check unit exists
    if unit_token not in ENGINE.token_to_id:
        raise HTTPException(status_code=404, detail=f"Unit '{unit}' not found")

    # Build base set: unit + any additional filters
    base_tokens = [unit_token] + filter_tokens
    base_bitmap = ENGINE.intersect(base_tokens)
    n_base = len(base_bitmap)

    if n_base == 0:
        return {
            "unit": unit,
            "filters": filter_tokens,
            "base": {"n": 0, "avg_placement": 4.5},
            "items": []
        }

    avg_base = ENGINE.avg_placement_for_bitmap(base_bitmap)

    # Find all equipped tokens for this unit: E:{unit}|*
    prefix = f"E:{unit}|"
    equipped_tokens = [t for t in ENGINE.id_to_token if t.startswith(prefix)]

    # Track which items are already in filters (to exclude from recommendations)
    existing_items = set()
    for t in filter_tokens:
        parsed = parse_token(t)
        if parsed["type"] == "item":
            existing_items.add(parsed["item"])
        elif parsed["type"] == "equipped" and parsed["unit"] == unit:
            existing_items.add(parsed["item"])

    # Score each equipped token
    results = []
    for eq_token in equipped_tokens:
        item_name = eq_token.split("|")[1]

        # Skip items already in filters
        if item_name in existing_items:
            continue

        token_id = ENGINE.token_to_id.get(eq_token)
        if token_id is None or token_id not in ENGINE.tokens:
            continue

        token_stats = ENGINE.tokens[token_id]

        # Intersect with base set
        with_bitmap = base_bitmap & token_stats.bitmap
        n_with = len(with_bitmap)

        if n_with < min_sample:
            continue

        avg_with = ENGINE.avg_placement_for_bitmap(with_bitmap)
        delta = avg_with - avg_base

        results.append({
            "item": item_name,
            "token": eq_token,
            "delta": round(delta, 3),
            "avg_placement": round(avg_with, 3),
            "n": n_with,
            "pct_of_base": round(n_with / n_base * 100, 1) if n_base > 0 else 0
        })

    # Sort based on sort_mode
    if sort_mode == "helpful":
        # Best items first (most negative delta = improves placement most)
        results.sort(key=lambda x: x["delta"])
    elif sort_mode == "harmful":
        # Worst items first (most positive delta = worsens placement most)
        results.sort(key=lambda x: x["delta"], reverse=True)
    else:
        # Impact: absolute delta, most impactful first
        results.sort(key=lambda x: abs(x["delta"]), reverse=True)

    # Apply top_k limit
    if top_k > 0:
        results = results[:top_k]

    return {
        "unit": unit,
        "filters": filter_tokens,
        "base": {
            "n": n_base,
            "avg_placement": round(avg_base, 3)
        },
        "items": results
    }


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
