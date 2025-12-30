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

import numpy as np
from dotenv import load_dotenv
load_dotenv()  # Load .env file

from fastapi import FastAPI, Query, UploadFile, File, Header, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
import uvicorn
import httpx

from .engine import GraphEngine, build_engine
from .clustering import (
    ClusterParams,
    TokenReportParams,
    compute_clusters,
    compute_cluster_playbook,
    compute_token_playbook,
    compute_token_stats,
)
from .causal import AIPWConfig, OverlapError, aipw_ate, e_value_from_risk_ratio, placements_to_outcome
from .features import TokenFeatureParams, board_strength_features, build_sparse_feature_matrix, select_feature_tokens
from .items import get_item_prefix, get_item_type

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
        try:
            ENGINE = GraphEngine.load(str(engine_path))
            stats = ENGINE.stats()
            print(f"Engine ready: {stats['total_tokens']} tokens, {stats['total_matches']} matches")

            # Upgrade old engines (built before equipped-count tokens) so duplicate-item
            # filters/builds work without requiring manual rebuild steps.
            if db_path.exists():
                has_equipped_counts = any(
                    t.startswith("E:") and int(parse_token(t).get("copies", 1) or 1) >= 2
                    for t in ENGINE.id_to_token
                )
                if not has_equipped_counts:
                    print("Engine is missing duplicate-item tokens; rebuilding a count-aware engine (preserving cached necessity estimates)...")
                    old_engine = ENGINE
                    ENGINE = GraphEngine()
                    ENGINE.build_from_db(str(db_path))

                    # Preserve labels where possible (helps search UX) by copying from the old engine
                    # for shared tokens.
                    for tok in old_engine.id_to_token:
                        old_id = old_engine.token_to_id.get(tok)
                        new_id = ENGINE.token_to_id.get(tok)
                        if old_id is None or new_id is None:
                            continue
                        label = old_engine.labels.get(old_id)
                        if label:
                            ENGINE.labels[new_id] = label

                    # Preserve cached top4 necessity estimates from the previous engine for any
                    # shared tokens. New duplicate-count tokens will remain uncached (NaN).
                    if getattr(old_engine, "necessity_top4_tau", None) is not None:
                        try:
                            ENGINE.init_necessity_cache_top4()
                            for tok in old_engine.id_to_token:
                                old_id = old_engine.token_to_id.get(tok)
                                new_id = ENGINE.token_to_id.get(tok)
                                if old_id is None or new_id is None:
                                    continue

                                ENGINE.necessity_top4_tau[new_id] = old_engine.necessity_top4_tau[old_id]
                                ENGINE.necessity_top4_ci95_low[new_id] = old_engine.necessity_top4_ci95_low[old_id]
                                ENGINE.necessity_top4_ci95_high[new_id] = old_engine.necessity_top4_ci95_high[old_id]
                                ENGINE.necessity_top4_se[new_id] = old_engine.necessity_top4_se[old_id]
                                ENGINE.necessity_top4_raw_tau[new_id] = old_engine.necessity_top4_raw_tau[old_id]
                                ENGINE.necessity_top4_frac_trimmed[new_id] = old_engine.necessity_top4_frac_trimmed[old_id]
                                ENGINE.necessity_top4_e_p01[new_id] = old_engine.necessity_top4_e_p01[old_id]
                                ENGINE.necessity_top4_e_p99[new_id] = old_engine.necessity_top4_e_p99[old_id]
                                ENGINE.necessity_top4_n_treated[new_id] = old_engine.necessity_top4_n_treated[old_id]
                                ENGINE.necessity_top4_n_control[new_id] = old_engine.necessity_top4_n_control[old_id]
                                ENGINE.necessity_top4_n_used[new_id] = old_engine.necessity_top4_n_used[old_id]
                                ENGINE.necessity_top4_scope_min_star[new_id] = old_engine.necessity_top4_scope_min_star[old_id]

                            ENGINE.necessity_top4_ready = bool(np.isfinite(ENGINE.necessity_top4_tau).any())
                        except Exception as e:
                            print(f"Warning: failed to preserve necessity cache ({e}); continuing without it.")

                    ENGINE.save(str(engine_path))
                    stats = ENGINE.stats()
                    print(f"Engine ready: {stats['total_tokens']} tokens, {stats['total_matches']} matches")
        except Exception as e:
            print(f"Failed to load engine.bin ({e}).")
            if db_path.exists():
                print("Rebuilding engine from database...")
                ENGINE = build_engine(str(db_path), str(engine_path))
                stats = ENGINE.stats()
                print(f"Engine ready: {stats['total_tokens']} tokens, {stats['total_matches']} matches")
            else:
                ENGINE = None
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
    token = token.lstrip("-!")
    if token.startswith("U:"):
        return "unit"
    elif token.startswith("I:"):
        return "item"
    elif token.startswith("E:"):
        return "equipped"
    elif token.startswith("T:"):
        return "trait"
    return "unknown"


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
    raw = token.lstrip("-!")
    negated = raw != token
    token = raw
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
                return {"type": "unit", "unit": unit, "stars": stars, "negated": negated}
        return {"type": "unit", "unit": rest, "stars": None, "negated": negated}
    elif token_type == "item":
        return {"type": "item", "item": token[2:], "negated": negated}
    elif token_type == "equipped":
        rest = token[2:]
        if "|" not in rest:
            return {"type": "equipped", "unit": rest, "item": "", "copies": 1, "negated": negated}
        unit, item_part = rest.split("|", 1)
        copies = 1
        item = item_part
        if ":" in item_part:
            base, maybe_copies = item_part.rsplit(":", 1)
            try:
                c = int(maybe_copies)
            except ValueError:
                c = None
            if c is not None and c >= 2:
                copies = c
                item = base
        return {"type": "equipped", "unit": unit, "item": item, "copies": copies, "negated": negated}
    elif token_type == "trait":
        # Handle tiered traits like T:Brawler:2
        parts = token[2:].split(":")
        if len(parts) == 2:
            return {"type": "trait", "trait": parts[0], "tier": int(parts[1]), "negated": negated}
        return {"type": "trait", "trait": parts[0], "tier": None, "negated": negated}
    return {"type": "unknown", "negated": negated}


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
        if parsed.get("negated"):
            continue
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
                    parsed = parse_token(eq_token)
                    item_name = parsed.get("item")
                    if item_name and item_name not in center_items:
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
    include_tokens: list[str] = []
    exclude_tokens: list[str] = []
    for t in token_list:
        if t.startswith("-") or t.startswith("!"):
            raw = t.lstrip("-!")
            if raw:
                exclude_tokens.append(raw)
        else:
            include_tokens.append(t)
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

    # Compute base set using include/exclude filters.
    base = ENGINE.filter_bitmap(include_tokens, exclude_tokens)
    n_base = len(base)
    avg_base = ENGINE.avg_placement_for_bitmap(base) if n_base > 0 else 4.5

    # Get center info
    center_info = get_center_info(include_tokens)

    # Generate candidates
    candidates = generate_candidates(center_info, include_tokens + exclude_tokens)

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
                label = ENGINE.get_label(node_id)
                if parsed.get("negated"):
                    label = f"Not {label}"
                nodes.append({
                    "id": node_id,
                    "label": label,
                    "type": "unit",
                    "negated": bool(parsed.get("negated")),
                    "isCenter": True
                })
                node_ids.add(node_id)
        elif parsed["type"] == "item":
            node_id = f"I:{parsed['item']}"
            if node_id not in node_ids:
                label = ENGINE.get_label(node_id)
                if parsed.get("negated"):
                    label = f"Not {label}"
                nodes.append({
                    "id": node_id,
                    "label": label,
                    "type": "item",
                    "negated": bool(parsed.get("negated")),
                    "isCenter": True
                })
                node_ids.add(node_id)
        elif parsed["type"] == "trait":
            if parsed.get("tier"):
                node_id = f"T:{parsed['trait']}:{parsed['tier']}"
            else:
                node_id = f"T:{parsed['trait']}"
            if node_id not in node_ids:
                label = ENGINE.get_label(node_id)
                if parsed.get("negated"):
                    label = f"Not {label}"
                nodes.append({
                    "id": node_id,
                    "label": label,
                    "type": "trait",
                    "negated": bool(parsed.get("negated")),
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


@app.get("/cluster-playbook")
def get_cluster_playbook(
    tokens: str = Query(default="", description="Comma-separated tokens (filters)"),
    cluster_id: int = Query(..., description="Cluster id from /clusters"),
    n_clusters: int = Query(default=15, ge=2, le=50),
    use_units: bool = Query(default=True),
    use_traits: bool = Query(default=True),
    use_items: bool = Query(default=False),
    min_token_freq: int = Query(default=100, ge=1),
    min_cluster_size: int = Query(default=50, ge=1),
    top_k_tokens: int = Query(default=10, ge=1, le=30),
    random_state: int = Query(default=42),
    min_with: int = Query(default=30, ge=1),
    min_without: int = Query(default=30, ge=1),
    max_drivers: int = Query(default=12, ge=1, le=50),
    max_killers: int = Query(default=12, ge=1, le=50),
):
    """
    Compute a "playbook" for a selected comp archetype cluster.

    Focuses on tokens that most strongly correlate with winning (1st place)
    vs losing within the cluster (stars, trait breakpoints, BIS, etc).
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
    return compute_cluster_playbook(
        ENGINE,
        token_list,
        params,
        cluster_id,
        min_with=min_with,
        min_without=min_without,
        max_drivers=max_drivers,
        max_killers=max_killers,
    )


@app.get("/token-stats")
def get_token_stats(
    tokens: str = Query(default="", description="Comma-separated tokens (filters)"),
):
    """
    Return fast placement stats for a token filter without running clustering.

    Intended for meta comps list rendering (avg/top4/win + placement histogram).
    """
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    token_list = [t.strip() for t in tokens.split(",") if t.strip()]
    return compute_token_stats(ENGINE, token_list)


@app.get("/token-playbook")
def get_token_playbook(
    tokens: str = Query(default="", description="Comma-separated tokens (filters)"),
    use_units: bool = Query(default=True),
    use_traits: bool = Query(default=True),
    use_items: bool = Query(default=True),
    min_token_freq: int = Query(default=100, ge=1),
    top_k_tokens: int = Query(default=10, ge=1, le=30),
    min_with: int = Query(default=30, ge=1),
    min_without: int = Query(default=30, ge=1),
    max_drivers: int = Query(default=12, ge=1, le=50),
    max_killers: int = Query(default=12, ge=1, le=50),
):
    """
    Compute a "playbook" for a token filter (treat the filtered set as a cluster).

    Used to make TFTAcademy meta comps feel first-class in the explorer without
    requiring a separate clustering run per comp.
    """
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    token_list = [t.strip() for t in tokens.split(",") if t.strip()]
    params = TokenReportParams(
        use_units=use_units,
        use_traits=use_traits,
        use_items=use_items,
        min_token_freq=min_token_freq,
        top_k_tokens=top_k_tokens,
    )
    return compute_token_playbook(
        ENGINE,
        token_list,
        params,
        min_with=min_with,
        min_without=min_without,
        max_drivers=max_drivers,
        max_killers=max_killers,
    )


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
- If the user says "best"/"top", set item_explorer_sort_mode="helpful"; if "worst"/"bad", set "harmful"; if "impact"/"most impact", set "impact"; if "necessary"/"necessity", set "necessity".
- If the user says "build(s)", set item_explorer_tab="builds"; if they say "item(s)" or mention an item category, set item_explorer_tab="items".
- If the user says a unit is "with"/"holding"/"equipped with" an item, put that in equipped=[{unit, item}].

EXCLUSION / NEGATION:
- If the user says "without", "no", "exclude", "not", "minus", or uses similar negation language, put those entities in the exclude_* fields.
- Example: "Tryndamere 2 without Ashe 3" => units=["Tryndamere 2"], exclude_units=["Ashe 3"]
- If the user says a unit WITHOUT a specific item (e.g. "Tryndamere without Guinsoo's"), prefer exclude_equipped=[{unit,item}] over exclude_items.

RULES:
1. Extract EVERY entity - do not miss any
2. Numbers before/after traits refer to the in-game breakpoint number (e.g., "5 demacia" = Demacia 5)
3. Numbers near unit names refer to star level (e.g., "Ambessa 2" or "2 star Ambessa" = Ambessa 2★)
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
                    "enum": ["helpful", "harmful", "impact", "necessity"],
                    "description": "Sort mode for the Items list (best/worst/impact/necessity)"
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
                },
                "exclude_units": {
                    "type": "array",
                    "items": {"type": "string", "enum": vocab['units']},
                    "description": "Excluded champion/unit names (use when user says WITHOUT/NO/EXCLUDE a unit)"
                },
                "exclude_items": {
                    "type": "array",
                    "items": {"type": "string", "enum": vocab['items']},
                    "description": "Excluded item names (use when user says WITHOUT/NO/EXCLUDE an item)"
                },
                "exclude_traits": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "name": {"type": "string", "enum": vocab['traits']},
                            "tier": {"type": "integer", "description": "Trait tier level (2-9)"}
                        },
                        "required": ["name"]
                    },
                    "description": "Excluded trait filters with optional tier"
                },
                "exclude_equipped": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "unit": {"type": "string", "enum": vocab['units']},
                            "item": {"type": "string", "enum": vocab['items']}
                        },
                        "required": ["unit", "item"]
                    },
                    "description": "Excluded equipped items on a specific unit (use when user says unit WITHOUT an item)"
                },
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
    min_sample: int = Query(default=30, description="Minimum sample size for inclusion"),
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

    # Parse additional filter tokens (supports exclude tokens prefixed by '-' or '!')
    filter_tokens = [t.strip() for t in tokens.split(",") if t.strip()]
    include_filters: list[str] = []
    exclude_filters: list[str] = []
    for t in filter_tokens:
        if t.startswith("-") or t.startswith("!"):
            raw = t.lstrip("-!")
            if raw:
                exclude_filters.append(raw)
        else:
            include_filters.append(t)
    allowed_item_types = _parse_item_types_param(item_types)
    allowed_item_prefixes = _parse_item_prefixes_param(item_prefixes)

    # Build the unit token
    unit_token = f"U:{unit}"

    if unit_token not in ENGINE.token_to_id:
        raise HTTPException(status_code=404, detail=f"Unit '{unit}' not found")

    # Get base stats (unit + included filters, minus excluded filters)
    base_tokens = [unit_token] + include_filters
    base_bitmap = ENGINE.filter_bitmap(base_tokens, exclude_filters)
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

    # Items already locked-in for this unit via filters (E:{unit}|{item}[:2|:3]).
    # We treat count tokens as consuming multiple slots.
    locked_counts: dict[str, int] = {}
    locked_order: list[str] = []
    for t in include_filters:
        parsed = parse_token(t)
        if parsed["type"] != "equipped" or parsed.get("unit") != unit:
            continue
        item_name = parsed.get("item") or ""
        if not item_name:
            continue
        copies = int(parsed.get("copies") or 1)
        copies = max(1, min(3, copies))
        if item_name not in locked_counts:
            locked_order.append(item_name)
        locked_counts[item_name] = max(locked_counts.get(item_name, 0), copies)

    # Beam-search parameters (kept internal to avoid API churn)
    beam_width = 40
    max_builds = 25
    prior_weight = float(max(25, min(2000, int(n_base * 0.05))))

    # Find all equipped tokens for this unit
    prefix = f"E:{unit}|"
    all_equipped = [t for t in ENGINE.id_to_token if t.startswith(prefix)]

    # If the user already filtered by equipped items on this unit, treat them as locked
    # and only recommend the remaining slots.
    base_item_dicts: list[dict] = []
    effective_locked_counts: dict[str, int] = {}
    for item_name in locked_order:
        if len(base_item_dicts) >= slots:
            break
        want = locked_counts.get(item_name, 0)
        if want <= 0:
            continue
        take = min(want, slots - len(base_item_dicts))
        for k in range(1, take + 1):
            tok = f"E:{unit}|{item_name}" if k == 1 else f"E:{unit}|{item_name}:{k}"
            base_item_dicts.append(
                {
                    "item": item_name,
                    "token": tok,
                    "delta": 0.0,
                    "avg_placement": round(avg_base, 3),
                    "n": n_base,
                    "item_type": get_item_type(item_name),
                    "item_prefix": get_item_prefix(item_name),
                }
            )
        effective_locked_counts[item_name] = take

    remaining_slots = max(0, slots - len(base_item_dicts))

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
        # Candidate items: map base item -> {copies -> token+bitmap}.
        candidates_by_item: dict[str, dict] = {}
        for eq_token in all_equipped:
            parsed_eq = parse_token(eq_token)
            if parsed_eq.get("type") != "equipped":
                continue
            item_name = parsed_eq.get("item") or ""
            if not item_name:
                continue
            copies = int(parsed_eq.get("copies") or 1)
            if copies < 1 or copies > 3:
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

            entry = candidates_by_item.get(item_name)
            if entry is None:
                entry = {
                    "item": item_name,
                    "item_type": cand_type,
                    "item_prefix": cand_prefix,
                    "tokens": {},
                    "bitmaps": {},
                }
                candidates_by_item[item_name] = entry

            entry["tokens"][copies] = eq_token
            entry["bitmaps"][copies] = token_stats.bitmap

        # Require a valid >=1 token and enough sample size in the current context.
        pruned: dict[str, dict] = {}
        for item_name, entry in candidates_by_item.items():
            tok1 = entry["tokens"].get(1)
            bm1 = entry["bitmaps"].get(1)
            if not tok1 or bm1 is None:
                continue
            n_with = len(base_bitmap & bm1)
            if n_with < min_sample:
                continue
            pruned[item_name] = entry
        candidates_by_item = pruned

        if not candidates_by_item:
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
                    "counts": dict(effective_locked_counts),
                }
            ]

            for _ in range(remaining_slots):
                next_states = []
                for state in beam:
                    for item_name, cand in candidates_by_item.items():
                        prev = int(state["counts"].get(item_name, 0) or 0)
                        want = prev + 1
                        if want > 3:
                            continue
                        tok = cand["tokens"].get(want)
                        bm = cand["bitmaps"].get(want)
                        if not tok or bm is None:
                            continue

                        with_bitmap = state["bitmap"] & bm
                        n_with = len(with_bitmap)
                        if n_with < min_sample:
                            continue

                        avg_with = ENGINE.avg_placement_for_bitmap(with_bitmap)
                        score_with = _shrink_avg(avg_with, n_with, avg_base, prior_weight)

                        item_stats = {
                            "item": item_name,
                            "token": tok,
                            "delta": round(avg_with - state["avg"], 3),
                            "avg_placement": round(avg_with, 3),
                            "n": n_with,
                            "item_type": cand["item_type"],
                            "item_prefix": cand["item_prefix"],
                        }

                        next_counts = dict(state["counts"])
                        next_counts[item_name] = want

                        next_states.append({
                            "items": state["items"] + [item_stats],
                            "bitmap": with_bitmap,
                            "n": n_with,
                            "avg": avg_with,
                            "score": score_with,
                            "counts": next_counts,
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
            all_builds.sort(key=lambda b: (-b["num_items"], b["_score"], b["final_avg"], -b["final_n"]))
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
    sort_mode: str = Query(default="necessity", description="Sort mode: necessity (causal Δ outcome), helpful (best avg), harmful (worst avg), impact (abs delta)"),
    necessity_outcome: str = Query(default="top4", description="Necessity outcome: top4, win, placement, rank_score"),
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

    # Parse additional filter tokens (supports exclude tokens prefixed by '-' or '!')
    filter_tokens = [t.strip() for t in tokens.split(",") if t.strip()]
    include_filters: list[str] = []
    exclude_filters: list[str] = []
    for t in filter_tokens:
        if t.startswith("-") or t.startswith("!"):
            raw = t.lstrip("-!")
            if raw:
                exclude_filters.append(raw)
        else:
            include_filters.append(t)
    allowed_item_types = _parse_item_types_param(item_types)
    allowed_item_prefixes = _parse_item_prefixes_param(item_prefixes)

    # Build the unit token
    unit_token = f"U:{unit}"

    # Check unit exists
    if unit_token not in ENGINE.token_to_id:
        raise HTTPException(status_code=404, detail=f"Unit '{unit}' not found")

    # Build base set: unit + included filters, minus excluded filters
    base_tokens = [unit_token] + include_filters
    base_bitmap = ENGINE.filter_bitmap(base_tokens, exclude_filters)
    n_base = len(base_bitmap)

    if n_base == 0:
        return {
            "unit": unit,
            "filters": filter_tokens,
            "base": {"n": 0, "avg_placement": 4.5},
            "items": []
        }

    scope = None
    # In "necessity" mode, avoid mixing "1★ desperation" boards into carry-item estimates.
    # If the unit's sample is dominated by 2★+ boards, auto-scope to 2★+ unless the
    # user explicitly constrained star level via filters.
    if sort_mode == "necessity":
        scope = {"unit_stars_min": 1, "auto": False}
        has_star_filter = any(t.startswith(f"U:{unit}:") for t in include_filters) or any(
            t.startswith(f"U:{unit}:") for t in exclude_filters
        )
        if not has_star_filter:
            from pyroaring import BitMap

            star2plus_bm = BitMap()
            for s in range(2, 7):
                star_tok = f"U:{unit}:{s}"
                token_id = ENGINE.token_to_id.get(star_tok)
                if token_id is None:
                    continue
                stats = ENGINE.tokens.get(token_id)
                if stats is None:
                    continue
                star2plus_bm |= stats.bitmap

            if star2plus_bm:
                # Match the engine's precomputed cache in the default view (no extra filters),
                # but allow the decision to adapt to the filtered context when users add tokens.
                if include_filters or exclude_filters:
                    n_all = len(base_bitmap)
                    n_2p = len(base_bitmap & star2plus_bm)
                else:
                    unit_stats = ENGINE.tokens.get(ENGINE.token_to_id.get(unit_token))
                    unit_all_bm = unit_stats.bitmap if unit_stats is not None else base_bitmap
                    n_all = len(unit_all_bm)
                    n_2p = len(unit_all_bm & star2plus_bm)
                if n_all > 0 and n_2p >= 2000 and (n_2p / float(n_all)) >= 0.7:
                    base_bitmap &= star2plus_bm
                    n_base = len(base_bitmap)
                    scope = {"unit_stars_min": 2, "auto": True}
                    if n_base == 0:
                        return {
                            "unit": unit,
                            "filters": filter_tokens,
                            "base": {"n": 0, "avg_placement": 4.5},
                            "items": [],
                            "scope": scope,
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
    equipped_tokens = [t for t in ENGINE.id_to_token if t.startswith(prefix) and parse_token(t).get("copies", 1) == 1]

    # Track which items are already equipped on this unit in filters (to exclude from recommendations).
    # NOTE: We intentionally do *not* treat global item tokens (I:Item) as "already present",
    # since they refer to the item existing anywhere on the board, not necessarily on this unit.
    existing_items = set()
    for t in include_filters:
        parsed = parse_token(t)
        if parsed["type"] == "equipped" and parsed["unit"] == unit:
            existing_items.add(parsed["item"])

    # Score each equipped token
    results = []
    for eq_token in equipped_tokens:
        parsed_eq = parse_token(eq_token)
        item_name = parsed_eq.get("item")
        if not item_name:
            continue

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
    elif sort_mode == "necessity":
        # Causal "necessity" sort: estimated causal Δ for each item-on-unit treatment.
        #
        # - Default view (no extra filters): use the engine.bin cache (fast + stable).
        # - Filtered context: compute a fast, context-specific estimate for ranking; the
        #   UI can request the full AIPW estimate for a specific item via /item-necessity.
        necessity_outcome_norm = (necessity_outcome or "").strip().lower()
        if necessity_outcome_norm in ("top_4", "topfour"):
            necessity_outcome_norm = "top4"
        use_cache = bool(
            getattr(ENGINE, "necessity_top4_ready", False)
            and not include_filters
            and not exclude_filters
            and scope is not None
            and necessity_outcome_norm == "top4"
        )

        if use_cache:
            scope_min_star = int(scope.get("unit_stars_min") or 1)
            for row in results:
                eq_token = row.get("token")
                token_id = ENGINE.token_to_id.get(eq_token) if isinstance(eq_token, str) else None
                if token_id is None:
                    row["necessity"] = None
                    continue

                stored_scope = int(ENGINE.necessity_top4_scope_min_star[token_id])
                if stored_scope and stored_scope != scope_min_star:
                    row["necessity"] = None
                    continue

                tau = float(ENGINE.necessity_top4_tau[token_id])
                if not np.isfinite(tau):
                    row["necessity"] = None
                    continue

                ci_low = float(ENGINE.necessity_top4_ci95_low[token_id])
                ci_high = float(ENGINE.necessity_top4_ci95_high[token_id])
                se = float(ENGINE.necessity_top4_se[token_id])
                raw_tau = float(ENGINE.necessity_top4_raw_tau[token_id])
                frac_trimmed = float(ENGINE.necessity_top4_frac_trimmed[token_id])
                e_p01 = float(ENGINE.necessity_top4_e_p01[token_id])
                e_p99 = float(ENGINE.necessity_top4_e_p99[token_id])

                warnings: list[str] = []
                if np.isfinite(frac_trimmed) and frac_trimmed > 0.5:
                    warnings.append("Low overlap: large fraction of samples trimmed by propensity bounds.")
                if np.isfinite(e_p01) and np.isfinite(e_p99) and (e_p01 < 0.02 or e_p99 > 0.98):
                    warnings.append("Positivity warning: propensity is near 0/1 in parts of X (effect may be unstable).")

                row["necessity"] = {
                    "method": "aipw",
                    "outcome": "top4",
                    "tau": round(tau, 6),
                    "ci95_low": round(ci_low, 6) if np.isfinite(ci_low) else None,
                    "ci95_high": round(ci_high, 6) if np.isfinite(ci_high) else None,
                    "se": round(se, 6) if np.isfinite(se) else None,
                    "p_value": None,
                    "raw_tau": round(raw_tau, 6) if np.isfinite(raw_tau) else None,
                    "n_treated": int(ENGINE.necessity_top4_n_treated[token_id]),
                    "n_control": int(ENGINE.necessity_top4_n_control[token_id]),
                    "n_used": int(ENGINE.necessity_top4_n_used[token_id]),
                    "frac_trimmed": round(frac_trimmed, 6) if np.isfinite(frac_trimmed) else None,
                    "e_p01": round(e_p01, 6) if np.isfinite(e_p01) else None,
                    "e_p50": None,
                    "e_p99": round(e_p99, 6) if np.isfinite(e_p99) else None,
                    "risk_ratio": None,
                    "e_value": None,
                    "warnings": warnings,
                    "cached": True,
                    "scope_min_star": stored_scope or scope_min_star,
                }
        else:
            # Fast contextual estimate:
            # stratify boards into coarse archetype clusters (units+traits only),
            # then compute a trimmed, cluster-adjusted Top4 difference for each item.
            from pyroaring import BitMap

            max_rows = 80_000
            min_token_freq = max(50, int(min_sample))
            overlap_min = 0.05
            overlap_max = 0.95
            min_group = max(100, int(min_sample))
            min_cluster_group = max(25, int(min_sample))

            base_ids_full = np.array(base_bitmap.to_array(), dtype=np.int64)
            rng = np.random.default_rng(42)
            if base_ids_full.size > max_rows:
                sel = rng.choice(base_ids_full.size, size=max_rows, replace=False)
                base_ids = np.sort(base_ids_full[sel])
                base_bitmap_model = BitMap(base_ids)
            else:
                base_ids = base_ids_full
                base_bitmap_model = base_bitmap

            placements = ENGINE.placements[base_ids].astype(np.int16, copy=False)
            y, kind = placements_to_outcome(placements, necessity_outcome_norm)

            feature_tokens = select_feature_tokens(
                ENGINE,
                TokenFeatureParams(
                    use_units=True,
                    use_traits=True,
                    use_items=False,
                    use_equipped=False,
                    include_star_units=False,
                    include_tier_traits=True,
                    min_token_freq=min_token_freq,
                ),
                exclude={unit_token},
            )

            X_tok, kept, _, _ = build_sparse_feature_matrix(ENGINE, base_bitmap_model, base_ids, feature_tokens)

            # If we can't build a meaningful context representation, don't try to estimate.
            if base_ids.size < 2000 or X_tok.shape[1] < 2:
                for row in results:
                    row["necessity"] = None
            else:
                from sklearn.cluster import MiniBatchKMeans

                n_clusters = 8
                # Keep clusters reasonably populated to reduce noise.
                n_clusters = min(n_clusters, max(2, int(base_ids.size // 500)))

                kmeans = MiniBatchKMeans(
                    n_clusters=n_clusters,
                    random_state=42,
                    batch_size=2048,
                    n_init=3,
                    reassignment_ratio=0.01,
                )
                labels = kmeans.fit_predict(X_tok).astype(np.int32, copy=False)

                cluster_sizes = np.bincount(labels, minlength=n_clusters).astype(np.int32, copy=False)
                y_f = y.astype(np.float64, copy=False)
                y2_f = (y_f * y_f).astype(np.float64, copy=False)
                cluster_y_sum = np.bincount(labels, weights=y_f, minlength=n_clusters).astype(
                    np.float64, copy=False
                )
                cluster_y2_sum = np.bincount(labels, weights=y2_f, minlength=n_clusters).astype(
                    np.float64, copy=False
                )
                n_total = int(base_ids.size)

                # Precompute weighted quantiles of per-cluster treatment rates.
                def _weighted_quantile(v: np.ndarray, w: np.ndarray, q: float) -> float:
                    if v.size == 0:
                        return float("nan")
                    q = float(q)
                    q = 0.0 if q < 0 else 1.0 if q > 1 else q
                    order = np.argsort(v)
                    v_s = v[order]
                    w_s = w[order].astype(np.float64, copy=False)
                    cw = np.cumsum(w_s)
                    total = float(cw[-1]) if cw.size else 0.0
                    if total <= 0:
                        return float("nan")
                    target = q * total
                    idx = int(np.searchsorted(cw, target, side="left"))
                    idx = max(0, min(idx, v_s.size - 1))
                    return float(v_s[idx])

                def _var_from_sums(n: np.ndarray, s: np.ndarray, s2: np.ndarray) -> np.ndarray:
                    n_f = n.astype(np.float64, copy=False)
                    mu = s / np.maximum(n_f, 1.0)
                    denom = np.maximum(n_f - 1.0, 1.0)
                    var = (s2 - n_f * (mu * mu)) / denom
                    return np.clip(var, 0.0, None)

                # Iterate items and compute a trimmed, cluster-adjusted estimate.
                for row in results:
                    eq_token = row.get("token")
                    token_id = ENGINE.token_to_id.get(eq_token) if isinstance(eq_token, str) else None
                    token_stats = ENGINE.tokens.get(token_id) if token_id is not None else None
                    if token_stats is None:
                        row["necessity"] = None
                        continue

                    treated_bm = base_bitmap_model & token_stats.bitmap
                    treated_ids = np.array(treated_bm.to_array(), dtype=np.int64)
                    T_rows = np.searchsorted(base_ids, treated_ids).astype(np.int32, copy=False) if treated_ids.size else np.zeros((0,), dtype=np.int32)

                    n_treated = int(T_rows.size)
                    n_control = int(n_total - n_treated)
                    if n_treated < min_group or n_control < min_group:
                        row["necessity"] = None
                        continue

                    # Raw (unadjusted) outcome delta.
                    y_t = y[T_rows] if T_rows.size else np.zeros((0,), dtype=y.dtype)
                    y1_mean = float(y_t.mean()) if y_t.size else float("nan")
                    y0_mean = float(((y.sum() - y_t.sum()) / max(1, n_control)))
                    raw_tau = float(y1_mean - y0_mean) if np.isfinite(y1_mean) and np.isfinite(y0_mean) else float("nan")

                    treated_labels = labels[T_rows] if T_rows.size else np.zeros((0,), dtype=np.int32)
                    treated_counts = np.bincount(treated_labels, minlength=n_clusters).astype(np.int32, copy=False)
                    treated_y = y_f[T_rows] if T_rows.size else np.zeros((0,), dtype=np.float64)
                    treated_y_sum = np.bincount(treated_labels, weights=treated_y, minlength=n_clusters).astype(
                        np.float64, copy=False
                    )
                    treated_y2_sum = np.bincount(treated_labels, weights=(treated_y * treated_y), minlength=n_clusters).astype(
                        np.float64, copy=False
                    )

                    control_counts = (cluster_sizes - treated_counts).astype(np.int32, copy=False)
                    control_y_sum = (cluster_y_sum - treated_y_sum).astype(np.float64, copy=False)
                    control_y2_sum = (cluster_y2_sum - treated_y2_sum).astype(np.float64, copy=False)

                    with np.errstate(divide="ignore", invalid="ignore"):
                        e = treated_counts.astype(np.float64) / np.maximum(cluster_sizes.astype(np.float64), 1.0)

                    used_clusters = (
                        (cluster_sizes >= (min_cluster_group * 2))
                        & (treated_counts >= min_cluster_group)
                        & (control_counts >= min_cluster_group)
                        & (e >= overlap_min)
                        & (e <= overlap_max)
                    )

                    n_used = int(cluster_sizes[used_clusters].sum())
                    n_treated_used = int(treated_counts[used_clusters].sum())
                    n_control_used = int(control_counts[used_clusters].sum())
                    if n_used < max(200, int(0.05 * n_total)) or n_treated_used < 50 or n_control_used < 50:
                        row["necessity"] = None
                        continue

                    mu1 = treated_y_sum[used_clusters] / treated_counts[used_clusters]
                    mu0 = control_y_sum[used_clusters] / control_counts[used_clusters]
                    diffs = mu1 - mu0
                    weights = cluster_sizes[used_clusters].astype(np.float64, copy=False)
                    tau = float(np.average(diffs, weights=weights)) if weights.size else float("nan")

                    # Approx SE for weighted stratified difference in means.
                    var1 = _var_from_sums(
                        treated_counts[used_clusters],
                        treated_y_sum[used_clusters],
                        treated_y2_sum[used_clusters],
                    )
                    var0 = _var_from_sums(
                        control_counts[used_clusters],
                        control_y_sum[used_clusters],
                        control_y2_sum[used_clusters],
                    )
                    var_diff = (var1 / treated_counts[used_clusters]) + (var0 / control_counts[used_clusters])
                    w = weights / float(n_used) if n_used > 0 else np.zeros_like(weights)
                    var_tau = float(np.sum((w**2) * var_diff)) if weights.size else float("nan")
                    se = float(np.sqrt(var_tau)) if var_tau >= 0 and np.isfinite(var_tau) else float("nan")

                    frac_trimmed = float(1.0 - (n_used / float(n_total))) if n_total else 1.0

                    e_used = e[cluster_sizes > 0]
                    w_used = cluster_sizes[cluster_sizes > 0]
                    e_p01 = _weighted_quantile(e_used, w_used, 0.01)
                    e_p99 = _weighted_quantile(e_used, w_used, 0.99)

                    warnings: list[str] = []
                    if np.isfinite(frac_trimmed) and frac_trimmed > 0.5:
                        warnings.append("Low overlap: large fraction of samples trimmed by propensity bounds.")
                    if np.isfinite(e_p01) and np.isfinite(e_p99) and (e_p01 < 0.02 or e_p99 > 0.98):
                        warnings.append("Positivity warning: propensity is near 0/1 in parts of X (effect may be unstable).")

                    row["necessity"] = {
                        "method": "cluster_adjusted",
                        "outcome": necessity_outcome_norm or "top4",
                        "tau": round(tau, 6) if np.isfinite(tau) else None,
                        "ci95_low": round(tau - 1.96 * se, 6) if np.isfinite(tau) and np.isfinite(se) else None,
                        "ci95_high": round(tau + 1.96 * se, 6) if np.isfinite(tau) and np.isfinite(se) else None,
                        "se": round(se, 6) if np.isfinite(se) else None,
                        "p_value": None,
                        "raw_tau": round(raw_tau, 6) if np.isfinite(raw_tau) else None,
                        "n_treated": int(n_treated),
                        "n_control": int(n_control),
                        "n_used": int(n_used),
                        "frac_trimmed": round(frac_trimmed, 6) if np.isfinite(frac_trimmed) else None,
                        "e_p01": round(e_p01, 6) if np.isfinite(e_p01) else None,
                        "e_p50": None,
                        "e_p99": round(e_p99, 6) if np.isfinite(e_p99) else None,
                        "risk_ratio": None,
                        "e_value": None,
                        "warnings": warnings,
                        "cached": False,
                        "scope_min_star": int(scope.get("unit_stars_min") or 1),
                    }

        def _sort_key(x: dict) -> tuple:
            nec = x.get("necessity") or {}
            tau = nec.get("tau")
            if tau is None:
                return (1, 0.0, 1.0, 0)
            frac_trimmed = float(nec.get("frac_trimmed") or 1.0)
            overlap_weight = max(0.0, 1.0 - frac_trimmed)
            # Prefer large effects that also have decent overlap; heavily penalize
            # estimates that rely on a narrow sliver of the data.
            higher_is_better = str(nec.get("outcome") or "").strip().lower() not in ("placement", "expected_placement")
            direction = 1.0 if higher_is_better else -1.0
            score = direction * float(tau) * (overlap_weight**2)
            return (
                0,
                -score,
                -(direction * float(tau)),
                frac_trimmed,
                -int(nec.get("n_used") or 0),
            )

        results.sort(key=_sort_key)
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
        "scope": scope,
        "items": results
    }


@app.get("/item-units")
def get_item_units(
    item: str = Query(..., description="Item id (e.g., GuinsoosRageblade)"),
    tokens: str = Query(default="", description="Additional filter tokens (comma-separated)"),
    min_sample: int = Query(default=30, description="Minimum sample size for inclusion"),
    top_k: int = Query(default=0, description="Max units to return (0 = unlimited)"),
    sort_mode: str = Query(
        default="necessity",
        description="Sort mode: necessity (AIPW ΔTop4), helpful (best avg), harmful (worst avg), impact (abs delta)",
    ),
):
    """
    Get best unit holders for a specific item given current filters.

    This endpoint returns units that equip the item (E:{unit}|{item}), ranked either by:
      - "necessity": cached AIPW ΔTop4 for equipping the item on that unit (causal),
      - or descriptive placement metrics within the filtered "item present" baseline.
    """
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    # Parse additional filter tokens (supports exclude tokens prefixed by '-' or '!')
    filter_tokens = [t.strip() for t in tokens.split(",") if t.strip()]
    include_filters: list[str] = []
    exclude_filters: list[str] = []
    for t in filter_tokens:
        if t.startswith("-") or t.startswith("!"):
            raw = t.lstrip("-!")
            if raw:
                exclude_filters.append(raw)
        else:
            include_filters.append(t)

    item_token = f"I:{item}"
    if item_token not in ENGINE.token_to_id:
        raise HTTPException(status_code=404, detail=f"Item '{item}' not found")

    # Base set: item present (any holder) + included filters, minus excluded filters.
    base_tokens = [item_token] + include_filters
    base_bitmap = ENGINE.filter_bitmap(base_tokens, exclude_filters)
    n_base = int(len(base_bitmap))
    if n_base == 0:
        return {
            "item": item,
            "filters": filter_tokens,
            "base": {"n": 0, "avg_placement": 4.5},
            "units": [],
        }
    avg_base = ENGINE.avg_placement_for_bitmap(base_bitmap)

    def _shrink_avg(avg: float, n: int, prior_mean: float, prior_weight: float) -> float:
        """Empirical-Bayes shrinkage to reduce small-sample noise (lower is better)."""
        if n <= 0:
            return prior_mean
        return (avg * n + prior_mean * prior_weight) / (n + prior_weight)

    # Shrinkage strength scales with min_sample so low-n holders don't look extreme.
    prior_weight = float(max(25, min(200, int(min_sample * 2))))

    # Consider only base unit tokens (exclude star-level U:Unit:2 etc).
    unit_tokens = [t for t in ENGINE.get_all_tokens_by_type("U:") if ":" not in t[2:]]

    results: list[dict] = []
    for unit_token in unit_tokens:
        unit = unit_token[2:]
        eq_token = f"E:{unit}|{item}"
        token_id = ENGINE.token_to_id.get(eq_token)
        if token_id is None:
            continue
        token_stats = ENGINE.tokens.get(token_id)
        if token_stats is None:
            continue

        with_bitmap = base_bitmap & token_stats.bitmap
        n_with = int(len(with_bitmap))
        if n_with < int(min_sample):
            continue

        avg_with = ENGINE.avg_placement_for_bitmap(with_bitmap)
        delta_raw = float(avg_with - avg_base)
        avg_adj = float(_shrink_avg(avg_with, n_with, avg_base, prior_weight))
        delta_adj = float(avg_adj - avg_base)

        results.append(
            {
                "unit": unit,
                "token": eq_token,
                "delta": round(delta_adj, 3),
                "avg_placement": round(avg_adj, 3),
                "n": n_with,
                "pct_of_base": round(n_with / float(n_base) * 100.0, 1) if n_base > 0 else 0,
                "raw_delta": round(delta_raw, 3),
                "raw_avg_placement": round(float(avg_with), 3),
            }
        )

    # Sort based on sort_mode
    if sort_mode == "helpful":
        # Best holders first (most negative delta = improves placement most vs item baseline)
        results.sort(key=lambda x: x["delta"])
    elif sort_mode == "harmful":
        # Worst holders first (most positive delta = worsens placement most vs item baseline)
        results.sort(key=lambda x: x["delta"], reverse=True)
    elif sort_mode == "necessity":
        # Cached AIPW ΔTop4 for equipping this item on the unit.
        necessity_outcome = "top4"
        use_cache = bool(getattr(ENGINE, "necessity_top4_ready", False))

        if use_cache:
            for row in results:
                eq_token = row.get("token")
                token_id = ENGINE.token_to_id.get(eq_token) if isinstance(eq_token, str) else None
                if token_id is None:
                    row["necessity"] = None
                    continue

                tau = float(ENGINE.necessity_top4_tau[token_id])
                if not np.isfinite(tau):
                    row["necessity"] = None
                    continue

                ci_low = float(ENGINE.necessity_top4_ci95_low[token_id])
                ci_high = float(ENGINE.necessity_top4_ci95_high[token_id])
                se = float(ENGINE.necessity_top4_se[token_id])
                raw_tau = float(ENGINE.necessity_top4_raw_tau[token_id])
                frac_trimmed = float(ENGINE.necessity_top4_frac_trimmed[token_id])
                e_p01 = float(ENGINE.necessity_top4_e_p01[token_id])
                e_p99 = float(ENGINE.necessity_top4_e_p99[token_id])
                scope_min_star = int(ENGINE.necessity_top4_scope_min_star[token_id] or 1)

                warnings: list[str] = []
                if np.isfinite(frac_trimmed) and frac_trimmed > 0.5:
                    warnings.append("Low overlap: large fraction of samples trimmed by propensity bounds.")
                if np.isfinite(e_p01) and np.isfinite(e_p99) and (e_p01 < 0.02 or e_p99 > 0.98):
                    warnings.append("Positivity warning: propensity is near 0/1 in parts of X (effect may be unstable).")

                row["necessity"] = {
                    "method": "aipw",
                    "outcome": necessity_outcome,
                    "tau": round(tau, 6),
                    "ci95_low": round(ci_low, 6) if np.isfinite(ci_low) else None,
                    "ci95_high": round(ci_high, 6) if np.isfinite(ci_high) else None,
                    "se": round(se, 6) if np.isfinite(se) else None,
                    "p_value": None,
                    "raw_tau": round(raw_tau, 6) if np.isfinite(raw_tau) else None,
                    "n_treated": int(ENGINE.necessity_top4_n_treated[token_id]),
                    "n_control": int(ENGINE.necessity_top4_n_control[token_id]),
                    "n_used": int(ENGINE.necessity_top4_n_used[token_id]),
                    "frac_trimmed": round(frac_trimmed, 6) if np.isfinite(frac_trimmed) else None,
                    "e_p01": round(e_p01, 6) if np.isfinite(e_p01) else None,
                    "e_p50": None,
                    "e_p99": round(e_p99, 6) if np.isfinite(e_p99) else None,
                    "risk_ratio": None,
                    "e_value": None,
                    "warnings": warnings,
                    "cached": True,
                    "scope_min_star": scope_min_star,
                }

        def _sort_key(x: dict) -> tuple:
            nec = x.get("necessity") or {}
            tau = nec.get("tau")
            if tau is None:
                return (1, 0.0, 1.0, 0)
            frac_trimmed = float(nec.get("frac_trimmed") or 1.0)
            overlap_weight = max(0.0, 1.0 - frac_trimmed)
            score = float(tau) * (overlap_weight**2)
            return (
                0,
                -score,
                -float(tau),
                frac_trimmed,
                -int(nec.get("n_used") or 0),
            )

        results.sort(key=_sort_key)
    else:
        # Impact: absolute delta, most impactful holders first
        results.sort(key=lambda x: abs(x["delta"]), reverse=True)

    # Apply top_k limit
    if top_k > 0:
        results = results[: int(top_k)]

    return {
        "item": item,
        "filters": filter_tokens,
        "base": {"n": n_base, "avg_placement": round(avg_base, 3)},
        "units": results,
    }


@app.get("/item-necessity")
def get_item_necessity(
    unit: str = Query(..., description="Unit name (e.g., KaiSa)"),
    item: str = Query(..., description="Item id (e.g., GuinsoosRageblade)"),
    tokens: str = Query(default="", description="Additional filter tokens (comma-separated)"),
    outcome: str = Query(default="top4", description="Outcome: top4, win, placement, rank_score"),
    unit_stars_min: int = Query(default=1, ge=1, le=6, description="Minimum star level for the unit"),
    auto_unit_stars_min: bool = Query(default=True, description="Auto-upgrade to 2★+ when the sample is dominated by 2★+ boards"),
    n_splits: int = Query(default=2, ge=2, le=5, description="Cross-fitting folds"),
    max_rows: int = Query(default=80_000, ge=1_000, le=500_000, description="Max rows to model (stratified subsample)"),
    min_token_freq: int = Query(default=25, ge=1, le=10_000, description="Min global token frequency for X features"),
    overlap_min: float = Query(default=0.05, ge=0.0, le=0.49, description="Min propensity overlap threshold"),
    overlap_max: float = Query(default=0.95, ge=0.51, le=1.0, description="Max propensity overlap threshold"),
    by_cluster: bool = Query(default=False, description="Also return a coarse CATE map by archetype clusters"),
    n_clusters: int = Query(default=8, ge=2, le=20, description="Clusters for coarse CATE map"),
):
    """
    Estimate "item necessity" as a causal effect using a doubly-robust AIPW estimator.

    Treatment T is having `item` equipped on `unit` (E:{unit}|{item}) among boards where
    the unit is present, within the provided filter context.
    """
    if ENGINE is None:
        raise HTTPException(status_code=503, detail="Engine not loaded")

    # Parse additional filter tokens (supports exclude tokens prefixed by '-' or '!')
    filter_tokens = [t.strip() for t in tokens.split(",") if t.strip()]
    include_filters: list[str] = []
    exclude_filters: list[str] = []
    for t in filter_tokens:
        if t.startswith("-") or t.startswith("!"):
            raw = t.lstrip("-!")
            if raw:
                exclude_filters.append(raw)
        else:
            include_filters.append(t)

    unit_token = f"U:{unit}"
    eq_token = f"E:{unit}|{item}"
    item_token = f"I:{item}"

    if unit_token not in ENGINE.token_to_id:
        raise HTTPException(status_code=404, detail=f"Unit '{unit}' not found")
    if eq_token not in ENGINE.token_to_id:
        raise HTTPException(status_code=404, detail=f"No data for '{eq_token}'")

    # Base set: unit + included filters, minus excluded filters
    base_tokens = [unit_token] + include_filters
    base_bitmap = ENGINE.filter_bitmap(base_tokens, exclude_filters)
    scope = {"unit_stars_min": unit_stars_min, "auto": False}
    has_star_filter = any(t.startswith(f"U:{unit}:") for t in include_filters) or any(
        t.startswith(f"U:{unit}:") for t in exclude_filters
    )
    if auto_unit_stars_min and not has_star_filter and unit_stars_min == 1:
        from pyroaring import BitMap

        star2plus_bm = BitMap()
        for s in range(2, 7):
            star_tok = f"U:{unit}:{s}"
            token_id = ENGINE.token_to_id.get(star_tok)
            if token_id is None:
                continue
            stats = ENGINE.tokens.get(token_id)
            if stats is None:
                continue
            star2plus_bm |= stats.bitmap
        if star2plus_bm:
            n_all = len(base_bitmap)
            n_2p = len(base_bitmap & star2plus_bm)
            if n_all > 0 and n_2p >= 2000 and (n_2p / float(n_all)) >= 0.7:
                unit_stars_min = 2
                scope = {"unit_stars_min": unit_stars_min, "auto": True}

    if unit_stars_min >= 2:
        from pyroaring import BitMap

        stars_bm = BitMap()
        for s in range(unit_stars_min, 7):
            star_tok = f"U:{unit}:{s}"
            token_id = ENGINE.token_to_id.get(star_tok)
            if token_id is None:
                continue
            stats = ENGINE.tokens.get(token_id)
            if stats is None:
                continue
            stars_bm |= stats.bitmap
        if stars_bm:
            base_bitmap &= stars_bm
    n_base = len(base_bitmap)
    if n_base == 0:
        return {
            "unit": unit,
            "item": item,
            "filters": filter_tokens,
            "base": {"n": 0},
            "scope": scope,
            "effect": None,
            "warning": "No matches for the current filters.",
        }

    eq_stats = ENGINE.tokens.get(ENGINE.token_to_id[eq_token])
    if eq_stats is None:
        raise HTTPException(status_code=404, detail=f"No stats for '{eq_token}'")

    treated_bm = base_bitmap & eq_stats.bitmap
    n_treated = len(treated_bm)
    n_control = int(n_base - n_treated)
    if n_treated < 50 or n_control < 50:
        return {
            "unit": unit,
            "item": item,
            "filters": filter_tokens,
            "base": {"n": int(n_base)},
            "scope": scope,
            "treatment": {"token": eq_token, "n_treated": int(n_treated), "n_control": int(n_control)},
            "effect": None,
            "warning": "Insufficient overlap/sample size for a reliable causal estimate.",
        }

    base_ids_full = np.array(base_bitmap.to_array(), dtype=np.int64)
    treated_ids = np.array(treated_bm.to_array(), dtype=np.int64)
    T_full = np.zeros((base_ids_full.size,), dtype=np.int8)
    if treated_ids.size:
        rows = np.searchsorted(base_ids_full, treated_ids).astype(np.int64, copy=False)
        T_full[rows] = 1

    # Stratified subsample for interactivity.
    rng = np.random.default_rng(42)
    if base_ids_full.size > max_rows:
        treated_idx = np.flatnonzero(T_full == 1)
        control_idx = np.flatnonzero(T_full == 0)

        min_per_group = min(5_000, max_rows // 10)
        desired_treated = int(min(treated_idx.size, max(min_per_group, round(max_rows * (treated_idx.size / base_ids_full.size)))))
        desired_control = int(min(control_idx.size, max(min_per_group, max_rows - desired_treated)))
        remaining = max_rows - (desired_treated + desired_control)
        if remaining > 0:
            # Fill from the larger group.
            if treated_idx.size - desired_treated > control_idx.size - desired_control:
                desired_treated = int(min(treated_idx.size, desired_treated + remaining))
            else:
                desired_control = int(min(control_idx.size, desired_control + remaining))

        pick_t = rng.choice(treated_idx, size=desired_treated, replace=False)
        pick_c = rng.choice(control_idx, size=desired_control, replace=False)
        sel = np.concatenate([pick_t, pick_c])
        sel.sort()
        base_ids = base_ids_full[sel]
        T = T_full[sel]
        from pyroaring import BitMap
        base_bitmap_model = BitMap(base_ids)
    else:
        base_ids = base_ids_full
        T = T_full
        base_bitmap_model = base_bitmap

    placements = ENGINE.placements[base_ids].astype(np.int16, copy=False)
    y, kind = placements_to_outcome(placements, outcome)

    def _rates(p: np.ndarray) -> dict[str, float]:
        n = int(p.size)
        if n == 0:
            return {"avg_placement": 4.5, "top4_rate": 0.0, "win_rate": 0.0}
        return {
            "avg_placement": float(p.mean()),
            "top4_rate": float((p <= 4).mean()),
            "win_rate": float((p == 1).mean()),
        }

    base_rates = _rates(placements)
    treated_rates = _rates(placements[T == 1])
    control_rates = _rates(placements[T == 0])

    # Fast path: use precomputed necessity cache when we're in the default
    # "unit present (auto 2★+)" context and Top4 outcome.
    cache_eligible = (
        outcome.strip().lower() in ("top4", "top_4", "topfour")
        and not include_filters
        and not exclude_filters
        and not by_cluster
        and getattr(ENGINE, "necessity_top4_ready", False)
        and n_splits == 2
        and max_rows == 80_000
        and min_token_freq == 25
        and overlap_min == 0.05
        and overlap_max == 0.95
    )
    if cache_eligible:
        tok_id = ENGINE.token_to_id.get(eq_token)
        if tok_id is not None:
            stored_scope = int(ENGINE.necessity_top4_scope_min_star[tok_id])
            if stored_scope and stored_scope == int(scope.get("unit_stars_min") or 1):
                tau = float(ENGINE.necessity_top4_tau[tok_id])
                if np.isfinite(tau):
                    ci_low = float(ENGINE.necessity_top4_ci95_low[tok_id])
                    ci_high = float(ENGINE.necessity_top4_ci95_high[tok_id])
                    se = float(ENGINE.necessity_top4_se[tok_id])
                    raw_tau_cached = float(ENGINE.necessity_top4_raw_tau[tok_id])
                    frac_trimmed = float(ENGINE.necessity_top4_frac_trimmed[tok_id])
                    e_p01 = float(ENGINE.necessity_top4_e_p01[tok_id])
                    e_p99 = float(ENGINE.necessity_top4_e_p99[tok_id])

                    warnings: list[str] = []
                    if np.isfinite(frac_trimmed) and frac_trimmed > 0.5:
                        warnings.append("Low overlap: large fraction of samples trimmed by propensity bounds.")
                    if np.isfinite(e_p01) and np.isfinite(e_p99) and (e_p01 < 0.02 or e_p99 > 0.98):
                        warnings.append("Positivity warning: propensity is near 0/1 in parts of X (effect may be unstable).")

                    return {
                        "unit": unit,
                        "item": item,
                        "filters": filter_tokens,
                        "scope": scope,
                        "base": {"n": int(n_base), **{k: round(v, 6) for k, v in base_rates.items()}},
                        "treatment": {
                            "token": eq_token,
                            "n_treated": int(n_treated),
                            "n_control": int(n_control),
                            "treated": {k: round(v, 6) for k, v in treated_rates.items()},
                            "control": {k: round(v, 6) for k, v in control_rates.items()},
                        },
                        "effect": {
                            "method": "aipw",
                            "outcome": "top4",
                            "kind": "binary",
                            "tau": round(tau, 6),
                            "ci95_low": round(ci_low, 6) if np.isfinite(ci_low) else None,
                            "ci95_high": round(ci_high, 6) if np.isfinite(ci_high) else None,
                            "se": round(se, 6) if np.isfinite(se) else None,
                            "p_value": None,
                            "raw_tau": round(raw_tau_cached, 6) if np.isfinite(raw_tau_cached) else None,
                            "y1": None,
                            "y0": None,
                        },
                        "overlap": {
                            "n_used": int(ENGINE.necessity_top4_n_used[tok_id]),
                            "frac_trimmed": round(frac_trimmed, 6) if np.isfinite(frac_trimmed) else None,
                            "e_min": None,
                            "e_p01": round(e_p01, 6) if np.isfinite(e_p01) else None,
                            "e_p50": None,
                            "e_p99": round(e_p99, 6) if np.isfinite(e_p99) else None,
                            "e_max": None,
                            "bounds": [float(overlap_min), float(overlap_max)],
                        },
                        "warning": None,
                        "warnings": warnings,
                        "cached": True,
                    }

    # Feature matrix X: sparse token presence + numeric board-strength proxies.
    exclude = {unit_token, eq_token, item_token}
    feature_params = TokenFeatureParams(
        use_units=True,
        use_traits=True,
        use_items=False,
        use_equipped=False,
        include_star_units=False,
        include_tier_traits=True,
        min_token_freq=min_token_freq,
    )
    feature_tokens = select_feature_tokens(ENGINE, feature_params, exclude=exclude)
    X_tok, kept, base_counts, feature_rows = build_sparse_feature_matrix(
        ENGINE, base_bitmap_model, base_ids, feature_tokens
    )

    # Board-strength proxy features.
    #
    # Important: avoid "bad controls" that mechanically include the treated item.
    #
    # Instead of using raw totals, we subtract *all* items equipped on this unit and
    # keep "rest-of-board" counts. This prevents X from trivially encoding T while
    # still controlling for overall board strength.
    Z_full = board_strength_features(ENGINE, base_ids).astype(np.float32, copy=False)
    unit_item_count = np.zeros((base_ids.size,), dtype=np.float32)
    unit_component_count = np.zeros((base_ids.size,), dtype=np.float32)
    unit_completed_count = np.zeros((base_ids.size,), dtype=np.float32)

    prefix = f"E:{unit}|"
    for eq_tok in ENGINE.id_to_token:
        if not eq_tok.startswith(prefix):
            continue
        if parse_token(eq_tok).get("copies", 1) != 1:
            continue
        tok_id = ENGINE.token_to_id.get(eq_tok)
        tok_stats = ENGINE.tokens.get(tok_id) if tok_id is not None else None
        if tok_stats is None:
            continue
        bm = base_bitmap_model & tok_stats.bitmap
        if not bm:
            continue
        ids = np.array(bm.to_array(), dtype=np.int64)
        rows = np.searchsorted(base_ids, ids).astype(np.int64, copy=False)
        unit_item_count[rows] += 1.0

        item_name = parse_token(eq_tok).get("item")
        if not item_name:
            continue
        if get_item_type(item_name) == "component":
            unit_component_count[rows] += 1.0
        else:
            unit_completed_count[rows] += 1.0

    other_item_count = np.clip(Z_full[:, 0] - unit_item_count, 0.0, None)
    other_component_count = np.clip(Z_full[:, 1] - unit_component_count, 0.0, None)
    other_completed_count = np.clip(Z_full[:, 2] - unit_completed_count, 0.0, None)
    Z = np.stack(
        [
            other_item_count,
            other_component_count,
            other_completed_count,
            Z_full[:, 3],
            Z_full[:, 4],
            Z_full[:, 5],
            Z_full[:, 6],
        ],
        axis=1,
    )
    from scipy.sparse import csr_matrix, hstack
    X = hstack([X_tok.astype(np.float32), csr_matrix(Z, dtype=np.float32)], format="csr")

    cfg = AIPWConfig(
        n_splits=n_splits,
        random_state=42,
        clip_eps=0.01,
        trim_low=overlap_min,
        trim_high=overlap_max,
    )
    try:
        est, phi, e_hat, used = aipw_ate(X, T, y, kind=kind, cfg=cfg)
    except OverlapError as e:
        warnings: list[str] = []
        if e.frac_trimmed > 0.5:
            warnings.append("Low overlap: large fraction of samples trimmed by propensity bounds.")
        if e.e_p01 < 0.02 or e.e_p99 > 0.98:
            warnings.append("Positivity warning: propensity is near 0/1 in parts of X (effect may be unstable).")
        if not warnings:
            warnings.append("Low overlap: effect is not reliably identifiable in this context.")

        return {
            "unit": unit,
            "item": item,
            "filters": filter_tokens,
            "scope": scope,
            "base": {"n": int(n_base), **{k: round(v, 6) for k, v in base_rates.items()}},
            "treatment": {
                "token": eq_token,
                "n_treated": int(n_treated),
                "n_control": int(n_control),
                "treated": {k: round(v, 6) for k, v in treated_rates.items()},
                "control": {k: round(v, 6) for k, v in control_rates.items()},
            },
            "effect": None,
            "overlap": {
                "n_used": int(e.n_used),
                "frac_trimmed": round(float(e.frac_trimmed), 6),
                "e_min": round(float(e.e_min), 6),
                "e_p01": round(float(e.e_p01), 6),
                "e_p50": round(float(e.e_p50), 6),
                "e_p99": round(float(e.e_p99), 6),
                "e_max": round(float(e.e_max), 6),
                "bounds": [float(overlap_min), float(overlap_max)],
                "n_treated_used": int(e.n_treated_used),
                "n_control_used": int(e.n_control_used),
            },
            "warning": str(e),
            "warnings": warnings,
        }

    raw_tau = float(y[T == 1].mean() - y[T == 0].mean())
    warnings: list[str] = []
    if est.frac_trimmed > 0.5:
        warnings.append("Low overlap: large fraction of samples trimmed by propensity bounds.")
    if est.e_p01 < 0.02 or est.e_p99 > 0.98:
        warnings.append("Positivity warning: propensity is near 0/1 in parts of X (effect may be unstable).")

    result: dict = {
        "unit": unit,
        "item": item,
        "filters": filter_tokens,
        "scope": scope,
        "base": {"n": int(n_base), **{k: round(v, 6) for k, v in base_rates.items()}},
        "treatment": {
            "token": eq_token,
            "n_treated": int(n_treated),
            "n_control": int(n_control),
            "treated": {k: round(v, 6) for k, v in treated_rates.items()},
            "control": {k: round(v, 6) for k, v in control_rates.items()},
        },
        "effect": {
            "method": "aipw",
            "outcome": outcome,
            "kind": kind,
            "tau": round(float(est.tau), 6),
            "ci95_low": round(float(est.ci95_low), 6) if np.isfinite(est.ci95_low) else None,
            "ci95_high": round(float(est.ci95_high), 6) if np.isfinite(est.ci95_high) else None,
            "se": round(float(est.se), 6) if np.isfinite(est.se) else None,
            "p_value": round(float(est.p_value), 6) if est.p_value is not None else None,
            "raw_tau": round(raw_tau, 6),
            "y1": round(float(est.y1), 6),
            "y0": round(float(est.y0), 6),
        },
        "overlap": {
            "n_used": int(est.n_used),
            "frac_trimmed": round(float(est.frac_trimmed), 6),
            "e_min": round(float(est.e_min), 6),
            "e_p01": round(float(est.e_p01), 6),
            "e_p50": round(float(est.e_p50), 6),
            "e_p99": round(float(est.e_p99), 6),
            "e_max": round(float(est.e_max), 6),
            "bounds": [overlap_min, overlap_max],
        },
        "features": {
            "n_rows_modeled": int(base_ids.size),
            "n_token_features": int(len(kept)),
            "n_proxy_features": int(Z.shape[1]),
            "min_token_freq": int(min_token_freq),
        },
    }
    if kind == "binary":
        rr = None
        if est.y0 > 0 and np.isfinite(est.y0) and np.isfinite(est.y1):
            rr = float(est.y1 / est.y0)
        e_value = e_value_from_risk_ratio(rr) if rr is not None else None
        result["sensitivity"] = {
            "risk_ratio": round(rr, 6) if rr is not None and np.isfinite(rr) else None,
            "e_value": round(float(e_value), 6) if e_value is not None and np.isfinite(e_value) else None,
        }
    if warnings:
        result["warnings"] = warnings

    if by_cluster and base_ids.size >= 2_000 and len(kept) >= 10:
        try:
            from sklearn.cluster import MiniBatchKMeans

            # Cluster on coarse comp context (tokens only) and summarize τ(X) by cluster.
            kmeans = MiniBatchKMeans(
                n_clusters=n_clusters,
                random_state=42,
                batch_size=2048,
                n_init=3,
                reassignment_ratio=0.01,
            )
            labels = kmeans.fit_predict(X_tok)
            cluster_sizes = np.bincount(labels, minlength=n_clusters).astype(np.int32, copy=False)
            base_freq = base_counts.astype(np.float32) / float(base_ids.size) if base_ids.size else np.zeros((len(kept),), dtype=np.float32)

            # Precompute counts(feature present) per cluster for each feature.
            feature_cluster_counts = np.zeros((n_clusters, len(kept)), dtype=np.int32)
            for j, rows in enumerate(feature_rows):
                feature_cluster_counts[:, j] = np.bincount(labels[rows], minlength=n_clusters)

            def _signature(kept_features: list[str], cluster_freq: np.ndarray, base_freq: np.ndarray) -> list[str]:
                eps = 1e-9
                lift = cluster_freq / np.maximum(base_freq, eps)
                score = cluster_freq * np.log2(np.maximum(lift, 1.0))

                def pick(prefix: str, k: int) -> list[str]:
                    idx = [i for i, t in enumerate(kept_features) if t.startswith(prefix)]
                    if not idx:
                        return []
                    idx_sorted = sorted(idx, key=lambda i: float(score[i]), reverse=True)
                    out: list[str] = []
                    for i in idx_sorted:
                        if cluster_freq[i] < 0.2:
                            continue
                        out.append(kept_features[i])
                        if len(out) >= k:
                            break
                    return out

                sig: list[str] = []
                sig.extend(pick("U:", 4))
                sig.extend(pick("T:", 3))
                sig.extend(pick("I:", 2))
                seen: set[str] = set()
                ordered: list[str] = []
                for t in sig:
                    if t in seen:
                        continue
                    seen.add(t)
                    ordered.append(t)
                return ordered

            clusters_out: list[dict] = []
            for c in range(n_clusters):
                size = int(cluster_sizes[c])
                if size < 250:
                    continue
                in_c = labels == c
                used_c = used & in_c
                n_used_c = int(used_c.sum())
                if n_used_c < 200:
                    continue

                cluster_counts = feature_cluster_counts[c].astype(np.float32, copy=False)
                cluster_freq = cluster_counts / float(size)
                tau_c = float(phi[used_c].mean())
                se_c = float(phi[used_c].std(ddof=1) / np.sqrt(n_used_c)) if n_used_c > 1 else float("nan")
                e_c = e_hat[in_c]
                qs = np.quantile(e_c, [0.1, 0.5, 0.9])

                clusters_out.append(
                    {
                        "cluster_id": int(c),
                        "size": size,
                        "n_used": n_used_c,
                        "share": round(size / float(base_ids.size), 6) if base_ids.size else 0.0,
                        "tau": round(tau_c, 6),
                        "se": round(se_c, 6) if np.isfinite(se_c) else None,
                        "ci95_low": round(tau_c - 1.96 * se_c, 6) if np.isfinite(se_c) else None,
                        "ci95_high": round(tau_c + 1.96 * se_c, 6) if np.isfinite(se_c) else None,
                        "e_p10": round(float(qs[0]), 6),
                        "e_p50": round(float(qs[1]), 6),
                        "e_p90": round(float(qs[2]), 6),
                        "signature_tokens": _signature(kept, cluster_freq, base_freq),
                    }
                )

            clusters_out.sort(key=lambda d: (-(d["size"]), abs(float(d["tau"]))))
            result["cate_by_cluster"] = clusters_out[: min(12, len(clusters_out))]
        except Exception as e:
            result["cate_by_cluster_error"] = str(e)

    return result


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


@app.get("/changelog")
@app.get("/changelog/")
def read_changelog():
    return FileResponse(str(static_path / "index.html"))


def main():
    import os
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)


if __name__ == "__main__":
    main()
