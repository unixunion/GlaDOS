"""Qdrant-backed semantic recipe search.

Embeds each recipe's core ingredient list into a Qdrant collection for
semantic matching. Falls back gracefully when Qdrant is unavailable.

The collection auto-builds on first startup and rebuilds when the recipe
data or ingredient map changes.
"""
import hashlib
import threading
import uuid

from loguru import logger

from glados.config import GladosConfig
from glados.system.event_system import EventSystem, EventMessage, EventHook
from rapidfuzz import fuzz

# Shared embedding lock — prevents sentence-transformers segfaults across threads.
# Same lock used by KnowledgeRAG and ConversationStore.
try:
    from plugins.knowledge.rag import _embed_lock
except ImportError:
    _embed_lock = threading.Lock()

COLLECTION_NAME = "recipe_ingredients"
EMBED_MODEL_NAME = "all-MiniLM-L6-v2"

# Module-level state
_qdrant = None
_embed_model = None
_ready = False
_init_lock = threading.Lock()


def _init() -> bool:
    """Initialize Qdrant client and embedding model. Thread-safe."""
    global _qdrant, _embed_model, _ready
    if _ready:
        return True
    with _init_lock:
        if _ready:
            return True
        config = GladosConfig.from_yaml("glados_config.yml")
        qdrant_url = getattr(config, "qdrant_url", "http://localhost:6333")

        try:
            from qdrant_client import QdrantClient
            _qdrant = QdrantClient(url=qdrant_url, timeout=5)
            _qdrant.get_collections()
            logger.info(f"[RecipeQdrant] Connected to Qdrant at {qdrant_url}")
        except Exception as e:
            logger.warning(f"[RecipeQdrant] Cannot connect to Qdrant: {e}")
            return False

        try:
            from sentence_transformers import SentenceTransformer
            _embed_model = SentenceTransformer(EMBED_MODEL_NAME)
            logger.info(f"[RecipeQdrant] Loaded embedding model: {EMBED_MODEL_NAME}")
        except Exception as e:
            logger.warning(f"[RecipeQdrant] Cannot load embedding model: {e}")
            return False

        _ready = True
        return True


def _collection_exists() -> bool:
    """Check if the recipe_ingredients collection exists."""
    if not _qdrant:
        return False
    try:
        _qdrant.get_collection(COLLECTION_NAME)
        return True
    except Exception:
        return False


def _get_collection_metadata() -> dict:
    """Get stored metadata from collection (recipe count, map hash)."""
    if not _qdrant:
        return {}
    try:
        info = _qdrant.get_collection(COLLECTION_NAME)
        # Qdrant doesn't have native collection metadata, so we store a
        # sentinel point with id="__meta__" in the payload
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        results = _qdrant.scroll(
            collection_name=COLLECTION_NAME,
            scroll_filter=Filter(must=[FieldCondition(key="source", match=MatchValue(value="__meta__"))]),
            limit=1,
        )
        points = results[0]
        if points:
            return points[0].payload or {}
        return {"points_count": info.points_count}
    except Exception:
        return {}


def _build_collection(recipes: list, ingredient_map_hash: str):
    """Batch embed all recipes and create/recreate the Qdrant collection."""
    from qdrant_client.models import Distance, VectorParams, PointStruct
    from tqdm import tqdm

    logger.info(f"[RecipeQdrant] Building collection with {len(recipes)} recipes...")

    # Get vector dimension
    with _embed_lock:
        dim = _embed_model.get_sentence_embedding_dimension()

    # Recreate collection
    try:
        _qdrant.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    _qdrant.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )

    # Batch embed and upsert
    batch_size = 200
    points = []

    for idx, recipe in enumerate(tqdm(recipes, desc="Embedding recipes", unit="recipe")):
        core_ings = recipe.get("core_ingredients", recipe.get("cleaned_ingredients", []))
        if not core_ings:
            continue

        document = " ".join(core_ings)
        with _embed_lock:
            vector = _embed_model.encode(document).tolist()

        points.append(PointStruct(
            id=idx,
            vector=vector,
            payload={
                "title": recipe["title"],
                "core_ingredients": core_ings,
                "ingredient_count": len(core_ings),
                "image_name": recipe.get("image_name"),
                "source": "dataset",
            },
        ))

        if len(points) >= batch_size:
            _qdrant.upsert(collection_name=COLLECTION_NAME, points=points)
            points = []

    # Flush remaining
    if points:
        _qdrant.upsert(collection_name=COLLECTION_NAME, points=points)

    # Store metadata sentinel
    meta_point = PointStruct(
        id=len(recipes),  # use index after last recipe
        vector=[0.0] * dim,
        payload={
            "source": "__meta__",
            "recipe_count": len(recipes),
            "ingredient_map_hash": ingredient_map_hash,
        },
    )
    _qdrant.upsert(collection_name=COLLECTION_NAME, points=[meta_point])

    logger.success(f"[RecipeQdrant] Collection built: {len(recipes)} recipes embedded")


