"""
Knowledge RAG query enhancement benchmark — tests raw, context, and rewrite modes.

Measures retrieval quality across different query modes and (for rewrite mode)
different LLM models. Requires a running Qdrant server with the wikipedia collection
and (for rewrite mode) a running LM Studio server.

Run:
    python tests/benchmark_knowledge.py                     # test all modes with default model
    python tests/benchmark_knowledge.py --mode rewrite      # test rewrite mode only
    python tests/benchmark_knowledge.py --models qwen2.5-0.5b-instruct qwen2.5-1.5b-instruct
    python tests/benchmark_knowledge.py --all-models        # cycle through all loaded LM Studio models
    python tests/benchmark_knowledge.py --threshold 0.45    # override similarity threshold
"""
import argparse
import json
import os
import sys
import time
from dataclasses import dataclass

import requests
from loguru import logger

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ---------------------------------------------------------------------------
# Test cases: (user_text, conversation_history, expected_article_keywords)
#
# conversation_history simulates recent messages in the context window.
# expected_article_keywords are words we expect in a GOOD retrieval result title/text.
# ---------------------------------------------------------------------------

TEST_CASES = [
    {
        "name": "Direct question — animal teeth",
        "user_text": "what animal has the most teeth",
        "history": [],
        "expected_keywords": ["teeth", "radula", "snail", "gastropod", "dental"],
    },
    {
        "name": "Follow-up — snail after mammal teeth discussion",
        "user_text": "I believe you forget the snail.",
        "history": [
            {"role": "user", "content": "what animal has the most teeth"},
            {"role": "assistant", "content": "The star-nosed mole has the most teeth of any mammal, with 44 teeth."},
        ],
        "expected_keywords": ["snail", "radula", "teeth", "gastropod"],
    },
    {
        "name": "Follow-up — not mammal, animal in general",
        "user_text": "no, not mammal, animal in general.",
        "history": [
            {"role": "user", "content": "what animal has the most teeth"},
            {"role": "assistant", "content": "The star-nosed mole has the most teeth of any mammal."},
        ],
        "expected_keywords": ["teeth", "animal", "radula", "snail"],
    },
    {
        "name": "Direct question — snail teeth count",
        "user_text": "how many teeth do snails have",
        "history": [],
        "expected_keywords": ["snail", "radula", "teeth", "gastropod"],
    },
    {
        "name": "Direct question — Eiffel Tower height",
        "user_text": "how tall is the eiffel tower",
        "history": [],
        "expected_keywords": ["eiffel", "tower", "paris", "metre", "meter", "iron"],
    },
    {
        "name": "Follow-up — who built it (Eiffel Tower context)",
        "user_text": "who built it",
        "history": [
            {"role": "user", "content": "how tall is the eiffel tower"},
            {"role": "assistant", "content": "The Eiffel Tower is 330 metres tall."},
        ],
        "expected_keywords": ["eiffel", "gustave", "engineer", "tower"],
    },
    {
        "name": "Follow-up — pronoun reference (Mars)",
        "user_text": "how far is it from earth",
        "history": [
            {"role": "user", "content": "tell me about Mars"},
            {"role": "assistant", "content": "Mars is the fourth planet from the Sun."},
        ],
        "expected_keywords": ["mars", "earth", "distance", "planet", "orbit"],
    },
    {
        "name": "Direct question — photosynthesis",
        "user_text": "how does photosynthesis work",
        "history": [],
        "expected_keywords": ["photosynthesis", "chlorophyll", "light", "plant", "carbon"],
    },
    {
        "name": "Conversational — that's interesting tell me more",
        "user_text": "that's interesting, tell me more",
        "history": [
            {"role": "user", "content": "what is a black hole"},
            {"role": "assistant", "content": "A black hole is a region of spacetime where gravity is so strong that nothing can escape."},
        ],
        "expected_keywords": ["black hole", "gravity", "event horizon", "spacetime"],
    },
    {
        "name": "Direct question — Nintendo DS",
        "user_text": "what are the specs of the Nintendo DS",
        "history": [],
        "expected_keywords": ["nintendo", "ds", "screen", "processor", "ARM"],
    },
]


@dataclass
class SearchResult:
    title: str
    score: float
    text_preview: str


@dataclass
class TestResult:
    case_name: str
    mode: str
    model: str
    search_query: str
    results: list  # list of SearchResult
    latency_ms: float
    hit: bool  # did any result match expected keywords?


# ---------------------------------------------------------------------------
# Qdrant search
# ---------------------------------------------------------------------------

