"""
Query server for the optimized graph engine.

Uses the high-performance GraphEngine with roaring bitmaps
for sub-millisecond query response times at scale.
"""
import json
import os
import threading
import time
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()  # Load .env file

from fastapi import FastAPI, Query, UploadFile, File, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import httpx

from .engine import GraphEngine, build_engine
from .clustering import ClusterParams, compute_clusters

# OpenAI API key for voice transcription and parsing
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
if OPENAI_API_KEY:
    print("OpenAI API key loaded for voice features")
else:
    print("Warning: OPENAI_API_KEY not set - voice features disabled")


def _env_int(key: str, default: int, *, min_value: int | None = None, max_value: int | None = None) -> int:
    try:
        value = int(os.environ.get(key, str(default)))
    except Exception:
        value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


def _env_float(key: str, default: float, *, min_value: float | None = None, max_value: float | None = None) -> float:
    try:
        value = float(os.environ.get(key, str(default)))
    except Exception:
        value = default
    if min_value is not None:
        value = max(min_value, value)
    if max_value is not None:
        value = min(max_value, value)
    return value


VOICE_RATE_LIMIT_ENABLED = os.environ.get("VOICE_RATE_LIMIT_ENABLED", "1") != "0"
VOICE_MAX_SDP_BYTES = _env_int("VOICE_MAX_SDP_BYTES", 50_000, min_value=1_000, max_value=500_000)
VOICE_MIN_SECONDS_BETWEEN_SESSIONS = _env_float("VOICE_MIN_SECONDS_BETWEEN_SESSIONS", 2.0, min_value=0.0, max_value=60.0)
VOICE_MAX_SESSIONS_PER_IP_PER_MINUTE = _env_int("VOICE_MAX_SESSIONS_PER_IP_PER_MINUTE", 12, min_value=1, max_value=10_000)
VOICE_MAX_SESSIONS_PER_IP_PER_HOUR = _env_int("VOICE_MAX_SESSIONS_PER_IP_PER_HOUR", 120, min_value=1, max_value=100_000)
VOICE_MAX_SESSIONS_GLOBAL_PER_MINUTE = _env_int("VOICE_MAX_SESSIONS_GLOBAL_PER_MINUTE", 120, min_value=1, max_value=100_000)
VOICE_MAX_CONCURRENT_SESSION_CREATIONS = _env_int("VOICE_MAX_CONCURRENT_SESSION_CREATIONS", 4, min_value=1, max_value=1000)

_voice_lock = threading.Lock()
_voice_ip_state: dict[str, dict] = {}
_voice_global_state = {
    "minute": deque(),
    "concurrent": 0,
}


def _get_client_ip(request: Request) -> str:
    # Optional proxy support; only trust if explicitly enabled.
    if os.environ.get("TRUST_PROXY_HEADERS", "0") == "1":
        xff = request.headers.get("x-forwarded-for")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _prune_times(q: deque, now: float, window_s: float) -> None:
    cutoff = now - window_s
    while q and q[0] < cutoff:
        q.popleft()


def _enforce_voice_rate_limits(request: Request) -> None:
    if not VOICE_RATE_LIMIT_ENABLED:
        return

    now = time.time()
    ip = _get_client_ip(request)

    with _voice_lock:
        st = _voice_ip_state.get(ip)
        if st is None:
            st = {"last": 0.0, "minute": deque(), "hour": deque()}
            _voice_ip_state[ip] = st

        _prune_times(st["minute"], now, 60.0)
        _prune_times(st["hour"], now, 3600.0)
        _prune_times(_voice_global_state["minute"], now, 60.0)

        # Basic concurrency guard (prevents thundering herds / runaway retries).
        if _voice_global_state["concurrent"] >= VOICE_MAX_CONCURRENT_SESSION_CREATIONS:
            raise HTTPException(
                status_code=429,
                detail="Voice is busy right now. Try again in a few seconds.",
                headers={"Retry-After": "5"},
            )

        # Per-IP cooldown
        last = float(st.get("last") or 0.0)
        if VOICE_MIN_SECONDS_BETWEEN_SESSIONS > 0 and last > 0:
            since = now - last
            if since < VOICE_MIN_SECONDS_BETWEEN_SESSIONS:
                retry = max(1, int(VOICE_MIN_SECONDS_BETWEEN_SESSIONS - since) + 1)
                raise HTTPException(
                    status_code=429,
                    detail="Please wait a moment before using voice again.",
                    headers={"Retry-After": str(retry)},
                )

        # Per-IP minute/hour limits
        if len(st["minute"]) >= VOICE_MAX_SESSIONS_PER_IP_PER_MINUTE:
            retry = 30
            if st["minute"]:
                retry = max(1, int(60 - (now - st["minute"][0])) + 1)
            raise HTTPException(
                status_code=429,
                detail="Voice rate limit exceeded. Try again soon.",
                headers={"Retry-After": str(retry)},
            )
        if len(st["hour"]) >= VOICE_MAX_SESSIONS_PER_IP_PER_HOUR:
            retry = 300
            if st["hour"]:
                retry = max(1, int(3600 - (now - st["hour"][0])) + 1)
            raise HTTPException(
                status_code=429,
                detail="Voice hourly limit exceeded. Try again later.",
                headers={"Retry-After": str(retry)},
            )

        # Global minute limit (helps protect against broad abuse).
        if len(_voice_global_state["minute"]) >= VOICE_MAX_SESSIONS_GLOBAL_PER_MINUTE:
            retry = 5
            if _voice_global_state["minute"]:
                retry = max(1, int(60 - (now - _voice_global_state["minute"][0])) + 1)
            raise HTTPException(
                status_code=429,
                detail="Voice is temporarily rate-limited. Try again shortly.",
                headers={"Retry-After": str(retry)},
            )

        # Record the attempt + reserve a concurrency slot.
        st["last"] = now
        st["minute"].append(now)
        st["hour"].append(now)
        _voice_global_state["minute"].append(now)
        _voice_global_state["concurrent"] += 1


