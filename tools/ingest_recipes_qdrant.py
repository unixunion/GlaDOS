"""Batch embed recipes into Qdrant for semantic ingredient search.

Processes all recipes from the recipe cache, embeds each recipe's core
ingredient list, and upserts into the `recipe_ingredients` Qdrant collection.

Usage:
    python tools/ingest_recipes_qdrant.py
    python tools/ingest_recipes_qdrant.py --url http://localhost:6333
    python tools/ingest_recipes_qdrant.py --stats

Requires: Qdrant running, sentence-transformers installed.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="INFO")


def main():
    parser = argparse.ArgumentParser(description="Embed recipes into Qdrant")
    parser.add_argument("--url", default="http://localhost:6333", help="Qdrant server URL")
    parser.add_argument("--stats", action="store_true", help="Show collection stats only")
    args = parser.parse_args()

    # Override Qdrant URL if provided
    os.environ["QDRANT_URL_OVERRIDE"] = args.url

    from plugins.recipes.recipe_api import load_recipes, recipes, _load_ingredient_map
    from plugins.recipes.recipe_qdrant import (
        _init, _qdrant, COLLECTION_NAME, _build_collection, _get_collection_metadata,
        _collection_exists,
    )

    # Load recipe data
    print("Loading recipe data...")
    load_recipes("data/recipes/dataset.csv")
    print(f"Loaded {len(recipes)} recipes")

    map_hash = _load_ingredient_map()

    # Init Qdrant connection
    if not _init():
        print("Failed to connect to Qdrant or load embedding model")
        sys.exit(1)

    if args.stats:
        if _collection_exists():
            from qdrant_client import QdrantClient
            client = QdrantClient(url=args.url, timeout=5)
            info = client.get_collection(COLLECTION_NAME)
            meta = _get_collection_metadata()
            print(f"\nCollection: {COLLECTION_NAME}")
            print(f"  Points: {info.points_count}")
            print(f"  Stored recipe count: {meta.get('recipe_count', '?')}")
            print(f"  Stored map hash: {meta.get('ingredient_map_hash', '?')[:12]}...")
            print(f"  Current map hash: {map_hash[:12]}...")
            stale = (meta.get('recipe_count') != len(recipes) or
                     meta.get('ingredient_map_hash') != map_hash)
            print(f"  Status: {'STALE — rebuild needed' if stale else 'up-to-date'}")
        else:
            print(f"Collection '{COLLECTION_NAME}' does not exist")
        return

    # Build/rebuild
    _build_collection(recipes, map_hash)
    print("Done!")


if __name__ == "__main__":
    main()
