"""Ingest a Kiwix Gutenberg ZIM file into the ebooks Qdrant collection.

Unlike ``tools/ingest_zim.py`` (which produces chunk-level vectors for
wiki knowledge retrieval), this tool produces ONE vector per BOOK —
title + author + LCC + subjects + first 300 words — suitable for
genre/theme search like "hard sci-fi" or "fantasy with dragons".

Writes both:
  - Qdrant collection (default ``ebooks``) with one point per book
  - ``plugin_data/ebook_reader/catalog.json`` (browsable metadata cache)

Usage:
    python tools/ingest_ebooks_qdrant.py --zim data/gutenberg_en_lcc-r_2026-03.zim
    python tools/ingest_ebooks_qdrant.py --zim data/gutenberg_en_lcc-pr_2026-03.zim --limit 100
    python tools/ingest_ebooks_qdrant.py --zim ... --dry-run --limit 20
    python tools/ingest_ebooks_qdrant.py --zim ... --recreate

Each invocation processes one ZIM. Re-running against the same or a
different ZIM appends idempotently (deterministic UUIDv5 point IDs
keyed on the Gutenberg ID), so the catalog + collection accumulate
across runs.

Requirements:
    pip install libzim qdrant-client sentence-transformers beautifulsoup4
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator, Optional

from loguru import logger

EBOOK_NAMESPACE = uuid.UUID("6b3d0c3c-9a3f-4a4b-9b5f-2f8e7d1c2b3a")
COLLECTION_DEFAULT = "ebooks"
MODEL_DEFAULT = "all-MiniLM-L6-v2"
LCC_RE = re.compile(r"gutenberg_en_lcc-([a-z]+)_")
_YEAR_PART_RE = re.compile(r"^\d{3,4}(-\d{0,4})?\??$")


def _clean_creator(raw: str) -> str:
    """'Jeaffreson, John Cordy, 1831-1901' -> 'John Cordy Jeaffreson'.

    Falls back to the raw string for unparseable formats (e.g. 'pseud.
    Aristotle', 'Anonymous', single-name creators).
    """
    if not raw:
        return "Unknown"
    parts = [p.strip() for p in raw.split(",")]
    parts = [p for p in parts if p and not _YEAR_PART_RE.match(p)]
    if len(parts) >= 2:
        # "Lastname" + "Firstname [Middle]" -> "Firstname [Middle] Lastname"
        return f"{parts[1]} {parts[0]}".strip()
    return parts[0] if parts else raw.strip()


def parse_book(html: str, gutenberg_id: str, zim_filename: str,
               entry_path: str, lcc: str) -> Optional[dict]:
    """Parse a Gutenberg book HTML entry into a catalog record.

    Returns ``None`` for entries that don't look like real books (too
    short, parse failure, missing essential metadata).
    """
    from bs4 import BeautifulSoup

    try:
        soup = BeautifulSoup(html, "html.parser")
    except Exception as e:
        logger.debug(f"[parse_book] BS4 failed on {gutenberg_id}: {e}")
        return None

    def _content_str(tag) -> str:
        """Robustly extract a meta tag's content as a single clean string.

        - bs4 can return list[str] for multi-valued attrs in some parsers
        - Gutenberg meta content occasionally contains raw HTML entities
          like ``&#10;`` (newline) that we decode and then collapse.
        """
        import html
        if tag is None:
            return ""
        c = tag.get("content")
        if c is None:
            return ""
        if isinstance(c, list):
            c = " ".join(str(x) for x in c)
        c = html.unescape(str(c))
        return re.sub(r"\s+", " ", c).strip()

    def meta(name: str) -> str:
        return _content_str(soup.find("meta", attrs={"name": name}))

    def meta_all(name: str) -> list[str]:
        out = []
        for t in soup.find_all("meta", attrs={"name": name}):
            v = _content_str(t)
            if v:
                out.append(v)
        return out

    title = meta("dc.title")
    if not title:
        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else entry_path
    raw_creator = meta("dc.creator")
    author = _clean_creator(raw_creator)
    subjects = meta_all("dc.subject")
    language = meta("dc.language") or "en"
    source_url = meta("dcterms.source")

    # Strip script/style/header/footer for body text extraction
    for tag in soup.find_all(["script", "style"]):
        tag.decompose()
    for tag_id in ("pg-header", "pg-footer"):
        el = soup.find(id=tag_id)
        if el:
            el.decompose()

    body_text = soup.get_text(" ", strip=True)
    body_text = re.sub(r"\s+", " ", body_text).strip()
    if len(body_text) < 500:
        return None

    words = body_text.split()
    word_count = len(words)
    first_300_words = " ".join(words[:300])

    # Chapter count by <h2> tag count
    chapter_count = len(soup.find_all("h2"))

    return {
        "book_id": f"gutenberg-{gutenberg_id}",
        "gutenberg_id": gutenberg_id,
        "title": title,
        "author": author,
        "raw_creator": raw_creator,
        "subjects": subjects,
        "language": language,
        "lcc": lcc,
        "zim_filename": zim_filename,
        "zim_entry_path": entry_path,
        "source_url": source_url,
        "word_count": word_count,
        "chapter_count": chapter_count,
        "first_300_words": first_300_words,
    }


def iter_books(zim_path: str, limit: int = 0) -> Iterator[dict]:
    """Yield parsed book dicts from a Gutenberg ZIM archive."""
    from libzim.reader import Archive

    archive = Archive(zim_path)
    lcc_match = LCC_RE.search(zim_path)
    lcc = lcc_match.group(1).upper() if lcc_match else "UNKNOWN"
    zim_filename = Path(zim_path).name

    logger.info(f"[ingest] Scanning {zim_filename} (LCC {lcc}, {archive.entry_count} entries)")

    n = 0
    skipped_non_book = 0
    skipped_parse = 0

    for i in range(archive.entry_count):
        if limit and n >= limit:
            break
        try:
            entry = archive._get_entry_by_id(i)
        except Exception:
            continue

        if entry.is_redirect:
            continue

        try:
            item = entry.get_item()
        except Exception:
            continue

        mimetype = item.mimetype
        if "text/html" not in mimetype:
            continue

        path = entry.path
        # Book filter: path ends in a numeric Gutenberg ID and is not a cover page
        if "_cover." in path:
            skipped_non_book += 1
            continue
        tail = path.rsplit(".", 1)[-1] if "." in path else ""
        if not tail.isdigit():
            skipped_non_book += 1
            continue

        try:
            html = bytes(item.content).decode("utf-8", errors="ignore")
        except Exception:
            skipped_parse += 1
            continue

        book = parse_book(html, gutenberg_id=tail, zim_filename=zim_filename,
                          entry_path=path, lcc=lcc)
        if book is None:
            skipped_parse += 1
            continue

        yield book
        n += 1

    logger.info(f"[ingest] Scan done: {n} books, skipped {skipped_non_book} non-book "
                f"entries, {skipped_parse} parse/short failures")


def build_embedding_text(book: dict) -> str:
    subjects_str = "; ".join(book["subjects"]) if book["subjects"] else "None"
    return (
        f"{book['title']} by {book['author']}. "
        f"Subjects: {subjects_str}. "
        f"LCC: {book['lcc']}. "
        f"{book['first_300_words']}"
    )


def book_point_id(book_id: str) -> str:
    """Deterministic UUIDv5 so reruns overwrite idempotently."""
    return str(uuid.uuid5(EBOOK_NAMESPACE, f"ebook:{book_id}"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_catalog(path: Path) -> dict:
    if path.exists():
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "books" in data:
                return data
        except Exception as e:
            logger.warning(f"[ingest] Could not read existing catalog at {path}: {e}")
    return {"schema_version": 1, "updated_at": _now_iso(), "books": []}


def save_catalog_atomic(catalog: dict, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(catalog, f, indent=2, ensure_ascii=False)
    tmp.replace(path)


def _catalog_to_record(book: dict) -> dict:
    """Strip runtime-only fields from the parsed book for catalog storage."""
    return {
        "book_id": book["book_id"],
        "gutenberg_id": book["gutenberg_id"],
        "title": book["title"],
        "author": book["author"],
        "subjects": book["subjects"],
        "language": book["language"],
        "lcc": book["lcc"],
        "zim_filename": book["zim_filename"],
        "zim_entry_path": book["zim_entry_path"],
        "source_url": book["source_url"],
        "word_count": book["word_count"],
        "chapter_count": book["chapter_count"],
        "has_cover": False,  # Pass 1: covers deferred
    }


def _payload(book: dict) -> dict:
    return {
        "book_id": book["book_id"],
        "title": book["title"],
        "author": book["author"],
        "subjects": book["subjects"],
        "lcc": book["lcc"],
        "zim_filename": book["zim_filename"],
        "zim_entry_path": book["zim_entry_path"],
        "language": book["language"],
        "word_count": book["word_count"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Ingest a Gutenberg ZIM into the ebooks Qdrant collection")
    parser.add_argument("--zim", required=True, help="Path to a gutenberg_en_lcc-*.zim file")
    parser.add_argument("--qdrant-url", default="http://localhost:6333")
    parser.add_argument("--collection", default=COLLECTION_DEFAULT)
    parser.add_argument("--catalog", default="plugin_data/ebook_reader/catalog.json",
                        help="Path to write/update catalog.json")
    parser.add_argument("--model", default=MODEL_DEFAULT)
    parser.add_argument("--limit", type=int, default=0, help="Max books to process (0=all)")
    parser.add_argument("--batch-size", type=int, default=200)
    parser.add_argument("--recreate", action="store_true", help="Drop and recreate the collection first")
    parser.add_argument("--dry-run", action="store_true",
                        help="Parse and print metadata only — no Qdrant or catalog writes")
    parser.add_argument("--catalog-flush-every", type=int, default=100,
                        help="Write catalog.json to disk every N books")
    args = parser.parse_args()

    if not Path(args.zim).exists():
        logger.error(f"ZIM file not found: {args.zim}")
        return 2

    # Dry-run path: just parse and print, no external dependencies on Qdrant/embeddings.
    if args.dry_run:
        logger.info("[ingest] Dry-run — parsing only, no writes")
        for i, book in enumerate(iter_books(args.zim, limit=args.limit), start=1):
            subjects = "; ".join(book["subjects"]) or "(none)"
            logger.info(
                f"  [{i:4d}] {book['book_id']}  {book['title']!r} — {book['author']!r} "
                f"| LCC={book['lcc']} | {book['word_count']} words, {book['chapter_count']} chapters"
            )
            logger.debug(f"         subjects: {subjects}")
        return 0

    # Real run — needs Qdrant + sentence-transformers
    try:
        from qdrant_client import QdrantClient
        from qdrant_client.models import Distance, VectorParams, PointStruct
        from sentence_transformers import SentenceTransformer
    except ImportError as e:
        logger.error(f"Missing dependency: {e}. Install with `pip install qdrant-client sentence-transformers`")
        return 2

    try:
        client = QdrantClient(url=args.qdrant_url, timeout=10)
        client.get_collections()
    except Exception as e:
        logger.error(f"Cannot connect to Qdrant at {args.qdrant_url}: {e}")
        return 2
    logger.info(f"[ingest] Connected to Qdrant at {args.qdrant_url}")

    model = SentenceTransformer(args.model)
    vector_size = model.get_sentence_embedding_dimension()
    logger.info(f"[ingest] Loaded embedding model {args.model} (dim={vector_size})")

    existing = [c.name for c in client.get_collections().collections]
    if args.recreate and args.collection in existing:
        client.delete_collection(args.collection)
        logger.info(f"[ingest] Dropped existing collection '{args.collection}'")
        existing.remove(args.collection)
    if args.collection not in existing:
        client.create_collection(
            collection_name=args.collection,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        logger.info(f"[ingest] Created collection '{args.collection}' (dim={vector_size})")
    else:
        info = client.get_collection(args.collection)
        logger.info(f"[ingest] Using existing collection '{args.collection}' "
                    f"({info.points_count} points)")

    catalog_path = Path(args.catalog)
    catalog = load_catalog(catalog_path)
    known_book_ids = {b["book_id"] for b in catalog.get("books", [])}
    logger.info(f"[ingest] Loaded catalog with {len(known_book_ids)} existing books")

    zim_basename = Path(args.zim).name.replace(".zim", "")
    zims_ingested = set(catalog.get("zims_ingested", []))
    zims_ingested.add(zim_basename)

    t_start = time.time()
    batch: list[PointStruct] = []
    total_new = 0
    total_updated = 0
    total_seen = 0

    for book in iter_books(args.zim, limit=args.limit):
        total_seen += 1
        embed_text = build_embedding_text(book)
        vector = model.encode(embed_text).tolist()
        point_id = book_point_id(book["book_id"])

        batch.append(PointStruct(id=point_id, vector=vector, payload=_payload(book)))

        # Maintain catalog
        record = _catalog_to_record(book)
        if book["book_id"] in known_book_ids:
            for i, existing_entry in enumerate(catalog["books"]):
                if existing_entry["book_id"] == book["book_id"]:
                    catalog["books"][i] = record
                    break
            total_updated += 1
        else:
            catalog["books"].append(record)
            known_book_ids.add(book["book_id"])
            total_new += 1

        if len(batch) >= args.batch_size:
            client.upsert(collection_name=args.collection, points=batch)
            batch = []
            elapsed = time.time() - t_start
            rate = total_seen / elapsed if elapsed > 0 else 0
            logger.info(
                f"[ingest] Upserted {total_seen} books ({total_new} new, "
                f"{total_updated} updated) — {rate:.1f} books/sec"
            )

        if total_seen % args.catalog_flush_every == 0:
            catalog["updated_at"] = _now_iso()
            catalog["zims_ingested"] = sorted(zims_ingested)
            save_catalog_atomic(catalog, catalog_path)

    # Flush remaining points
    if batch:
        client.upsert(collection_name=args.collection, points=batch)

    # Final catalog write
    catalog["updated_at"] = _now_iso()
    catalog["zims_ingested"] = sorted(zims_ingested)
    save_catalog_atomic(catalog, catalog_path)

    # Write/refresh the metadata sentinel point
    meta_payload = {
        "is_meta": True,
        "schema_version": 1,
        "book_count": len(catalog["books"]),
        "zims_ingested": sorted(zims_ingested),
        "last_updated": catalog["updated_at"],
    }
    meta_point = PointStruct(
        id=str(uuid.uuid5(EBOOK_NAMESPACE, "ebook:__meta__")),
        vector=[0.0] * vector_size,
        payload=meta_payload,
    )
    client.upsert(collection_name=args.collection, points=[meta_point])

    elapsed = time.time() - t_start
    info = client.get_collection(args.collection)
    logger.success(
        f"[ingest] Done. Processed {total_seen} books ({total_new} new, "
        f"{total_updated} updated) in {elapsed:.1f}s. "
        f"Collection '{args.collection}' has {info.points_count} points. "
        f"Catalog at {catalog_path} has {len(catalog['books'])} books."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
