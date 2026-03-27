"""Ingest ZIM files (Kiwix offline content) into Qdrant vector store.

Usage:
    python tools/ingest_zim.py --zim ~/data/wikipedia_en_simple.zim --collection wikipedia
    python tools/ingest_zim.py --zim ~/data/wikipedia_en_simple.zim --collection wikipedia --limit 1000
    python tools/ingest_zim.py --zim ~/data/stackoverflow.zim --collection stackoverflow --batch-size 500

Requirements:
    pip install libzim qdrant-client sentence-transformers beautifulsoup4
"""
import argparse
import re
import sys
import time
import uuid
from typing import Iterator

from loguru import logger


def extract_articles(zim_path: str, limit: int = 0) -> Iterator[dict]:
    """Read articles from a ZIM file, yielding {title, text, url} dicts."""
    from libzim.reader import Archive

    archive = Archive(zim_path)
    count = 0

    for i in range(archive.entry_count):
        if limit and count >= limit:
            break

        entry = archive._get_entry_by_id(i)

        # Skip non-article entries (metadata, redirects, images)
        if entry.is_redirect:
            continue

        item = entry.get_item()
        mimetype = item.mimetype

        if "text/html" not in mimetype:
            continue

        try:
            html = bytes(item.content).decode("utf-8", errors="ignore")
        except Exception:
            continue

        # Strip HTML tags
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(html, "html.parser")

        # Remove script/style/nav elements
        for tag in soup(["script", "style", "nav", "header", "footer", "aside"]):
            tag.decompose()

        text = soup.get_text(separator=" ", strip=True)

        # Skip very short articles
        if len(text) < 100:
            continue

        # Clean up whitespace
        text = re.sub(r"\s+", " ", text).strip()

        title = entry.title or f"Article_{i}"
        path = entry.path

        yield {"title": title, "text": text, "url": path}
        count += 1

    logger.info(f"Extracted {count} articles from {zim_path}")


def chunk_text(text: str, title: str, max_tokens: int = 500, overlap_tokens: int = 50) -> list[dict]:
    """Split text into overlapping chunks of ~max_tokens words."""
    words = text.split()
    chunks = []
    start = 0

    while start < len(words):
        end = min(start + max_tokens, len(words))
        chunk_words = words[start:end]
        chunk_text = " ".join(chunk_words)

        # Prepend title for context
        chunk_with_title = f"{title}: {chunk_text}"

        chunks.append({
            "text": chunk_with_title,
            "chunk_index": len(chunks),
        })

        if end >= len(words):
            break
        start = end - overlap_tokens

    return chunks


def main():
    parser = argparse.ArgumentParser(description="Ingest ZIM files into Qdrant")
    parser.add_argument("--zim", required=True, help="Path to ZIM file")
    parser.add_argument("--collection", required=True, help="Qdrant collection name")
    parser.add_argument("--qdrant-url", default="http://localhost:6333", help="Qdrant server URL")
    parser.add_argument("--model", default="all-MiniLM-L6-v2", help="Sentence transformer model")
    parser.add_argument("--limit", type=int, default=0, help="Max articles to process (0=all)")
    parser.add_argument("--batch-size", type=int, default=200, help="Vectors per upsert batch")
    parser.add_argument("--max-tokens", type=int, default=500, help="Tokens per chunk")
    parser.add_argument("--recreate", action="store_true", help="Delete and recreate the collection")
    args = parser.parse_args()

    # Connect to Qdrant
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct

    client = QdrantClient(url=args.qdrant_url)
    logger.info(f"Connected to Qdrant at {args.qdrant_url}")

    # Load embedding model
    from sentence_transformers import SentenceTransformer
    model = SentenceTransformer(args.model)
    vector_size = model.get_sentence_embedding_dimension()
    logger.info(f"Loaded model {args.model} (dim={vector_size})")

    # Create or recreate collection
    collections = [c.name for c in client.get_collections().collections]
    if args.recreate and args.collection in collections:
        client.delete_collection(args.collection)
        logger.info(f"Deleted existing collection '{args.collection}'")
        collections.remove(args.collection)

    if args.collection not in collections:
        client.create_collection(
            collection_name=args.collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        logger.info(f"Created collection '{args.collection}' (dim={vector_size})")
    else:
        info = client.get_collection(args.collection)
        logger.info(f"Using existing collection '{args.collection}' ({info.points_count} points)")

    # Process ZIM file
    t_start = time.time()
    total_articles = 0
    total_chunks = 0
    batch_points = []
    source_name = args.zim.split("/")[-1].replace(".zim", "")

    for article in extract_articles(args.zim, limit=args.limit):
        total_articles += 1
        chunks = chunk_text(article["text"], article["title"], max_tokens=args.max_tokens)

        for chunk in chunks:
            total_chunks += 1
            vector = model.encode(chunk["text"]).tolist()

            batch_points.append(PointStruct(
                id=str(uuid.uuid4()),
                vector=vector,
                payload={
                    "text": chunk["text"],
                    "article_title": article["title"],
                    "source": source_name,
                    "url": article["url"],
                    "chunk_index": chunk["chunk_index"],
                },
            ))

            if len(batch_points) >= args.batch_size:
                client.upsert(collection_name=args.collection, points=batch_points)
                elapsed = time.time() - t_start
                rate = total_chunks / elapsed if elapsed > 0 else 0
                logger.info(
                    f"Upserted {total_chunks} chunks from {total_articles} articles "
                    f"({rate:.0f} chunks/sec)"
                )
                batch_points = []

    # Flush remaining
    if batch_points:
        client.upsert(collection_name=args.collection, points=batch_points)

    elapsed = time.time() - t_start
    info = client.get_collection(args.collection)
    logger.success(
        f"Done! {total_chunks} chunks from {total_articles} articles "
        f"in {elapsed:.1f}s. Collection '{args.collection}' now has {info.points_count} points."
    )


if __name__ == "__main__":
    main()