def embed_and_search(query: str, embed_model, qdrant_client, collection: str,
                     top_k: int = 5, threshold: float = 0.45) -> list[SearchResult]:
    """Embed a query and search Qdrant, returning results."""
    import threading
    vector = embed_model.encode(query).tolist()

    response = qdrant_client.query_points(
        collection_name=collection,
        query=vector,
        limit=top_k,
        score_threshold=threshold,
    )
    hits = response.points if hasattr(response, 'points') else []
    results = []
    for hit in hits:
        payload = hit.payload or {}
        results.append(SearchResult(
            title=payload.get("article_title", "Unknown"),
            score=hit.score,
            text_preview=payload.get("text", "")[:150],
        ))
    return results


# ---------------------------------------------------------------------------
# Query builders (mirrors rag.py logic)
# ---------------------------------------------------------------------------

def build_raw_query(user_text: str, history: list[dict]) -> str:
    return user_text


def build_context_query(user_text: str, history: list[dict]) -> str:
    if not history:
        return user_text
    recent = []
    for msg in reversed(history):
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            recent.append(msg["content"][:100])
            if len(recent) >= 2:
                break
    if recent:
        context = " | ".join(reversed(recent))
        return f"{context} | {user_text}"
    return user_text


def build_rewrite_query(user_text: str, history: list[dict], client, model: str) -> str:
    recent_context = ""
    parts = []
    for msg in reversed(history):
        if msg.get("role") in ("user", "assistant") and msg.get("content"):
            parts.insert(0, f"{msg['role']}: {msg['content'][:150]}")
            if len("\n".join(parts)) > 400:
                break
    recent_context = "\n".join(parts)

    messages = [
        {"role": "system", "content": (
            "Rewrite the user's message into a concise Wikipedia search query. "
            "Consider the conversation context. Output ONLY the search query, nothing else."
        )},
        {"role": "user", "content": f"Conversation:\n{recent_context}\nCurrent message: {user_text}"},
    ]

    response = client.chat.completions.create(
        model=model,
        messages=messages,
        max_tokens=50,
        temperature=0.0,
    )
    return response.choices[0].message.content.strip()


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def check_hit(results: list[SearchResult], expected_keywords: list[str]) -> bool:
    """Check if any result contains at least one expected keyword."""
    for r in results:
        combined = f"{r.title} {r.text_preview}".lower()
        for kw in expected_keywords:
            if kw.lower() in combined:
                return True
    return False


# ---------------------------------------------------------------------------
# Main benchmark
# ---------------------------------------------------------------------------