def _release_voice_concurrency_slot() -> None:
    if not VOICE_RATE_LIMIT_ENABLED:
        return
    with _voice_lock:
        if _voice_global_state["concurrent"] > 0:
            _voice_global_state["concurrent"] -= 1


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


_COMPONENT_ITEMS: set[str] = {
    "BFSword",
    "ChainVest",
    "GiantsBelt",
    "NeedlesslyLargeRod",
    "NegatronCloak",
    "RecurveBow",
    "SparringGloves",
    "Spatula",
    "TearOfTheGoddess",
}


def get_item_type(item_name: str) -> str:
    """
    Best-effort item categorization for filtering.

    Returns one of: component, full, artifact, emblem, radiant
    """
    if item_name in _COMPONENT_ITEMS:
        return "component"
    if item_name.endswith("Radiant"):
        return "radiant"
    if item_name.startswith("Artifact_") or "Item_Ornn" in item_name:
        return "artifact"
    if item_name.endswith("EmblemItem") or item_name.startswith("TFT_Item_Emblem_"):
        return "emblem"
    return "full"


def get_item_prefix(item_name: str) -> str | None:
    """
    Best-effort "set prefix" for filtering *full* items that use a Name_Pattern.

    Examples:
      - Bilgewater_CaptainsBrew -> Bilgewater
    """
    item_type = get_item_type(item_name)
    if item_type != "full":
        return None

    if "_" in item_name:
        prefix = item_name.split("_", 1)[0]
        if prefix.upper().startswith("TFT") or prefix.lower().startswith("set"):
            return None
        return prefix or None

    return None


_ITEM_TYPE_ALIASES: dict[str, str] = {
    "component": "component",
    "components": "component",
    "comp": "component",
    "full": "full",
    "fullitem": "full",
    "full_item": "full",
    "full-item": "full",
    "completed": "full",
    "complete": "full",
    "artifact": "artifact",
    "artifacts": "artifact",
    "emblem": "emblem",
    "emblems": "emblem",
    "radiant": "radiant",
    "radiantitem": "radiant",
    "radiant_item": "radiant",
    "radiant-item": "radiant",
}


def _parse_item_types_param(item_types: str) -> set[str] | None:
    """
    Parse item_types query param into normalized set.
    Empty/unknown-only input => None (no filtering).
    """
    raw = [t.strip() for t in (item_types or "").split(",") if t.strip()]
    if not raw:
        return None
    normalized = {_ITEM_TYPE_ALIASES.get(t.lower(), t.lower()) for t in raw}
    allowed = {"component", "full", "artifact", "emblem", "radiant"}
    normalized = {t for t in normalized if t in allowed}
    return normalized or None


def _parse_item_prefixes_param(item_prefixes: str) -> set[str]:
    """
    Parse item_prefixes query param into normalized set (case-insensitive).

    Semantics:
      - Base items (no prefix) are always included.
      - Prefixed "set" items (e.g., Bilgewater_*) are only included when their prefix
        is present in this set.
      - Empty input => empty set (i.e., exclude all prefixed set items by default).
    """
    raw = [t.strip() for t in (item_prefixes or "").split(",") if t.strip()]
    normalized = {t.rstrip("_").lower() for t in raw if t.rstrip("_")}
    return normalized