def ensure_collection():
    """Ensure the collection exists and is up-to-date. Rebuilds if stale."""
    from plugins.recipes.recipe_api import recipes

    if not recipes:
        logger.warning("[RecipeQdrant] No recipes loaded — skipping collection build")
        return

    if _collection_exists():
        meta = _get_collection_metadata()
        stored_count = meta.get("recipe_count", 0)
        if stored_count == len(recipes):
            try:
                info = _qdrant.get_collection(COLLECTION_NAME)
                logger.info(f"[RecipeQdrant] Collection up-to-date: {info.points_count} points")
                return
            except Exception:
                pass

    _build_collection(recipes, "")


def search_recipes_semantic(query_ingredients: list[str], top_k: int = 20) -> list[dict] | None:
    """Search recipes by semantic ingredient matching.

    Returns list of recipe dicts (same format as search_by_ingredients) or
    None if Qdrant is not available/ready.
    """
    if not _ready or not _qdrant or not _embed_model:
        return None

    if not query_ingredients:
        return []

    # Embed query: join all pantry items into a single query string
    query_text = " ".join(q.lower() for q in query_ingredients)
    try:
        with _embed_lock:
            query_vector = _embed_model.encode(query_text).tolist()
    except Exception as e:
        logger.warning(f"[RecipeQdrant] Embedding failed: {e}")
        return None

    # Search Qdrant
    try:
        response = _qdrant.query_points(
            collection_name=COLLECTION_NAME,
            query=query_vector,
            limit=top_k * 3,  # fetch more, post-filter for actual overlap
            score_threshold=0.3,
        )
        hits = response.points if hasattr(response, "points") else []
    except Exception as e:
        logger.warning(f"[RecipeQdrant] Search failed: {e}")
        return None

    if not hits:
        return []

    # Post-filter: count actual ingredient overlap
    from plugins.recipes.recipe_api import recipes as all_recipes
    results = []
    for hit in hits:
        payload = hit.payload or {}
        if payload.get("source") == "__meta__":
            continue
        recipe_core = payload.get("core_ingredients", [])
        matched = []
        for q_ing in query_ingredients:
            q_lower = q_ing.lower()
            for r_ing in recipe_core:
                if q_lower == r_ing or fuzz.partial_ratio(q_lower, r_ing) >= 75:
                    matched.append(q_ing)
                    break
        if not matched:
            continue

        # Get full recipe data for directions etc.
        recipe_idx = hit.id
        if isinstance(recipe_idx, int) and recipe_idx < len(all_recipes):
            recipe = all_recipes[recipe_idx]
        else:
            recipe = {"title": payload.get("title", ""), "ingredients": [], "directions": []}

        results.append({
            "title": payload.get("title", recipe.get("title", "")),
            "ingredients": recipe.get("ingredients", []),
            "directions": recipe.get("directions", []),
            "image_name": payload.get("image_name"),
            "matched_ingredients": matched,
            "match_count": len(matched),
            "search_method": "semantic",
        })

    results.sort(key=lambda x: x["match_count"], reverse=True)
    return results[:top_k]


def embed_single_recipe(recipe_idx: int, recipe: dict):
    """Embed and upsert a single recipe (for runtime additions)."""
    if not _ready:
        return
    from qdrant_client.models import PointStruct

    core_ings = recipe.get("core_ingredients", recipe.get("cleaned_ingredients", []))
    if not core_ings:
        return

    document = " ".join(core_ings)
    try:
        with _embed_lock:
            vector = _embed_model.encode(document).tolist()

        point = PointStruct(
            id=recipe_idx,
            vector=vector,
            payload={
                "title": recipe["title"],
                "core_ingredients": core_ings,
                "ingredient_count": len(core_ings),
                "image_name": recipe.get("image_name"),
                "source": "user",
            },
        )
        _qdrant.upsert(collection_name=COLLECTION_NAME, points=[point])
        logger.info(f"[RecipeQdrant] Indexed new recipe: {recipe['title']}")
    except Exception as e:
        logger.warning(f"[RecipeQdrant] Failed to index recipe: {e}")


def _on_recipe_added(event: EventMessage):
    """Handle tool.recipe_added events for future user-added recipes."""
    data = event.content if isinstance(event.content, dict) else {}
    title = data.get("title")
    if not title:
        return

    from plugins.recipes.recipe_api import recipes, clean_ingredients, ingredient_map

    # Build recipe dict with core ingredients
    raw_ingredients = data.get("ingredients", [])
    cleaned = clean_ingredients(raw_ingredients) if raw_ingredients else []
    core = [ingredient_map.get(c, c) for c in cleaned]

    recipe = {
        "title": title,
        "ingredients": raw_ingredients,
        "directions": data.get("directions", []),
        "cleaned_ingredients": cleaned,
        "core_ingredients": core,
        "image_name": data.get("image_name"),
    }

    recipe_idx = len(recipes)  # next index
    embed_single_recipe(recipe_idx, recipe)


def start_background():
    """Start background initialization and collection building."""
    config = GladosConfig.from_yaml("glados_config.yml")
    if not getattr(config, "recipe_qdrant_enabled", False):
        logger.info("[RecipeQdrant] Disabled via config (recipe_qdrant_enabled: false)")
        return

    def _bg():
        if not _init():
            return
        ensure_collection()

    threading.Thread(target=_bg, daemon=True, name="recipe-qdrant-init").start()

    # Subscribe to future recipe additions
    event_system = EventSystem()
    event_system.subscribe(
        "tool.recipe_added",
        EventHook("recipe_qdrant_indexer", callback=_on_recipe_added, priority=10),
    )
