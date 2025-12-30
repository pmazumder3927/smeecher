"""
Item taxonomy helpers shared across the backend.

This module intentionally keeps logic "best-effort" and dependency-free so it
can be used in both the query server and the engine builder.
"""

from __future__ import annotations


COMPONENT_ITEMS: set[str] = {
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
    Best-effort item categorization for filtering and feature engineering.

    Returns one of: component, full, artifact, emblem, radiant
    """
    if item_name in COMPONENT_ITEMS:
        return "component"
    # Set-specific / generated items often keep a "TFTxx_" or "TFTx_Item_" prefix
    # even after cleaning. Treat them as artifacts so "full" remains close to
    # "standard craftable" completed items in filtering UIs.
    if (item_name.startswith("TFT") or item_name.startswith("Set")) and "_" in item_name:
        return "artifact"
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
    if get_item_type(item_name) != "full":
        return None

    if "_" in item_name:
        prefix = item_name.split("_", 1)[0]
        if prefix.upper().startswith("TFT") or prefix.lower().startswith("set"):
            return None
        return prefix or None

    return None