def parse_token(token: str) -> dict:
    """Parse token into components."""
    token_type = get_token_type(token)
    if token_type == "unit":
        rest = token[2:]
        # Star-level units are encoded as U:UnitName:2 (2★), U:UnitName:3 (3★), etc.
        if ":" in rest:
            unit, maybe_stars = rest.rsplit(":", 1)
            try:
                stars = int(maybe_stars)
            except ValueError:
                stars = None
            if stars is not None:
                return {"type": "unit", "unit": unit, "stars": stars}
        return {"type": "unit", "unit": rest, "stars": None}
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

    # Only include base unit tokens as candidates. Star-level unit tokens (U:Unit:2)
    # are available via search, but excluding them here prevents noisy/duplicative
    # suggestions and an explosion of root nodes.
    all_units = [t for t in ENGINE.get_all_tokens_by_type("U:") if ":" not in t[2:]]
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
    sort_mode: str = Query(default="impact", description="Sort mode: impact (abs delta), helpful (most negative delta), harmful (most positive delta)"),
    item_types: str = Query(
        default="",
        description="Item types to include (comma-separated): component, full, artifact, emblem, radiant",
    ),
    item_prefixes: str = Query(
        default="",
        description="Item prefixes to include (comma-separated, case-insensitive), e.g. Bilgewater",
    ),
):
    """
    Get graph data for the given filter tokens.

    Uses roaring bitmap intersections for O(n) performance
    where n is the size of the smallest set.
    """
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    token_list = [t.strip() for t in tokens.split(",") if t.strip()]
    active_types = set(t.strip().lower() for t in types.split(",") if t.strip())
    allowed_item_types = _parse_item_types_param(item_types)
    allowed_item_prefixes = _parse_item_prefixes_param(item_prefixes)

    if not token_list:
        # Special case: return all root nodes (units, items, base traits)
        all_units = [t for t in ENGINE.get_all_tokens_by_type("U:") if ":" not in t[2:]]
        all_items = ENGINE.get_all_tokens_by_type("I:")
        all_traits = [t for t in ENGINE.get_all_tokens_by_type("T:") if ":" not in t[2:]]

        # Always apply set-prefix filtering:
        # - Base items (no prefix) are always included
        # - Prefixed set items are excluded unless selected via item_prefixes
        def _item_allowed_root(item_name: str) -> bool:
            item_type = get_item_type(item_name)
            if allowed_item_types is not None and item_type not in allowed_item_types:
                return False
            prefix = get_item_prefix(item_name)
            if prefix and prefix.lower() not in allowed_item_prefixes:
                return False
            return True

        all_items = [t for t in all_items if _item_allowed_root(t[2:])]

        nodes = []
        for t in all_units:
            nodes.append({"id": t, "label": ENGINE.get_label(t), "type": "unit", "isCenter": False})
        for t in all_items:
            nodes.append({"id": t, "label": ENGINE.get_label(t), "type": "item", "isCenter": False})
        for t in all_traits:
            nodes.append({"id": t, "label": ENGINE.get_label(t), "type": "trait", "isCenter": False})

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

    # Narrow which item candidates are shown:
    # - Base items (no prefix) are always included
    # - Prefixed set items are excluded unless selected via item_prefixes
    center_items = set(center_info.get("items") or [])

    def _item_allowed(item_name: str) -> bool:
        item_type = get_item_type(item_name)
        if allowed_item_types is not None and item_type not in allowed_item_types:
            return False
        prefix = get_item_prefix(item_name)
        if prefix and prefix.lower() not in allowed_item_prefixes:
            return False
        return True

    filtered = []
    for tok, edge_type in candidates:
        parsed = parse_token(tok)
        if parsed["type"] == "item":
            if not _item_allowed(parsed["item"]):
                continue
        elif parsed["type"] == "equipped":
            # Preserve equipped edges for explicitly-selected center items
            if parsed["item"] not in center_items and not _item_allowed(parsed["item"]):
                continue
        filtered.append((tok, edge_type))
    candidates = filtered

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
                    "label": ENGINE.get_label(node_id),
                    "type": "unit",
                    "isCenter": True
                })
                node_ids.add(node_id)
        elif parsed["type"] == "item":
            node_id = f"I:{parsed['item']}"
            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": ENGINE.get_label(node_id),
                    "type": "item",
                    "isCenter": True
                })
                node_ids.add(node_id)
        elif parsed["type"] == "trait":
            if parsed.get("tier"):
                node_id = f"T:{parsed['trait']}:{parsed['tier']}"
            else:
                node_id = f"T:{parsed['trait']}"
            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": ENGINE.get_label(node_id),
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
                    "label": ENGINE.get_label(unit_id),
                    "type": "unit",
                    "isCenter": True
                })
                node_ids.add(unit_id)
            if item_id not in node_ids:
                nodes.append({
                    "id": item_id,
                    "label": ENGINE.get_label(item_id),
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
                    "label": ENGINE.get_label(from_id),
                    "type": "unit",
                    "isCenter": False
                })
                node_ids.add(from_id)
            if to_id not in node_ids:
                nodes.append({
                    "id": to_id,
                    "label": ENGINE.get_label(to_id),
                    "type": "item",
                    "isCenter": False
                })
                node_ids.add(to_id)

            edges.append({
                "from": from_id,
                "to": to_id,
                "token": score["token"],
                "label": ENGINE.get_label(score["token"]),
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
                    "label": ENGINE.get_label(node_id),
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
                    if center_parsed.get("tier"):
                        from_id = f"T:{center_parsed['trait']}:{center_parsed['tier']}"
                    else:
                        from_id = f"T:{center_parsed['trait']}"
                else:
                    from_id = node_id
            else:
                from_id = node_id

            edges.append({
                "from": from_id,
                "to": node_id,
                "token": score["token"],
                "label": ENGINE.get_label(score["token"]),
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
                    "label": ENGINE.get_label(node_id),
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
                    if center_parsed.get("tier"):
                        from_id = f"T:{center_parsed['trait']}:{center_parsed['tier']}"
                    else:
                        from_id = f"T:{center_parsed['trait']}"
                else:
                    from_id = node_id
            else:
                from_id = node_id

            edges.append({
                "from": from_id,
                "to": node_id,
                "token": score["token"],
                "label": ENGINE.get_label(score["token"]),
                "type": "cooccur",
                "delta": score["delta"],
                "avg_with": score["avg_with"],
                "avg_base": score["avg_base"],
                "n_with": score["n_with"],
                "n_base": score["n_base"]
            })

        elif parsed["type"] == "trait":
            if parsed.get("tier"):
                node_id = f"T:{parsed['trait']}:{parsed['tier']}"
            else:
                node_id = f"T:{parsed['trait']}"

            if node_id not in node_ids:
                nodes.append({
                    "id": node_id,
                    "label": ENGINE.get_label(node_id),
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
                    if center_parsed.get("tier"):
                        from_id = f"T:{center_parsed['trait']}:{center_parsed['tier']}"
                    else:
                        from_id = f"T:{center_parsed['trait']}"
                else:
                    from_id = node_id
            else:
                from_id = node_id

            edges.append({
                "from": from_id,
                "to": node_id,
                "token": score["token"],
                "label": ENGINE.get_label(score["token"]),
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
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    q_norm = _normalize_search_text(q)
    if not q_norm:
        return []

    results = []
    for entry in _get_search_index():
        if q_norm in entry["_label_norm"] or q_norm in entry["_token_norm"]:
            results.append({
                "token": entry["token"],
                "label": entry["label"],
                "type": entry["type"],
                "count": entry["count"],
            })

    results.sort(key=lambda x: x["count"], reverse=True)
    return results[:20]


# Cache for client-side search index
_search_index_cache = None


def _normalize_search_text(text: str) -> str:
    """Normalize search text for fast, forgiving matching."""
    return "".join(ch for ch in text.lower() if ch.isalnum())


def _get_search_index():
    """
    Build or return cached search index.

    Includes equipped tokens (E:*), so the client can build fast UIs that treat
    equipped items as properties of a unit without extra backend round-trips.
    """
    global _search_index_cache
    if _search_index_cache is not None:
        return _search_index_cache

    if ENGINE is None:
        return []

    entries = []
    for token_str in ENGINE.id_to_token:
        if not (
            token_str.startswith("U:")
            or token_str.startswith("I:")
            or token_str.startswith("T:")
            or token_str.startswith("E:")
        ):
            continue

        label = ENGINE.get_label(token_str)
        entries.append({
            "token": token_str,
            "label": label,
            "type": get_token_type(token_str),
            "count": ENGINE.get_token_count(token_str),
            "_label_norm": _normalize_search_text(label),
            "_token_norm": _normalize_search_text(token_str),
        })

    _search_index_cache = entries
    return _search_index_cache


@app.get("/search-index")
def get_search_index():
    """Return full search index for client-side search (no per-keystroke API calls)."""
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    return [
        {"token": e["token"], "label": e["label"], "type": e["type"], "count": e["count"]}
        for e in _get_search_index()
    ]


# Cache for item filter options (prefixes/types)
_item_filters_cache = None


@app.get("/item-filters")
def get_item_filters():
    """
    Return available item filter options for the UI.

    These filters are *not* match constraints; they are used to narrow which item
    candidates are shown/recommended (e.g., include Bilgewater_* full items).
    """
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    global _item_filters_cache
    if _item_filters_cache is not None:
        return _item_filters_cache

    # Build from item presence tokens (I:*) so it matches graph item nodes.
    item_names = [t[2:] for t in ENGINE.id_to_token if t.startswith("I:")]

    type_counts: dict[str, int] = {k: 0 for k in ("component", "full", "artifact", "emblem", "radiant")}
    prefix_to_items: dict[str, set[str]] = {}

    for name in item_names:
        item_type = get_item_type(name)
        if item_type in type_counts:
            type_counts[item_type] += 1

        prefix = get_item_prefix(name)
        if prefix:
            prefix_to_items.setdefault(prefix, set()).add(name)

    # Only show prefixes that actually represent a "set" (2+ distinct items).
    prefixes = [
        {"key": p, "items": sorted(items), "n_items": len(items)}
        for p, items in prefix_to_items.items()
        if len(items) >= 2
    ]
    prefixes.sort(key=lambda x: (-x["n_items"], x["key"].lower()))

    _item_filters_cache = {
        "item_types": [
            {"key": "full", "label": "Full items", "n_items": type_counts["full"]},
            {"key": "radiant", "label": "Radiant", "n_items": type_counts["radiant"]},
            {"key": "artifact", "label": "Artifacts", "n_items": type_counts["artifact"]},
            {"key": "emblem", "label": "Emblems", "n_items": type_counts["emblem"]},
            {"key": "component", "label": "Components", "n_items": type_counts["component"]},
        ],
        "item_prefixes": prefixes,
    }
    return _item_filters_cache


# Cache for voice parsing vocabulary context
_voice_vocab_cache = None


def _get_voice_vocab():
    """Get cached vocabulary for voice parsing."""
    global _voice_vocab_cache
    if _voice_vocab_cache is None and ENGINE is not None:
        import re

        unit_tokens = [t for t in ENGINE.id_to_token if t.startswith("U:")]
        base_unit_tokens = [t for t in unit_tokens if ":" not in t[2:]]
        star_unit_tokens = [t for t in unit_tokens if ":" in t[2:]]
        units = [ENGINE.get_label(t) for t in base_unit_tokens + star_unit_tokens]
        base_trait_tokens = [t for t in ENGINE.id_to_token if t.startswith("T:") and ":" not in t[2:]]

        def _strip_breakpoint(label: str) -> str:
            # Engine trait labels include the inferred first breakpoint number (e.g. "Demacia 3").
            return re.sub(r"(?:\s|:)\d+\s*$", "", label or "").strip()

        # Build unit label -> token lookup for forgiving matching
        unit_lookup = {}
        for t in base_unit_tokens + star_unit_tokens:
            unit_id = t[2:]
            label = ENGINE.get_label(t)
            keys = [
                label.lower().replace(" ", ""),
                _normalize_search_text(label),
                unit_id.lower().replace(" ", ""),
                _normalize_search_text(unit_id),
            ]
            for key in keys:
                if key and key not in unit_lookup:
                    unit_lookup[key] = t

        # Build item label -> token lookup for fast matching
        item_lookup = {}
        items = []
        for t in ENGINE.id_to_token:
            if t.startswith("I:"):
                label = ENGINE.get_label(t)
                keys = [
                    label.lower().replace(" ", ""),
                    _normalize_search_text(label),
                    t[2:].lower(),  # canonical item id (e.g. RunaansHurricane)
                    _normalize_search_text(t[2:]),
                ]
                added_any = False
                for key in keys:
                    if key and key not in item_lookup:
                        item_lookup[key] = t
                        added_any = True
                if added_any:
                    items.append(label)

        # Build trait name -> token lookup
        trait_lookup = {}
        traits = []
        for t in base_trait_tokens:
            trait_id = t[2:]
            label = ENGINE.get_label(t)
            display = _strip_breakpoint(label) or trait_id
            traits.append(display)

            keys = [
                display.lower().replace(" ", ""),
                _normalize_search_text(display),
                trait_id.lower().replace(" ", ""),
                _normalize_search_text(trait_id),
            ]
            for key in keys:
                if key and key not in trait_lookup:
                    trait_lookup[key] = t

        # Build trait label (with inferred breakpoint numbers) -> token lookup.
        # Example: "Demacia 5" -> "T:Demacia:2"
        trait_tier_lookup = {}
        for t in ENGINE.id_to_token:
            if not t.startswith("T:"):
                continue
            label = ENGINE.get_label(t)
            keys = [
                label.lower().replace(" ", ""),
                _normalize_search_text(label),
            ]
            for key in keys:
                if key and key not in trait_tier_lookup:
                    trait_tier_lookup[key] = t

        _voice_vocab_cache = {
            "units": sorted(set(units)),
            "items": sorted(set(items)),
            "traits": sorted(set(traits)),
            "unit_lookup": unit_lookup,
            "item_lookup": item_lookup,
            "trait_lookup": trait_lookup,
            "trait_tier_lookup": trait_tier_lookup,
        }
    return _voice_vocab_cache


def _fuzzy_lookup(name: str, lookup: dict) -> str | None:
    """Try to find a match with fuzzy matching for plurals and common variations."""
    raw = name.lower()
    keys = [
        raw.replace(" ", ""),
        _normalize_search_text(raw),
    ]

    for key in keys:
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


def _get_realtime_session_config():
    """Build the session config for OpenAI Realtime API (unified interface)."""
    # Simple config per docs - tools configured via data channel
    return {
        "type": "realtime",
        "model": "gpt-realtime",
    }


def _get_session_update_event():
    """Build session.update event to configure tools after connection."""
    vocab = _get_voice_vocab()
    if not vocab:
        return None

    instructions = """You are a TFT (Teamfight Tactics) voice command parser for the Smeecher UI. Extract ALL game entities the user mentions AND any UI intent.

ITEM ABBREVIATIONS (expand before calling tool):
hoj=Hand of Justice, jg=Jeweled Gauntlet, tg=Thief's Gloves, ie=Infinity Edge, bt=Bloodthirster, gs=Giant Slayer, lw=Last Whisper, db=Deathblade, ga=Guardian Angel, qss=Quicksilver, rfc=Rapid Firecannon, rb=Guinsoo's Rageblade, tr=Titan's Resolve, dcap=Rabadon's Deathcap, shiv=Statikk Shiv, shojin=Spear of Shojin, blue=Blue Buff, nashors=Nashor's Tooth, archangels=Archangel's Staff, morello=Morellonomicon, sunfire=Sunfire Cape, ionic=Ionic Spark, shroud=Shroud of Stillness, eon=Edge of Night, gargoyle=Gargoyle Stoneplate, bramble=Bramble Vest, dclaw=Dragon's Claw, warmogs=Warmog's Armor, zzrot=Zz'Rot Portal, hullbreaker=Hullcrusher, cg=Crownguard

UI INTENT (set these fields when the user asks for it):
- If the user asks for best items/builds (e.g., "best artifacts for ashe"), set open_item_explorer=true.
- If the user asks for best comp(s)/composition(s)/archetype(s) (e.g., "best yasuo comp"), set open_cluster_explorer=true (and run_cluster_explorer=true).
- If the user mentions item categories, set item_types to the matching keys: component, full, radiant, artifact, emblem.
- If the user says "best"/"top", set item_explorer_sort_mode="helpful"; if "worst"/"bad", set "harmful"; if "impact"/"most impact", set "impact".
- If the user says "build(s)", set item_explorer_tab="builds"; if they say "item(s)" or mention an item category, set item_explorer_tab="items".
- If the user says a unit is "with"/"holding"/"equipped with" an item, put that in equipped=[{unit, item}].

RULES:
1. Extract EVERY entity - do not miss any
2. Numbers before/after traits refer to the in-game breakpoint number (e.g., "5 demacia" = Demacia 5)
3. Numbers after unit names refer to star level (e.g., "Ambessa 2" = Ambessa 2★)
4. Match phonetically similar words to the closest enum value
5. Always call add_search_filters with everything detected"""

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
                },
                "open_item_explorer": {
                    "type": "boolean",
                    "description": "Open the Items panel (Item Explorer) and focus it for best items/builds queries"
                },
                "open_cluster_explorer": {
                    "type": "boolean",
                    "description": "Open the Explorer panel (Cluster Explorer) for comp/archetype exploration"
                },
                "run_cluster_explorer": {
                    "type": "boolean",
                    "description": "Run clustering now (refresh results for current filters)"
                },
                "item_explorer_tab": {
                    "type": "string",
                    "enum": ["builds", "items"],
                    "description": "Which tab to focus in the Items panel"
                },
                "item_explorer_sort_mode": {
                    "type": "string",
                    "enum": ["helpful", "harmful", "impact"],
                    "description": "Sort mode for the Items list (best/worst/impact)"
                },
                "item_types": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["component", "full", "radiant", "artifact", "emblem"]},
                    "description": "Item type filters to apply (candidate narrowing)"
                },
                "equipped": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "unit": {"type": "string", "enum": vocab['units']},
                            "item": {"type": "string", "enum": vocab['items']}
                        },
                        "required": ["unit", "item"]
                    },
                    "description": "Equipped items on a specific unit (use when user says unit WITH/HOLDING an item)"
                }
            }
        }
    }

    return {
        "type": "session.update",
        "session": {
            "type": "realtime",
            "instructions": instructions,
            "tools": [tool_definition],
            "tool_choice": "required",
        }
    }


