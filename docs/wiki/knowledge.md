# Knowledge Base

GlaDOS can answer factual questions using an offline knowledge base (Wikipedia, StackOverflow, etc.) stored in Qdrant. When you ask something, relevant passages are automatically retrieved and used to give accurate answers.

## Using It

Just ask questions naturally — knowledge retrieval is automatic:

| Say this | What happens |
|----------|-------------|
| "What is the speed of light?" | Finds Wikipedia passage, answers with sourced facts |
| "Tell me about the Eiffel Tower" | Retrieves article, summarizes key info |
| "Who invented the telephone?" | Searches knowledge base, answers from Wikipedia |
| "Tell me more about that" | Follow-up questions work — context from prior exchange is used |

**What you'll see in the chat panel:**
- A green "Knowledge: article name" collapsible bubble showing which sources were used
- A citation badge on the assistant's response with source names and relevance scores
- If no knowledge was found: "answered from model — no knowledge base sources"

**When knowledge is NOT searched** (for speed):
- Tool commands like "play some jazz", "set a timer", "add eggs to the list" skip the knowledge search automatically
- Very short inputs (< 10 characters) like "hi" or "thanks"

## Memory vs Knowledge

| | Memory | Knowledge |
|---|---|---|
| **What it stores** | Your conversations, preferences ("remember that I like metric") | Wikipedia articles, reference docs |
| **How it's added** | Automatic from conversations, or "remember that..." | Offline ingestion (see setup below) |
| **Scale** | Hundreds of entries | Millions of passages |

Both run automatically on every request — memory retrieves personal context, knowledge retrieves factual context.

---

## Setup & Configuration

### Prerequisites

```bash
pip install qdrant-client libzim sentence-transformers beautifulsoup4
```

Start Qdrant via Docker:

```bash
docker run -d --name qdrant \
  -p 6333:6333 \
  -v ~/data/qdrant_storage:/qdrant/storage \
  qdrant/qdrant
```

### Ingesting Content

ZIM files are offline snapshots of websites. Download from [Kiwix](https://download.kiwix.org/zim/).

```bash
# Recommended: Simple English Wikipedia (~1 GB, ~200K articles)
python tools/ingest_zim.py --zim ~/data/wikipedia_en_simple.zim --collection wikipedia

# Small test (first 100 articles)
python tools/ingest_zim.py --zim ~/data/wikipedia_en_simple.zim --collection wikipedia --limit 100
```

| Flag | Default | Description |
|------|---------|-------------|
| `--zim` | (required) | Path to ZIM file |
| `--collection` | (required) | Qdrant collection name |
| `--limit` | 0 (all) | Max articles to process |
| `--recreate` | false | Delete and recreate collection |

### Ingestion estimates

| Source | Download | Articles | Qdrant storage | Time |
|--------|----------|----------|-----------------|------|
| Wikipedia Simple English | ~1 GB | ~200K | ~2 GB | ~1 hour |
| Wikipedia Full English | ~90 GB | ~6.7M | ~100 GB | ~days |

### Configuration

```yaml
knowledge_enabled: true
qdrant_url: "http://localhost:6333"
knowledge_collections:
  - wikipedia
knowledge_top_k: 3              # passages per query
knowledge_threshold: 0.5        # similarity score (0.0-1.0)
knowledge_query_mode: "context"  # "raw", "context", or "rewrite"
```

### Query Modes

| Mode | How it works | Quality |
|------|-------------|---------|
| `raw` | Uses your words as-is | Good for direct questions |
| `context` (default) | Includes prior conversation for context | Handles follow-ups well |
| `rewrite` | Uses a small LLM to reformulate the query | Best retrieval quality, adds ~500ms |

### Tuning

- **`knowledge_threshold`**: 0.3 = permissive (more noise), 0.5 = balanced, 0.7 = strict
- **`knowledge_top_k`**: More passages = more context but more tokens. 3 is good.
- **`knowledge_query_mode`**: Use `context` unless you have a fast local model, then try `rewrite`

### Troubleshooting

- **No results**: Lower `knowledge_threshold` to 0.3, verify collection has data
- **Slow first query**: Embedding model downloads on first use (~90MB)
- **Not ready yet**: Normal during first few seconds — background init in progress
- **Qdrant unavailable**: RAG silently skips — GlaDOS still works, just without knowledge context
