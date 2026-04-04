"""Index GlaDOS wiki docs into Qdrant for self-referential RAG queries.

Scans docs/wiki/*.md, chunks by ## sections, embeds into a `glados_manual`
Qdrant collection. Uses file content hashes to detect changes — only
rebuilds when docs have been modified.

Called from KnowledgeRAG background init. Can also be run standalone:
    python -m plugins.knowledge.wiki_indexer
"""
import glob
import hashlib
import os
import re
import threading
import uuid

from loguru import logger

WIKI_DIR = "docs/wiki"
COLLECTION_NAME = "glados_manual"

# Reuse the shared embedding lock from rag.py
try:
    from plugins.knowledge.rag import _embed_lock
except ImportError:
    _embed_lock = threading.Lock()


def _hash_files(wiki_dir: str) -> str:
    """Compute a combined hash of all wiki .md files (content-based)."""
    h = hashlib.md5()
    for path in sorted(glob.glob(os.path.join(wiki_dir, "*.md"))):
        with open(path, "r", encoding="utf-8") as f:
            h.update(f.read().encode())
    return h.hexdigest()


def _chunk_markdown(filepath: str) -> list[dict]:
    """Split a markdown file into sections by ## headings."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()

    filename = os.path.basename(filepath)
    page_title = ""

    # Extract page title from first # heading
    title_match = re.match(r"^#\s+(.+)", content)
    if title_match:
        page_title = title_match.group(1).strip()

    # Split on ## headings
    sections = re.split(r"\n(?=## )", content)
    chunks = []

    for section in sections:
        section = section.strip()
        if not section or len(section) < 20:
            continue

        # Extract section heading
        heading_match = re.match(r"^##\s+(.+)", section)
        section_title = heading_match.group(1).strip() if heading_match else page_title

        # Clean markdown for speech-friendly storage
        text = section
        text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)  # [text](url) → text
        text = re.sub(r"```[\s\S]*?```", "", text)             # Remove code blocks
        text = re.sub(r"`([^`]+)`", r"\1", text)               # `code` → code
        text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)         # **bold** → bold
        text = re.sub(r"\*([^*]+)\*", r"\1", text)             # *italic* → italic
        text = re.sub(r"^#+\s+", "", text, flags=re.MULTILINE) # Strip heading markers
        text = re.sub(r"^\|.*\|$", "", text, flags=re.MULTILINE)  # Remove table rows
        text = re.sub(r"^[-|:]+$", "", text, flags=re.MULTILINE)  # Remove table separators
        text = re.sub(r"^[-*]\s+", "- ", text, flags=re.MULTILINE)  # Normalize list markers
        text = re.sub(r"\n{3,}", "\n\n", text)                 # Collapse blank lines

        chunks.append({
            "text": text,
            "page_title": page_title,
            "section_title": section_title,
            "filename": filename,
        })

    return chunks


def _get_stored_hash(qdrant_client, collection_name: str) -> str:
    """Read the stored content hash from collection metadata point."""
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        results = qdrant_client.scroll(
            collection_name=collection_name,
            scroll_filter=Filter(must=[
                FieldCondition(key="source", match=MatchValue(value="__meta__"))
            ]),
            limit=1,
        )
        points = results[0]
        if points:
            return points[0].payload.get("content_hash", "")
    except Exception:
        pass
    return ""


def ensure_wiki_indexed(qdrant_client, embed_model, wiki_dir: str = WIKI_DIR):
    """Check if wiki docs have changed and re-index if needed.

    Args:
        qdrant_client: Connected QdrantClient instance
        embed_model: Loaded SentenceTransformer model
        wiki_dir: Path to wiki markdown files
    """
    if not os.path.isdir(wiki_dir):
        logger.debug(f"[WikiIndexer] Wiki dir not found: {wiki_dir}")
        return

    md_files = sorted(glob.glob(os.path.join(wiki_dir, "*.md")))
    if not md_files:
        logger.debug("[WikiIndexer] No markdown files found in wiki dir")
        return

    current_hash = _hash_files(wiki_dir)

    # Check if collection exists and is up-to-date
    collection_exists = False
    try:
        qdrant_client.get_collection(COLLECTION_NAME)
        collection_exists = True
    except Exception:
        pass

    if collection_exists:
        stored_hash = _get_stored_hash(qdrant_client, COLLECTION_NAME)
        if stored_hash == current_hash:
            logger.info(f"[WikiIndexer] Collection '{COLLECTION_NAME}' is up-to-date")
            return
        logger.info(f"[WikiIndexer] Wiki docs changed — rebuilding collection")
    else:
        logger.info(f"[WikiIndexer] Creating collection '{COLLECTION_NAME}'")

    _build_collection(qdrant_client, embed_model, md_files, current_hash)


def _build_collection(qdrant_client, embed_model, md_files: list[str], content_hash: str):
    """Rebuild the glados_manual collection from wiki markdown files."""
    from qdrant_client.models import Distance, VectorParams, PointStruct

    # Get vector dimension
    with _embed_lock:
        dim = embed_model.get_sentence_embedding_dimension()

    # Recreate collection
    try:
        qdrant_client.delete_collection(COLLECTION_NAME)
    except Exception:
        pass
    qdrant_client.create_collection(
        collection_name=COLLECTION_NAME,
        vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
    )

    # Chunk all files
    all_chunks = []
    for filepath in md_files:
        chunks = _chunk_markdown(filepath)
        all_chunks.extend(chunks)

    if not all_chunks:
        logger.warning("[WikiIndexer] No content chunks extracted from wiki")
        return

    # Batch embed and upsert
    points = []
    for chunk in all_chunks:
        with _embed_lock:
            vector = embed_model.encode(chunk["text"]).tolist()
        points.append(PointStruct(
            id=str(uuid.uuid4()),
            vector=vector,
            payload={
                "text": chunk["text"],
                "article_title": f"{chunk['page_title']} — {chunk['section_title']}",
                "source": COLLECTION_NAME,
                "filename": chunk["filename"],
                "page_title": chunk["page_title"],
                "section_title": chunk["section_title"],
            },
        ))

    # Upsert in batches
    batch_size = 50
    for i in range(0, len(points), batch_size):
        qdrant_client.upsert(
            collection_name=COLLECTION_NAME,
            points=points[i:i + batch_size],
        )

    # Store metadata point with content hash
    meta_point = PointStruct(
        id=str(uuid.uuid4()),
        vector=[0.0] * dim,
        payload={
            "source": "__meta__",
            "content_hash": content_hash,
            "file_count": len(md_files),
            "chunk_count": len(all_chunks),
        },
    )
    qdrant_client.upsert(collection_name=COLLECTION_NAME, points=[meta_point])

    logger.success(f"[WikiIndexer] Indexed {len(all_chunks)} sections from {len(md_files)} wiki pages")


# Standalone runner
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

    from loguru import logger as _logger
    _logger.remove()
    _logger.add(sys.stderr, level="INFO")

    from qdrant_client import QdrantClient
    from sentence_transformers import SentenceTransformer

    url = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:6333"
    print(f"Connecting to Qdrant at {url}...")
    client = QdrantClient(url=url, timeout=5)
    model = SentenceTransformer("all-MiniLM-L6-v2")

    ensure_wiki_indexed(client, model)