@app.post("/realtime-session")
async def create_realtime_session(request: Request):
    """
    Create a WebRTC session for OpenAI Realtime API.

    Receives SDP offer from browser, forwards to OpenAI with session config,
    returns SDP answer for direct browser <-> OpenAI connection.
    """
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=503, detail="Voice features unavailable (no OpenAI API key)")

    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    session_config = _get_realtime_session_config()
    if not session_config:
        raise HTTPException(status_code=503, detail="Vocabulary not loaded")

    # Get SDP offer from browser (and reject absurd requests early).
    content_length = request.headers.get("content-length")
    if content_length:
        try:
            if int(content_length) > VOICE_MAX_SDP_BYTES:
                raise HTTPException(status_code=413, detail="SDP offer too large")
        except ValueError:
            # Ignore invalid header
            pass

    raw = await request.body()
    if not raw:
        raise HTTPException(status_code=400, detail="Missing SDP offer")
    if len(raw) > VOICE_MAX_SDP_BYTES:
        raise HTTPException(status_code=413, detail="SDP offer too large")

    try:
        sdp_offer = raw.decode("utf-8")
    except UnicodeDecodeError:
        sdp_offer = raw.decode("utf-8", errors="ignore")

    # Quick sanity checks to avoid spending API calls on garbage.
    if "m=audio" not in sdp_offer or "v=" not in sdp_offer:
        raise HTTPException(status_code=400, detail="Invalid SDP offer")

    # Guardrail: prevent abusive session creation.
    _enforce_voice_rate_limits(request)
    reserved_slot = True

    try:
        async with httpx.AsyncClient() as client:
            # Build multipart form data - use files with None filename for text fields
            response = await client.post(
                "https://api.openai.com/v1/realtime/calls",
                headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
                files={
                    'sdp': (None, sdp_offer),
                    'session': (None, json.dumps(session_config)),
                },
                timeout=10.0,
            )

            if not response.is_success:
                raise HTTPException(
                    status_code=response.status_code,
                    detail=f"OpenAI error: {response.text}"
                )

            # Return SDP answer to browser
            return Response(
                content=response.content,
                media_type="application/sdp"
            )

    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Failed to connect to OpenAI: {str(e)}")
    finally:
        # Make sure we always release the concurrency slot.
        if reserved_slot:
            _release_voice_concurrency_slot()


