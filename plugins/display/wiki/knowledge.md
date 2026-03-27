# Knowledge Base (RAG with Qdrant)

GlaDOS can retrieve factual knowledge from a Qdrant vector store using RAG (Retrieval Augmented Generation). When you ask a question, relevant passages are fetched from the knowledge base and injected into the LLM context so it can answer with accurate, sourced information.

## Two retrieval modes

1. **Passive RAG** (automatic): every request is searched against Qdrant, relevant passages injected as `<knowledge>` context before the LLM runs
2. **Active `lookup_knowledge` tool** (LLM-initiated): the LLM can explicitly search for more detail. Used when passive RAG context isn't enough or the user asks "tell me more about..."

## How It Works

```
User: "What is the speed of light?"
  │
  ├─ Memory hook (priority 10) — checks conversational memory
  ├─ Conversation RAG hook (priority 12) — retrieves relevant prior exchanges
  ├─ Knowledge RAG hook (priority 15) — searches Qdrant for relevant passages
  │     → Finds: "Speed of light: The speed of light in vacuum is 299,792,458 m/s..."
  │     → Injects passage as <knowledge> into LLM context
  ├─ Hybrid NLP (priority 20) — no tool match, falls through
  └─ LLM generates response using the injected knowledge passage
      │
      └─ (Optional) LLM calls lookup_knowledge("speed of light applications")
         → Gets more detailed passages → responds with additional info
```
```

The knowledge base runs alongside ChromaDB memory — they serve different purposes:

| | ChromaDB (Memory) | Qdrant (Knowledge) |
|---|---|---|
| **Content** | Past conversations, user preferences | Wikipedia, manuals, reference docs |
| **Storage** | Automatic or explicit ("remember that...") | Offline ingestion via CLI tool |
| **Query** | Every request (auto-retrieval) | Every request (similarity search) |
| **Scale** | Hundreds of entries | Millions of passages |

## Prerequisites

### 1. Install dependencies

```bash
pip install qdrant-client libzim sentence-transformers beautifulsoup4
```

### 2. Start Qdrant

Using Docker (recommended for persistent storage):

```bash
docker run -d --name qdrant \
  -p 6333:6333 \
  -v ~/data/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

Or for testing (data lost on restart):

```bash
docker run -p 6333:6333 qdrant/qdrant
```

Verify it's running: `curl http://localhost:6333/collections`

## Ingesting Content

### ZIM Files (Kiwix)

ZIM files are offline snapshots of websites — Wikipedia, StackOverflow, Wiktionary, etc. Download them from [Kiwix](https://download.kiwix.org/zim/).

**Recommended starting point:** Simple English Wikipedia (~1 GB download, ~200K articles)

```bash
# Ingest the full ZIM file
python tools/ingest_zim.py --zim ~/data/wikipedia_en_simple.zim --collection wikipedia

# Or start with a small test (first 100 articles)
python tools/ingest_zim.py --zim ~/data/wikipedia_en_simple.zim --collection wikipedia --limit 100

# Recreate collection from scratch
python tools/ingest_zim.py --zim ~/data/wikipedia_en_simple.zim --collection wikipedia --recreate
```

### Ingestion options

| Flag | Default | Description |
|------|---------|-------------|
| `--zim` | (required) | Path to ZIM file |
| `--collection` | (required) | Qdrant collection name |
| `--qdrant-url` | `http://localhost:6333` | Qdrant server URL |
| `--model` | `all-MiniLM-L6-v2` | Sentence transformer model for embeddings |
| `--limit` | 0 (all) | Max articles to process |
| `--batch-size` | 200 | Vectors per upsert batch |
| `--max-tokens` | 500 | Words per chunk |
| `--recreate` | false | Delete and recreate the collection |

### Ingestion estimates

| ZIM file | Download | Articles | Chunks | Qdrant storage | Ingest time* |
|----------|----------|----------|--------|-----------------|-------------|
| Wikipedia Simple English | ~1 GB | ~200K | ~1M | ~2 GB | ~1 hour |
| Wikipedia Full English | ~90 GB | ~6.7M | ~50M | ~100 GB | ~days |
| StackOverflow | ~10 GB | ~20M Q&A | ~40M | ~80 GB | ~days |

*Depends on CPU/GPU for embedding generation. GPU with `sentence-transformers` is significantly faster.

### Multiple collections

You can ingest different sources into separate collections and search them all:

```bash
python tools/ingest_zim.py --zim ~/data/wikipedia_en_simple.zim --collection wikipedia
python tools/ingest_zim.py --zim ~/data/stackoverflow.zim --collection stackoverflow
```

```yaml
knowledge_collections:
  - wikipedia
  - stackoverflow
```

## Configuration

Add to `glados_config.yml`:

```yaml
  knowledge_enabled: true
  qdrant_url: "http://localhost:6333"
  knowledge_collections:
    - wikipedia
  knowledge_top_k: 3              # passages to retrieve per query
  knowledge_threshold: 0.5        # minimum similarity score (0.0-1.0)
  knowledge_embed_model: "all-MiniLM-L6-v2"
```

### Tuning

- **`knowledge_threshold`**: Higher = fewer but more relevant results. Lower = more results but more noise. Start at 0.5 and adjust.
  - `0.3` — very permissive, may inject irrelevant passages
  - `0.5` — balanced default
  - `0.7` — strict, only very relevant matches
- **`knowledge_top_k`**: More passages = more context for the LLM but uses more tokens. 3 is a good default.
- **`knowledge_embed_model`**: `all-MiniLM-L6-v2` is small and fast. For better quality, try `nomic-embed-text-v1.5` or `BAAI/bge-small-en-v1.5`.

## How context injection works

When the RAG hook finds relevant passages, they're injected as a system message:

```
[Knowledge base — relevant passages]
Source: wikipedia — Paris (relevance: 85%)
Paris: Paris is the capital and largest city of France, situated on the river Seine...

Source: wikipedia — France (relevance: 72%)
France: France is a country located in Western Europe. Its capital is Paris...

[End of knowledge passages]
```

The LLM sees this alongside the user's question and can use the passages to give an accurate, sourced answer. If no passages meet the threshold, nothing is injected and the LLM answers from its own training data.

## Troubleshooting

- **"Cannot connect to Qdrant"**: Make sure Docker is running and port 6333 is accessible
- **No results for queries**: Check `knowledge_threshold` (try lowering to 0.3) and verify the collection has data: `curl http://localhost:6333/collections/wikipedia`
- **Slow embedding**: The embedding model downloads on first use (~90MB). Use GPU if available for faster encoding.
- **RAG interfering with tools**: The hook skips inputs shorter than 10 characters and only runs if the request hasn't already been handled by memory or NLP fast-path
