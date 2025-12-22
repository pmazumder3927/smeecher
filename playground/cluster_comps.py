"""
Comp Archetype Discovery via K-Means Clustering
================================================

This script clusters TFT team compositions to discover "archetypes" -
groups of comps that share similar unit/trait patterns and perform similarly.

The idea:
1. Each player_match becomes a feature vector (which units/traits were played)
2. K-means groups similar vectors into clusters
3. We analyze each cluster's avg placement and defining characteristics

This is exploratory - tweak n_clusters, features, and filters to find insights.

Usage:
    uv run python playground/cluster_comps.py

Requirements:
    - Built engine.bin (run smeecher-build first)
    - scikit-learn (uv add scikit-learn)
"""

import sys
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import numpy as np
from collections import Counter
from dataclasses import dataclass

from graph.engine import GraphEngine


@dataclass
class ClusterProfile:
    """Summary of a discovered comp archetype."""
    cluster_id: int
    size: int
    avg_placement: float
    top_units: list[tuple[str, float]]      # (unit, % of cluster)
    top_traits: list[tuple[str, float]]     # (trait, % of cluster)
    top_items: list[tuple[str, float]]      # (item, % of cluster)
    defining_units: list[str]               # Units that appear way more here than overall


def build_feature_matrix(
    engine: GraphEngine,
    use_units: bool = True,
    use_traits: bool = True,
    use_items: bool = False,  # Items add noise, optional
    min_token_freq: int = 100,  # Skip rare tokens
) -> tuple[np.ndarray, list[int], list[str]]:
    """
    Build a binary feature matrix: rows=matches, cols=tokens.

    Returns:
        X: np.ndarray of shape (n_matches, n_features)
        pm_ids: list of player_match IDs (row index -> pm_id)
        feature_names: list of token strings (col index -> token)

    Memory note: 60k matches × 200 features × 1 byte = 12MB, very manageable.
    """
    # Collect feature tokens
    features = []
    if use_units:
        features += [t for t in engine.id_to_token if t.startswith("U:")]
    if use_traits:
        # Only base traits (T:Mage), not tiered (T:Mage:2)
        features += [t for t in engine.id_to_token
                     if t.startswith("T:") and t.count(":") == 1]
    if use_items:
        features += [t for t in engine.id_to_token if t.startswith("I:")]

    # Filter by frequency
    features = [
        t for t in features
        if engine.token_to_id.get(t) is not None
        and engine.tokens.get(engine.token_to_id[t]) is not None
        and engine.tokens[engine.token_to_id[t]].count >= min_token_freq
    ]

    print(f"Using {len(features)} features (units + traits + items)")

    # Build matrix
    pm_ids = sorted(engine.all_players)  # Consistent ordering
    pm_to_idx = {pm: i for i, pm in enumerate(pm_ids)}

    n_matches = len(pm_ids)
    n_features = len(features)

    print(f"Building {n_matches} x {n_features} feature matrix...")

    # Use int8 to save memory (0 or 1)
    X = np.zeros((n_matches, n_features), dtype=np.int8)

    for j, token in enumerate(features):
        token_id = engine.token_to_id[token]
        bitmap = engine.tokens[token_id].bitmap
        for pm_id in bitmap:
            if pm_id in pm_to_idx:  # Should always be true
                X[pm_to_idx[pm_id], j] = 1

    print(f"Matrix built: {X.nbytes / 1024 / 1024:.1f} MB")

    return X, pm_ids, features


def analyze_cluster(
    engine: GraphEngine,
    pm_ids_in_cluster: list[int],
    all_pm_ids: list[int],
    features: list[str],
    X: np.ndarray,
    cluster_mask: np.ndarray,
    cluster_id: int,
    top_k: int = 10,
) -> ClusterProfile:
    """
    Analyze a single cluster to extract its defining characteristics.
    """
    # Avg placement
    placements = [engine.placements[pm] for pm in pm_ids_in_cluster]
    avg_placement = np.mean(placements)

    # Feature prevalence in this cluster
    cluster_X = X[cluster_mask]
    cluster_freq = cluster_X.mean(axis=0)  # % of cluster with each feature

    # Overall prevalence (for comparison)
    overall_freq = X.mean(axis=0)

    # Separate by type
    units = [(f, cluster_freq[i]) for i, f in enumerate(features) if f.startswith("U:")]
    traits = [(f, cluster_freq[i]) for i, f in enumerate(features) if f.startswith("T:")]
    items = [(f, cluster_freq[i]) for i, f in enumerate(features) if f.startswith("I:")]

    # Sort by prevalence
    top_units = sorted(units, key=lambda x: -x[1])[:top_k]
    top_traits = sorted(traits, key=lambda x: -x[1])[:top_k]
    top_items = sorted(items, key=lambda x: -x[1])[:top_k]

    # Find "defining" units: appear way more in this cluster than overall
    # Lift = cluster_freq / overall_freq (>2 means 2x more common here)
    defining = []
    for i, f in enumerate(features):
        if f.startswith("U:") and overall_freq[i] > 0.01:  # Skip very rare
            lift = cluster_freq[i] / overall_freq[i]
            if lift > 2.0 and cluster_freq[i] > 0.3:  # 2x lift AND >30% prevalence
                defining.append((f, lift, cluster_freq[i]))

    defining = sorted(defining, key=lambda x: -x[1])[:5]
    defining_units = [f[2:] for f, _, _ in defining]  # Strip "U:" prefix

    return ClusterProfile(
        cluster_id=cluster_id,
        size=len(pm_ids_in_cluster),
        avg_placement=avg_placement,
        top_units=[(u[2:], pct) for u, pct in top_units],  # Strip prefix
        top_traits=[(t[2:], pct) for t, pct in top_traits],
        top_items=[(i[2:], pct) for i, pct in top_items] if items else [],
        defining_units=defining_units,
    )