@app.get("/voice-vocab")
async def get_voice_vocab():
    """Return vocabulary for client-side token validation."""
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    vocab = _get_voice_vocab()
    if not vocab:
        raise HTTPException(status_code=503, detail="Vocabulary not loaded")

    return {
        "units": vocab['units'],
        "items": vocab['items'],
        "traits": vocab['traits'],
        "unit_lookup": vocab['unit_lookup'],
        "item_lookup": vocab['item_lookup'],
        "trait_lookup": vocab['trait_lookup'],
        "trait_tier_lookup": vocab.get("trait_tier_lookup") or {},
    }


@app.get("/voice-session-config")
async def get_voice_session_config():
    """Return session.update event for configuring voice session via data channel."""
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    config = _get_session_update_event()
    if not config:
        raise HTTPException(status_code=503, detail="Vocabulary not loaded")

    return config


@app.get("/stats")
def get_stats():
    """Return engine statistics."""
    return ENGINE.stats()


@app.get("/unit-build")
def get_unit_build(
    unit: str = Query(..., description="Unit name (e.g., MissFortune)"),
    tokens: str = Query(default="", description="Additional filter tokens (comma-separated)"),
    min_sample: int = Query(default=10, description="Minimum sample size for inclusion"),
    slots: int = Query(default=3, ge=1, le=3, description="Number of item slots to fill"),
    item_types: str = Query(
        default="",
        description="Item types to include (comma-separated): component, full, artifact, emblem, radiant",
    ),
    item_prefixes: str = Query(
        default="",
        description="Item prefixes to include (comma-separated, case-insensitive), e.g. Bilgewater",
    ),
):
    """
    Get multiple optimal item builds for a unit.

    This endpoint searches item combinations (beam search + small-sample shrinkage)
    to surface strong builds and better capture item interactions than greedy
    one-at-a-time selection.
    """
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    # Parse additional filter tokens
    filter_tokens = [t.strip() for t in tokens.split(",") if t.strip()]
    allowed_item_types = _parse_item_types_param(item_types)
    allowed_item_prefixes = _parse_item_prefixes_param(item_prefixes)

    # Build the unit token
    unit_token = f"U:{unit}"

    if unit_token not in ENGINE.token_to_id:
        raise HTTPException(status_code=404, detail=f"Unit '{unit}' not found")

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

    def _shrink_avg(avg: float, n: int, prior_mean: float, prior_weight: float) -> float:
        """Empirical-Bayes shrinkage to reduce small-sample noise (lower is better)."""
        if n <= 0:
            return prior_mean
        return (avg * n + prior_mean * prior_weight) / (n + prior_weight)

    # Items already locked-in for this unit via filters (E:{unit}|{item})
    locked_items: list[str] = []
    locked_item_set: set[str] = set()
    for t in filter_tokens:
        parsed = parse_token(t)
        if parsed["type"] == "equipped" and parsed["unit"] == unit:
            item_name = parsed["item"]
            if item_name in locked_item_set:
                continue
            locked_item_set.add(item_name)
            locked_items.append(item_name)

    # Beam-search parameters (kept internal to avoid API churn)
    beam_width = 40
    max_builds = 25
    prior_weight = float(max(25, min(200, int(n_base * 0.05))))

    # Find all equipped tokens for this unit
    prefix = f"E:{unit}|"
    all_equipped = [t for t in ENGINE.id_to_token if t.startswith(prefix)]

    # If the user already filtered by equipped items on this unit, treat them as locked
    # and only recommend the remaining slots.
    locked_items = locked_items[:slots]
    locked_item_set = set(locked_items)
    remaining_slots = max(0, slots - len(locked_items))

    base_item_dicts = [
        {
            "item": item_name,
            "token": f"E:{unit}|{item_name}",
            "delta": 0.0,
            "avg_placement": round(avg_base, 3),
            "n": n_base,
            "item_type": get_item_type(item_name),
            "item_prefix": get_item_prefix(item_name),
        }
        for item_name in locked_items
    ]

    if remaining_slots == 0:
        all_builds = [
            {
                "items": [{**item, "slot": i + 1} for i, item in enumerate(base_item_dicts)],
                "final_avg": round(avg_base, 3),
                "final_n": n_base,
                "total_delta": 0.0,
                "num_items": len(base_item_dicts),
            }
        ]
    else:
        # Candidate items: equipped items for this unit with enough sample size under the current filters.
        candidates = []
        for eq_token in all_equipped:
            item_name = eq_token.split("|")[1]
            if item_name in locked_item_set:
                continue

            cand_type = get_item_type(item_name)
            if allowed_item_types is not None and cand_type not in allowed_item_types:
                continue
            cand_prefix = get_item_prefix(item_name)
            if cand_prefix and cand_prefix.lower() not in allowed_item_prefixes:
                continue

            token_id = ENGINE.token_to_id.get(eq_token)
            if token_id is None:
                continue
            token_stats = ENGINE.tokens.get(token_id)
            if token_stats is None:
                continue

            with_bitmap = base_bitmap & token_stats.bitmap
            n_with = len(with_bitmap)
            if n_with < min_sample:
                continue

            candidates.append({
                "item": item_name,
                "token": eq_token,
                "bitmap": token_stats.bitmap,
                "item_type": cand_type,
                "item_prefix": cand_prefix,
            })

        if not candidates:
            all_builds = []
            if base_item_dicts:
                all_builds.append({
                    "items": [{**item, "slot": i + 1} for i, item in enumerate(base_item_dicts)],
                    "final_avg": round(avg_base, 3),
                    "final_n": n_base,
                    "total_delta": 0.0,
                    "num_items": len(base_item_dicts),
                })
        else:
            # Beam search over item combinations to better capture item interactions than greedy selection.
            beam = [
                {
                    "items": list(base_item_dicts),
                    "bitmap": base_bitmap,
                    "n": n_base,
                    "avg": avg_base,
                    "score": _shrink_avg(avg_base, n_base, avg_base, prior_weight),
                    "used": set(locked_item_set),
                }
            ]

            for _ in range(remaining_slots):
                next_states = []
                for state in beam:
                    for cand in candidates:
                        if cand["item"] in state["used"]:
                            continue

                        with_bitmap = state["bitmap"] & cand["bitmap"]
                        n_with = len(with_bitmap)
                        if n_with < min_sample:
                            continue

                        avg_with = ENGINE.avg_placement_for_bitmap(with_bitmap)
                        score_with = _shrink_avg(avg_with, n_with, avg_base, prior_weight)

                        item_stats = {
                            "item": cand["item"],
                            "token": cand["token"],
                            "delta": round(avg_with - state["avg"], 3),
                            "avg_placement": round(avg_with, 3),
                            "n": n_with,
                            "item_type": cand["item_type"],
                            "item_prefix": cand["item_prefix"],
                        }

                        next_states.append({
                            "items": state["items"] + [item_stats],
                            "bitmap": with_bitmap,
                            "n": n_with,
                            "avg": avg_with,
                            "score": score_with,
                            "used": state["used"] | {cand["item"]},
                        })

                if not next_states:
                    break

                # Prefer lower (better) shrunk score; then lower raw avg; then higher sample size.
                next_states.sort(key=lambda s: (s["score"], s["avg"], -s["n"]))

                # De-dupe by item set and keep a reasonable beam.
                new_beam = []
                seen_keys = set()
                for s in next_states:
                    key = tuple(sorted(it["item"] for it in s["items"]))
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    new_beam.append(s)
                    if len(new_beam) >= beam_width:
                        break
                beam = new_beam

            # Convert beam states to builds.
            all_builds = []
            for state in beam:
                items_out = [{**item, "slot": i + 1} for i, item in enumerate(state["items"][:slots])]
                if not items_out:
                    continue

                final_avg = float(state["avg"])
                final_n = int(state["n"])
                all_builds.append({
                    "items": items_out,
                    "final_avg": round(final_avg, 3),
                    "final_n": final_n,
                    "total_delta": round(final_avg - avg_base, 3),
                    "num_items": len(items_out),
                    "_score": float(state["score"]),
                })

            # Sort and trim.
            all_builds.sort(key=lambda b: (-b["num_items"], b["final_avg"], -b["final_n"], b["_score"]))
            all_builds = all_builds[:max_builds]
            for b in all_builds:
                b.pop("_score", None)

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
    sort_mode: str = Query(default="helpful", description="Sort mode: helpful (best avg), harmful (worst avg), impact (abs delta)"),
    item_types: str = Query(
        default="",
        description="Item types to include (comma-separated): component, full, artifact, emblem, radiant",
    ),
    item_prefixes: str = Query(
        default="",
        description="Item prefixes to include (comma-separated, case-insensitive), e.g. Bilgewater",
    ),
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
    allowed_item_types = _parse_item_types_param(item_types)
    allowed_item_prefixes = _parse_item_prefixes_param(item_prefixes)

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

    def _shrink_avg(avg: float, n: int, prior_mean: float, prior_weight: float) -> float:
        """Empirical-Bayes shrinkage to reduce small-sample noise (lower is better)."""
        if n <= 0:
            return prior_mean
        return (avg * n + prior_mean * prior_weight) / (n + prior_weight)

    # Shrinkage strength scales with min_sample so low-n items don't look extreme.
    prior_weight = float(max(25, min(200, int(min_sample * 2))))

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

        item_type = get_item_type(item_name)
        if allowed_item_types is not None and item_type not in allowed_item_types:
            continue
        item_prefix = get_item_prefix(item_name)
        if item_prefix and item_prefix.lower() not in allowed_item_prefixes:
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
        delta_raw = avg_with - avg_base
        avg_adj = _shrink_avg(avg_with, n_with, avg_base, prior_weight)
        delta_adj = avg_adj - avg_base

        results.append({
            "item": item_name,
            "token": eq_token,
            "delta": round(delta_adj, 3),
            "avg_placement": round(avg_adj, 3),
            "n": n_with,
            "pct_of_base": round(n_with / n_base * 100, 1) if n_base > 0 else 0,
            "raw_delta": round(delta_raw, 3),
            "raw_avg_placement": round(avg_with, 3),
            "item_type": item_type,
            "item_prefix": item_prefix,
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