def run_benchmark(args):
    from qdrant_client import QdrantClient
    from sentence_transformers import SentenceTransformer

    qdrant_url = args.qdrant_url
    collection = args.collection
    threshold = args.threshold
    top_k = args.top_k

    logger.info(f"Connecting to Qdrant at {qdrant_url}...")
    qdrant = QdrantClient(url=qdrant_url, timeout=10)
    info = qdrant.get_collection(collection)
    logger.info(f"Collection '{collection}': {info.points_count} points")

    logger.info(f"Loading embedding model: {args.embed_model}...")
    embed_model = SentenceTransformer(args.embed_model)

    # Determine which modes to test
    modes = args.modes.split(",") if args.modes else ["raw", "context", "rewrite"]

    # For rewrite mode, determine models
    rewrite_models = []
    if "rewrite" in modes:
        lm_url = args.lm_url
        if args.all_models:
            resp = requests.get(f"{lm_url}/v1/models")
            rewrite_models = [m["id"] for m in resp.json().get("data", [])]
            logger.info(f"Found {len(rewrite_models)} models in LM Studio: {rewrite_models}")
        elif args.models:
            rewrite_models = args.models
        else:
            # Use currently loaded model
            try:
                resp = requests.get(f"{lm_url}/v1/models")
                loaded = [m["id"] for m in resp.json().get("data", []) if m.get("id")]
                rewrite_models = loaded[:1] if loaded else ["default"]
            except Exception:
                rewrite_models = ["default"]

    from openai import OpenAI
    lm_client = OpenAI(base_url=args.lm_url, api_key="not-needed") if rewrite_models else None

    all_results: list[TestResult] = []

    # Run tests — group by model to minimize LM Studio reloads.
    # Non-rewrite modes (raw, context) run first with model="n/a",
    # then each rewrite model runs all cases before moving to the next.
    non_rewrite_modes = [m for m in modes if m != "rewrite"]
    run_order = [(m, "n/a") for m in non_rewrite_modes]
    if "rewrite" in modes:
        run_order += [("rewrite", model) for model in rewrite_models]

    for mode, model in run_order:
        if mode == "rewrite":
            logger.info(f"\n{'='*80}\nLoading model: {model}\n{'='*80}")
        for case in TEST_CASES:
            t_start = time.perf_counter()

            try:
                if mode == "raw":
                    query = build_raw_query(case["user_text"], case["history"])
                elif mode == "context":
                    query = build_context_query(case["user_text"], case["history"])
                elif mode == "rewrite":
                    query = build_rewrite_query(case["user_text"], case["history"], lm_client, model)
                else:
                    continue

                results = embed_and_search(query, embed_model, qdrant, collection, top_k, threshold)
                latency = (time.perf_counter() - t_start) * 1000
                hit = check_hit(results, case["expected_keywords"])

                tr = TestResult(
                    case_name=case["name"],
                    mode=mode,
                    model=model,
                    search_query=query[:120],
                    results=[{"title": r.title, "score": round(r.score, 3)} for r in results],
                    latency_ms=round(latency, 1),
                    hit=hit,
                )
                all_results.append(tr)

                status = "\033[92mHIT\033[0m" if hit else "\033[91mMISS\033[0m"
                top_title = results[0].title if results else "no results"
                top_score = f"{results[0].score:.2f}" if results else "n/a"
                logger.info(
                    f"[{status}] {mode:8s} | {model:30s} | {case['name']:50s} | "
                    f"query: {query[:60]:60s} | top: {top_title[:40]} ({top_score}) | {latency:.0f}ms"
                )

            except Exception as e:
                logger.error(f"[ERR] {mode:8s} | {model:30s} | {case['name']}: {e}")

    # ---------------------------------------------------------------------------
    # Summary
    # ---------------------------------------------------------------------------
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)

    # Group by mode+model
    from collections import defaultdict
    groups = defaultdict(lambda: {"hits": 0, "total": 0, "latency": []})
    for tr in all_results:
        key = f"{tr.mode}/{tr.model}"
        groups[key]["hits"] += 1 if tr.hit else 0
        groups[key]["total"] += 1
        groups[key]["latency"].append(tr.latency_ms)

    print(f"\n{'Mode/Model':<45s} {'Hits':>6s} {'Total':>6s} {'Rate':>8s} {'Avg ms':>8s} {'P95 ms':>8s}")
    print("-" * 85)
    for key, data in sorted(groups.items()):
        rate = data["hits"] / data["total"] * 100 if data["total"] else 0
        avg_ms = sum(data["latency"]) / len(data["latency"])
        sorted_lat = sorted(data["latency"])
        p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if sorted_lat else 0
        print(f"{key:<45s} {data['hits']:>6d} {data['total']:>6d} {rate:>7.1f}% {avg_ms:>7.0f} {p95:>7.0f}")

    # Per-case breakdown for misses
    misses = [tr for tr in all_results if not tr.hit]
    if misses:
        print(f"\nMISSES ({len(misses)}):")
        for tr in misses:
            print(f"  [{tr.mode}/{tr.model}] {tr.case_name}")
            print(f"    query: {tr.search_query}")
            print(f"    results: {tr.results[:3]}")

    # Save results
    os.makedirs("tests/benchmark_results", exist_ok=True)
    ts = time.strftime("%Y%m%d_%H%M%S")
    outfile = f"tests/benchmark_results/knowledge_{ts}.json"
    with open(outfile, "w") as f:
        json.dump(
            [{"case": tr.case_name, "mode": tr.mode, "model": tr.model,
              "query": tr.search_query, "results": tr.results,
              "latency_ms": tr.latency_ms, "hit": tr.hit}
             for tr in all_results],
            f, indent=2,
        )
    print(f"\nResults saved to {outfile}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Knowledge RAG query enhancement benchmark")
    parser.add_argument("--modes", default="raw,context,rewrite",
                        help="Comma-separated modes to test: raw,context,rewrite")
    parser.add_argument("--models", nargs="+",
                        help="Specific model names for rewrite mode")
    parser.add_argument("--all-models", action="store_true",
                        help="Test all models available in LM Studio")
    parser.add_argument("--lm-url", default="http://localhost:1234/v1",
                        help="LM Studio API URL (default: http://localhost:1234/v1)")
    parser.add_argument("--qdrant-url", default="http://localhost:6333",
                        help="Qdrant server URL")
    parser.add_argument("--collection", default="wikipedia",
                        help="Qdrant collection name")
    parser.add_argument("--embed-model", default="all-MiniLM-L6-v2",
                        help="Sentence transformer model for embedding")
    parser.add_argument("--threshold", type=float, default=0.45,
                        help="Similarity threshold (default: 0.45, lower than prod to see more)")
    parser.add_argument("--top-k", type=int, default=5,
                        help="Results per query (default: 5)")
    args = parser.parse_args()

    run_benchmark(args)