def print_cluster_report(profiles: list[ClusterProfile]):
    """Pretty-print cluster analysis results."""

    # Sort by avg placement (best first)
    profiles = sorted(profiles, key=lambda p: p.avg_placement)

    print("\n" + "=" * 70)
    print("COMP ARCHETYPE DISCOVERY RESULTS")
    print("=" * 70)

    for p in profiles:
        placement_bar = "█" * int((8 - p.avg_placement) * 3)  # Visual bar

        print(f"\n{'─' * 70}")
        print(f"CLUSTER {p.cluster_id}: {p.size:,} matches | Avg Placement: {p.avg_placement:.2f} {placement_bar}")
        print(f"{'─' * 70}")

        if p.defining_units:
            print(f"  Defining units: {', '.join(p.defining_units)}")

        print(f"\n  Top Units:")
        for unit, pct in p.top_units[:6]:
            bar = "▓" * int(pct * 20)
            print(f"    {unit:<20} {pct*100:5.1f}% {bar}")

        print(f"\n  Top Traits:")
        for trait, pct in p.top_traits[:5]:
            bar = "▓" * int(pct * 20)
            print(f"    {trait:<20} {pct*100:5.1f}% {bar}")

        if p.top_items:
            print(f"\n  Top Items:")
            for item, pct in p.top_items[:5]:
                bar = "▓" * int(pct * 20)
                print(f"    {item:<20} {pct*100:5.1f}% {bar}")

    # Summary table - BEST
    print("\n" + "=" * 70)
    print("BEST COMPS (lowest avg placement = wins more)")
    print("=" * 70)
    print(f"{'Cluster':<8} {'Size':>7} {'AvgPl':>6} {'Defining Units':<50}")
    print("-" * 70)
    for p in profiles[:6]:  # Top 6
        defining = ", ".join(p.defining_units[:5]) if p.defining_units else "(mixed)"
        print(f"{p.cluster_id:<8} {p.size:>7,} {p.avg_placement:>6.2f} {defining:<50}")

    # Summary table - WORST
    print("\n" + "=" * 70)
    print("WORST COMPS (highest avg placement = loses more)")
    print("=" * 70)
    print(f"{'Cluster':<8} {'Size':>7} {'AvgPl':>6} {'Defining Units':<50}")
    print("-" * 70)
    for p in reversed(profiles[-6:]):  # Bottom 6
        defining = ", ".join(p.defining_units[:5]) if p.defining_units else "(mixed/no identity)"
        print(f"{p.cluster_id:<8} {p.size:>7,} {p.avg_placement:>6.2f} {defining:<50}")


def run_clustering(
    engine: GraphEngine,
    n_clusters: int = 12,
    use_units: bool = True,
    use_traits: bool = True,
    use_items: bool = False,
    min_token_freq: int = 100,
    random_state: int = 42,
) -> list[ClusterProfile]:
    """
    Main clustering pipeline.

    Args:
        engine: Loaded GraphEngine
        n_clusters: Number of archetypes to discover (try 8-20)
        use_units: Include unit presence as features
        use_traits: Include trait presence as features
        use_items: Include item presence as features (adds noise)
        min_token_freq: Skip tokens appearing in fewer matches
        random_state: For reproducibility

    Returns:
        List of ClusterProfile objects
    """
    try:
        from sklearn.cluster import MiniBatchKMeans
    except ImportError:
        print("ERROR: scikit-learn not installed. Run: uv add scikit-learn")
        sys.exit(1)

    # Build feature matrix
    X, pm_ids, features = build_feature_matrix(
        engine,
        use_units=use_units,
        use_traits=use_traits,
        use_items=use_items,
        min_token_freq=min_token_freq,
    )

    # Run k-means (MiniBatch is faster for large datasets)
    print(f"\nRunning MiniBatchKMeans with {n_clusters} clusters...")
    kmeans = MiniBatchKMeans(
        n_clusters=n_clusters,
        random_state=random_state,
        batch_size=1024,
        n_init=3,
    )
    labels = kmeans.fit_predict(X)

    print(f"Clustering complete. Analyzing clusters...")

    # Analyze each cluster
    profiles = []
    for c in range(n_clusters):
        mask = labels == c
        cluster_pm_ids = [pm_ids[i] for i in range(len(pm_ids)) if mask[i]]

        if len(cluster_pm_ids) < 50:  # Skip tiny clusters
            continue

        profile = analyze_cluster(
            engine=engine,
            pm_ids_in_cluster=cluster_pm_ids,
            all_pm_ids=pm_ids,
            features=features,
            X=X,
            cluster_mask=mask,
            cluster_id=c,
        )
        profiles.append(profile)

    return profiles


def main():
    """Entry point for exploration."""

    print("Loading engine...")
    engine_path = Path(__file__).parent.parent / "data" / "engine.bin"

    if not engine_path.exists():
        print(f"ERROR: {engine_path} not found. Run smeecher-build first.")
        sys.exit(1)

    engine = GraphEngine.load(str(engine_path))
    stats = engine.stats()
    print(f"Loaded: {stats['total_matches']:,} matches, {stats['total_tokens']} tokens")

    # Run clustering
    # Tweak these parameters to explore!
    profiles = run_clustering(
        engine,
        n_clusters=15,       # Try 8-20 to find natural groupings
        use_units=True,      # Core feature
        use_traits=True,     # Helps identify synergies
        use_items=True,      # Include items for full comp picture
        min_token_freq=100,  # Filter rare tokens
    )

    # Print results
    print_cluster_report(profiles)

    # Return for interactive use
    return engine, profiles


if __name__ == "__main__":
    engine, profiles = main()
