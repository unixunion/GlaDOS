"""
Model benchmark tool — measures LLM tool-calling accuracy and speed.

Sends golden test cases (from test_nlp.py) to the LLM with real tool schemas
and measures whether the model picks the correct tool and how fast it responds.

Run:
    python tests/benchmark_models.py                        # test currently loaded model
    python tests/benchmark_models.py --model qwen2.5-14b    # load, test, unload one model
    python tests/benchmark_models.py --all                  # cycle through ALL available models
    python tests/benchmark_models.py --models m1 m2 m3      # cycle through specific models
"""
import argparse
import dataclasses
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime

import requests
from loguru import logger
from openai import OpenAI

# ---------------------------------------------------------------------------
# Project imports
# ---------------------------------------------------------------------------
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from glados.config import GladosConfig
from glados.context.activity import Activity
from glados.system.plugin import PluginSystem, load_plugins

# Golden test cases: (text, expected_tool, min_confidence)
from tests.test_nlp import INTENT_TEST_CASES



# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class TestCase:
    text: str
    expected_tool: str


@dataclasses.dataclass
class TestResult:
    test_case: TestCase
    called_tool: str | None = None
    called_params: dict | None = None
    tool_correct: bool = False
    ttft_ms: float = 0.0
    total_time_ms: float = 0.0
    response_text: str = ""
    error: str | None = None


# ---------------------------------------------------------------------------
# LM Studio model management (via lms CLI)
# ---------------------------------------------------------------------------

def lms_list_models() -> list[str]:
    """List all LLM models available on disk via lms CLI."""
    try:
        result = subprocess.run(
            ["lms", "ls", "--llm", "--json"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            logger.error(f"lms ls failed: {result.stderr}")
            return []
        models = json.loads(result.stdout)
        return [m.get("modelKey", "") for m in models if m.get("modelKey")]
    except FileNotFoundError:
        logger.error("lms CLI not found. Install LM Studio and ensure 'lms' is on your PATH.")
        return []
    except Exception as e:
        logger.error(f"Error listing models: {e}")
        return []


def lms_get_loaded() -> list[str]:
    """Get currently loaded model(s) via lms CLI."""
    try:
        result = subprocess.run(
            ["lms", "ps", "--json"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []
        models = json.loads(result.stdout)
        return [m.get("modelKey") or m.get("identifier", "") for m in models]
    except Exception:
        return []


def lms_load_model(model_key: str, timeout: int = 120) -> bool:
    """Load a model in LM Studio. Returns True on success."""
    print(f"\n  Loading {model_key}...")
    try:
        result = subprocess.run(
            ["lms", "load", model_key],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            logger.error(f"Failed to load {model_key}: {result.stderr.strip()}")
            return False
        # Give LM Studio a moment to make the model available via API
        time.sleep(2)
        print(f"  Loaded {model_key}")
        return True
    except subprocess.TimeoutExpired:
        logger.error(f"Timeout loading {model_key} (>{timeout}s)")
        return False
    except Exception as e:
        logger.error(f"Error loading {model_key}: {e}")
        return False


def lms_unload_model(model_key: str = None) -> bool:
    """Unload a model (or all models if no key given)."""
    try:
        cmd = ["lms", "unload"]
        if model_key:
            cmd.append(model_key)
        else:
            cmd.append("--all")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            logger.warning(f"Unload warning: {result.stderr.strip()}")
        time.sleep(1)
        return True
    except Exception as e:
        logger.error(f"Error unloading: {e}")
        return False


def discover_loaded_model_id(completion_url: str) -> str | None:
    """Query the OpenAI-compatible /models endpoint to get the loaded model ID."""
    try:
        url = completion_url.rstrip("/")
        if not url.endswith("/models"):
            url = f"{url}/models"
        resp = requests.get(url, timeout=5)
        resp.raise_for_status()
        models = resp.json().get("data", [])
        if models:
            return models[0].get("id")
    except Exception:
        pass
    return None


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def bootstrap(config_path: str):
    """Load plugins and config. Returns (client, config, tools, system_messages)."""
    load_plugins("plugins")

    config = GladosConfig.from_yaml(config_path)
    client = OpenAI(base_url=config.completion_url, api_key=config.api_key or "lm-studio")

    # Get GENERAL activity tools (largest set)
    plugin_system = PluginSystem()
    tools = plugin_system.get_available_tools(activity=Activity.GENERAL)
    logger.info(f"Loaded {len(tools)} GENERAL-activity tools")

    # Build system messages from config
    system_messages = []
    if config.personality_preprompt:
        for entry in config.personality_preprompt:
            for role, content in entry.items():
                system_messages.append({"role": role, "content": content.strip()})

    return client, config, tools, system_messages


# ---------------------------------------------------------------------------
# Test suite
# ---------------------------------------------------------------------------

def build_test_suite(tools: list[dict]) -> list[TestCase]:
    """Filter INTENT_TEST_CASES to only tools the LLM will see."""
    available = set()
    for t in tools:
        if isinstance(t, dict) and "function" in t:
            available.add(t["function"]["name"])

    suite = []
    skipped = []
    for text, expected_tool, _confidence in INTENT_TEST_CASES:
        if expected_tool.startswith("_nlp_"):
            skipped.append(expected_tool)
            continue
        if expected_tool not in available:
            skipped.append(expected_tool)
            continue
        suite.append(TestCase(text=text, expected_tool=expected_tool))

    if skipped:
        logger.info(f"Skipped {len(skipped)} test cases (NLP-only or not in GENERAL activity)")
    logger.info(f"Benchmark suite: {len(suite)} test cases")
    return suite


# ---------------------------------------------------------------------------
# Single test execution
# ---------------------------------------------------------------------------

def run_single_test(
    client: OpenAI,
    model_id: str,
    system_messages: list[dict],
    tools: list[dict],
    test_case: TestCase,
) -> TestResult:
    """Send one utterance to the LLM and measure tool selection + timing."""
    messages = list(system_messages) + [{"role": "user", "content": test_case.text}]

    result = TestResult(test_case=test_case)
    t_start = time.perf_counter()

    try:
        stream = client.chat.completions.create(
            model=model_id,
            messages=messages,
            tools=tools,
            tool_choice="auto",
            stream=True,
            temperature=0.0,
            timeout=30.0,
        )

        first_chunk = True
        pending_tool_calls: dict[int, dict] = {}
        text_parts: list[str] = []

        for chunk in stream:
            if first_chunk:
                result.ttft_ms = (time.perf_counter() - t_start) * 1000
                first_chunk = False

            choice = chunk.choices[0] if chunk.choices else None
            if not choice:
                continue

            delta = choice.delta

            # Accumulate text
            if delta.content:
                text_parts.append(delta.content)

            # Accumulate tool calls
            if delta.tool_calls:
                for tc_delta in delta.tool_calls:
                    idx = tc_delta.index
                    if idx not in pending_tool_calls:
                        pending_tool_calls[idx] = {"name": None, "arguments": ""}
                    if tc_delta.function and tc_delta.function.name:
                        pending_tool_calls[idx]["name"] = tc_delta.function.name
                    if tc_delta.function and tc_delta.function.arguments:
                        pending_tool_calls[idx]["arguments"] += tc_delta.function.arguments

        result.total_time_ms = (time.perf_counter() - t_start) * 1000
        result.response_text = "".join(text_parts)

        # Parse first tool call
        if pending_tool_calls:
            first_tc = pending_tool_calls[min(pending_tool_calls.keys())]
            result.called_tool = first_tc["name"]
            try:
                result.called_params = json.loads(first_tc["arguments"]) if first_tc["arguments"] else {}
            except json.JSONDecodeError:
                result.called_params = None

        result.tool_correct = result.called_tool == test_case.expected_tool

    except Exception as e:
        result.total_time_ms = (time.perf_counter() - t_start) * 1000
        result.error = str(e)
        logger.error(f"Error on '{test_case.text}': {e}")

    return result


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(
    client: OpenAI,
    model_id: str,
    system_messages: list[dict],
    tools: list[dict],
    suite: list[TestCase],
) -> list[TestResult]:
    """Run all test cases sequentially."""
    results = []
    total = len(suite)

    for i, tc in enumerate(suite, 1):
        status = f"[{i}/{total}]"
        result = run_single_test(client, model_id, system_messages, tools, tc)

        mark = "PASS" if result.tool_correct else "FAIL"
        got = result.called_tool or "(no tool call)"
        print(f"  {status} {mark}  {tc.text[:50]:<50}  expected: {tc.expected_tool:<25} got: {got:<25} {result.total_time_ms:.0f}ms")

        results.append(result)

    return results


# ---------------------------------------------------------------------------
# Scorecard
# ---------------------------------------------------------------------------

def print_scorecard(model_id: str, results: list[TestResult], tools: list[dict]):
    """Print a summary scorecard to stdout."""
    total = len(results)
    correct = sum(1 for r in results if r.tool_correct)
    accuracy = (correct / total * 100) if total else 0

    ttft_values = [r.ttft_ms for r in results if r.ttft_ms > 0]
    time_values = [r.total_time_ms for r in results if r.total_time_ms > 0]
    avg_ttft = sum(ttft_values) / len(ttft_values) if ttft_values else 0
    avg_total = sum(time_values) / len(time_values) if time_values else 0

    print()
    print(f"{'=' * 60}")
    print(f"  Model Benchmark: {model_id}")
    print(f"{'=' * 60}")
    print(f"  Test cases: {total} | Tools: {len(tools)}")
    print()
    print(f"  Tool Accuracy: {correct}/{total} ({accuracy:.1f}%)")
    print(f"  Avg TTFT: {avg_ttft:.0f}ms | Avg Total: {avg_total:.0f}ms")
    print()

    # Per-tool breakdown
    per_tool: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0, "times": []})
    for r in results:
        tool = r.test_case.expected_tool
        per_tool[tool]["total"] += 1
        per_tool[tool]["times"].append(r.total_time_ms)
        if r.tool_correct:
            per_tool[tool]["correct"] += 1

    print(f"  {'Tool':<28} {'Score':>7}  {'Acc':>5}  {'Avg Time':>9}")
    print(f"  {'-' * 55}")
    for tool_name in sorted(per_tool.keys()):
        info = per_tool[tool_name]
        acc = info["correct"] / info["total"] * 100 if info["total"] else 0
        avg_t = sum(info["times"]) / len(info["times"]) if info["times"] else 0
        print(f"  {tool_name:<28} {info['correct']:>3}/{info['total']:<3}  {acc:>4.0f}%  {avg_t:>7.0f}ms")

    # Failures
    failures = [r for r in results if not r.tool_correct]
    if failures:
        print()
        print(f"  Failures ({len(failures)}):")
        for r in failures:
            got = r.called_tool or "(no tool call)"
            print(f"    \"{r.test_case.text}\"")
            print(f"      expected: {r.test_case.expected_tool} -> got: {got}")
            if r.response_text:
                preview = r.response_text[:100].replace("\n", " ")
                print(f"      text: {preview}")

    print(f"{'=' * 60}")


# ---------------------------------------------------------------------------
# Save results
# ---------------------------------------------------------------------------

def save_results(model_id: str, results: list[TestResult], tools: list[dict], output_dir: str) -> str:
    """Save benchmark results to a JSON file."""
    os.makedirs(output_dir, exist_ok=True)

    total = len(results)
    correct = sum(1 for r in results if r.tool_correct)
    ttft_values = [r.ttft_ms for r in results if r.ttft_ms > 0]
    time_values = [r.total_time_ms for r in results if r.total_time_ms > 0]

    # Per-tool stats
    per_tool: dict[str, dict] = defaultdict(lambda: {"correct": 0, "total": 0, "times": []})
    for r in results:
        tool = r.test_case.expected_tool
        per_tool[tool]["total"] += 1
        per_tool[tool]["times"].append(r.total_time_ms)
        if r.tool_correct:
            per_tool[tool]["correct"] += 1

    per_tool_summary = {}
    for tool_name, info in per_tool.items():
        per_tool_summary[tool_name] = {
            "total": info["total"],
            "correct": info["correct"],
            "accuracy_pct": round(info["correct"] / info["total"] * 100, 1) if info["total"] else 0,
            "avg_time_ms": round(sum(info["times"]) / len(info["times"]), 1) if info["times"] else 0,
        }

    data = {
        "model": model_id,
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "tool_count": len(tools),
        "summary": {
            "total_cases": total,
            "correct": correct,
            "accuracy_pct": round(correct / total * 100, 1) if total else 0,
            "avg_ttft_ms": round(sum(ttft_values) / len(ttft_values), 1) if ttft_values else 0,
            "avg_total_time_ms": round(sum(time_values) / len(time_values), 1) if time_values else 0,
        },
        "per_tool": per_tool_summary,
        "results": [
            {
                "text": r.test_case.text,
                "expected_tool": r.test_case.expected_tool,
                "called_tool": r.called_tool,
                "tool_correct": r.tool_correct,
                "called_params": r.called_params,
                "ttft_ms": round(r.ttft_ms, 1),
                "total_time_ms": round(r.total_time_ms, 1),
                "response_text": r.response_text[:200] if r.response_text else "",
                "error": r.error,
            }
            for r in results
        ],
    }

    # Sanitize model name for filename
    safe_name = model_id.replace("/", "_").replace(" ", "_").strip("_")

    # Remove old result files for this model before writing the new one
    if os.path.isdir(output_dir):
        for old_file in os.listdir(output_dir):
            if old_file.startswith(safe_name + "_") and old_file.endswith(".json"):
                os.remove(os.path.join(output_dir, old_file))

    timestamp = datetime.now().strftime("%Y-%m-%dT%H%M%S")
    filename = f"{safe_name}_{timestamp}.json"
    filepath = os.path.join(output_dir, filename)

    with open(filepath, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\n  Results saved to {filepath}")
    return filepath


# ---------------------------------------------------------------------------
# Load existing results (for skip & comparison)
# ---------------------------------------------------------------------------

def _sanitize_model_key(model_key: str) -> str:
    """Sanitize a model key the same way save_results does for filenames."""
    return model_key.replace("/", "_").replace(" ", "_").strip("_")


def load_existing_results(output_dir: str) -> dict[str, dict]:
    """Scan output_dir for result JSON files.

    Returns a dict mapping sanitized model key -> most recent result data.
    If a model was tested multiple times, only the latest run is kept.
    """
    existing = {}
    if not os.path.isdir(output_dir):
        return existing

    for filename in os.listdir(output_dir):
        if not filename.endswith(".json"):
            continue
        filepath = os.path.join(output_dir, filename)
        try:
            with open(filepath) as f:
                data = json.load(f)
            model = data.get("model", "")
            timestamp = data.get("timestamp", "")
            key = _sanitize_model_key(model)
            # Keep the most recent result per model
            if key not in existing or timestamp > existing[key].get("timestamp", ""):
                existing[key] = data
        except (json.JSONDecodeError, OSError):
            continue

    return existing


def get_tested_model_keys(output_dir: str) -> set[str]:
    """Return sanitized model keys that already have benchmark results."""
    existing = load_existing_results(output_dir)
    return set(existing.keys())


# ---------------------------------------------------------------------------
# Comparison summary
# ---------------------------------------------------------------------------

def _format_comparison_row(model: str, summary: dict) -> str:
    """Format one row of the comparison table from a summary dict."""
    total = summary.get("total_cases", 0)
    correct = summary.get("correct", 0)
    accuracy = summary.get("accuracy_pct", 0)
    avg_ttft = summary.get("avg_ttft_ms", 0)
    avg_total = summary.get("avg_total_time_ms", 0)
    name = model[:38]
    return f"  {name:<40} {correct:>2}/{total} ({accuracy:4.1f}%) {avg_ttft:>7.0f}ms {avg_total:>8.0f}ms"


def print_comparison(all_run_results: list[tuple[str, list[TestResult]]], tools: list[dict],
                     prior_results: dict[str, dict] | None = None):
    """Print a side-by-side comparison table.

    Includes both freshly-run results and prior results loaded from disk.
    """
    # Build rows: list of (model, correct, total, row_string)
    rows: list[tuple[int, int, str]] = []

    # Fresh results
    for model_id, results in all_run_results:
        total = len(results)
        correct = sum(1 for r in results if r.tool_correct)
        accuracy = correct / total * 100 if total else 0
        ttft_values = [r.ttft_ms for r in results if r.ttft_ms > 0]
        time_values = [r.total_time_ms for r in results if r.total_time_ms > 0]
        avg_ttft = sum(ttft_values) / len(ttft_values) if ttft_values else 0
        avg_total = sum(time_values) / len(time_values) if time_values else 0

        summary = {"total_cases": total, "correct": correct, "accuracy_pct": round(accuracy, 1),
                    "avg_ttft_ms": round(avg_ttft), "avg_total_time_ms": round(avg_total)}
        rows.append((correct, total, _format_comparison_row(model_id, summary)))

    # Prior results from disk (skip duplicates already in fresh results)
    if prior_results:
        fresh_keys = {_sanitize_model_key(m) for m, _ in all_run_results}
        for key, data in prior_results.items():
            if key in fresh_keys:
                continue
            summary = data.get("summary", {})
            model = data.get("model", key)
            correct = summary.get("correct", 0)
            total = summary.get("total_cases", 0)
            rows.append((correct, total, _format_comparison_row(f"{model} (cached)", summary)))

    if len(rows) < 2:
        return

    # Sort by accuracy descending
    rows.sort(key=lambda r: (-r[0], r[1]))

    print()
    print(f"{'=' * 80}")
    print(f"  COMPARISON SUMMARY")
    print(f"{'=' * 80}")
    print(f"  {'Model':<40} {'Accuracy':>10} {'Avg TTFT':>10} {'Avg Total':>10}")
    print(f"  {'-' * 74}")
    for _, _, row_str in rows:
        print(row_str)
    print(f"{'=' * 80}")


# ---------------------------------------------------------------------------
# Delta logic — find new test cases not in prior results, merge after run
# ---------------------------------------------------------------------------

def get_delta_suite(suite: list[TestCase], prior_data: dict) -> list[TestCase]:
    """Return only the test cases that weren't in a prior result file."""
    prior_texts = {r["text"] for r in prior_data.get("results", [])}
    return [tc for tc in suite if tc.text not in prior_texts]


def merge_results(
    prior_data: dict,
    new_results: list[TestResult],
    tools: list[dict],
) -> tuple[str, list[TestResult]]:
    """Merge prior JSON results with new TestResults. Returns (model_id, all_results)."""
    model_id = prior_data.get("model", "unknown")

    # Convert prior JSON entries back to TestResult objects
    all_results: list[TestResult] = []
    for r in prior_data.get("results", []):
        all_results.append(TestResult(
            test_case=TestCase(text=r["text"], expected_tool=r["expected_tool"]),
            called_tool=r.get("called_tool"),
            called_params=r.get("called_params"),
            tool_correct=r.get("tool_correct", False),
            ttft_ms=r.get("ttft_ms", 0),
            total_time_ms=r.get("total_time_ms", 0),
            response_text=r.get("response_text", ""),
            error=r.get("error"),
        ))

    # Append new results
    all_results.extend(new_results)
    return model_id, all_results


# ---------------------------------------------------------------------------
# Run one model (load -> benchmark -> unload)
# ---------------------------------------------------------------------------

def benchmark_single_model(
    client: OpenAI,
    config: GladosConfig,
    model_key: str,
    tools: list[dict],
    system_messages: list[dict],
    suite: list[TestCase],
    output_dir: str,
    manage_loading: bool = True,
    prior_data: dict | None = None,
) -> list[TestResult] | None:
    """Load a model, run the benchmark, unload it. Returns results or None on failure.

    If prior_data is provided, only runs test cases not already in those results,
    then merges old + new into a single updated result file.
    """
    # Determine which cases to actually run
    run_suite = suite
    if prior_data:
        run_suite = get_delta_suite(suite, prior_data)
        if not run_suite:
            print(f"  {model_key}: all {len(suite)} test cases already have results, skipping.")
            model_id, all_results = merge_results(prior_data, [], tools)
            return all_results

    if manage_loading:
        lms_unload_model()
        if not lms_load_model(model_key):
            print(f"  SKIPPING {model_key} (failed to load)")
            return None

    model_id = discover_loaded_model_id(config.completion_url) or model_key

    delta_label = f" (delta: {len(run_suite)} new)" if prior_data else ""
    print(f"\n{'=' * 60}")
    print(f"  Benchmarking: {model_id}{delta_label}")
    print(f"  Test cases: {len(run_suite)} | Tools: {len(tools)}")
    print(f"{'=' * 60}\n")

    new_results = run_benchmark(client, model_id, system_messages, tools, run_suite)

    # Merge with prior results if this is a delta run
    if prior_data:
        model_id, all_results = merge_results(prior_data, new_results, tools)
    else:
        all_results = new_results

    print_scorecard(model_id, all_results, tools)
    save_results(model_id, all_results, tools, output_dir)

    if manage_loading:
        lms_unload_model(model_key)

    return all_results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Benchmark LLM models for tool-calling accuracy and speed")
    parser.add_argument("--config", default="glados_config.yml", help="Path to glados_config.yml")
    parser.add_argument("--model", default=None, help="Load and test a single model")
    parser.add_argument("--models", nargs="+", default=None, help="Load and test specific models in sequence")
    parser.add_argument("--all", action="store_true", help="Cycle through ALL available LLM models (skips already-tested)")
    parser.add_argument("--retest", action="store_true", help="Re-test models even if results already exist")
    parser.add_argument("--report", action="store_true", help="Print comparison report from existing results (no benchmarking)")
    parser.add_argument("--output-dir", default="tests/benchmark_results", help="Directory for result JSON files")
    args = parser.parse_args()

    # --report: just print the comparison table from saved results and exit
    if args.report:
        prior_results = load_existing_results(args.output_dir)
        if not prior_results:
            print(f"No results found in {args.output_dir}/")
            return
        # Print comparison using only cached data (no fresh runs)
        print_comparison([], [], prior_results)
        return

    print("Bootstrapping...")
    client, config, tools, system_messages = bootstrap(args.config)

    tool_names = [t["function"]["name"] for t in tools if isinstance(t, dict) and "function" in t]
    print(f"Tools ({len(tools)}): {', '.join(sorted(tool_names))}")

    suite = build_test_suite(tools)
    if not suite:
        print("No test cases to run!")
        return

    # Load existing results for delta detection and comparison
    prior_results = load_existing_results(args.output_dir)

    # Determine which models to test
    if args.all:
        all_models = lms_list_models()
        if not all_models:
            print("No models found via 'lms ls'. Is LM Studio installed?")
            return
        models_to_test = all_models

        if args.retest:
            print(f"\nWill benchmark {len(models_to_test)} models (full retest):")
        else:
            # Show delta info
            need_delta = 0
            fully_done = 0
            fresh = 0
            for m in models_to_test:
                key = _sanitize_model_key(m)
                if key in prior_results:
                    delta = get_delta_suite(suite, prior_results[key])
                    if delta:
                        need_delta += 1
                    else:
                        fully_done += 1
                else:
                    fresh += 1
            print(f"\nModels: {len(models_to_test)} total — {fresh} new, {need_delta} need delta update, {fully_done} fully up-to-date")

        print(f"\nWill benchmark {len(models_to_test)} models:")
        for m in models_to_test:
            key = _sanitize_model_key(m)
            if key in prior_results and not args.retest:
                delta = get_delta_suite(suite, prior_results[key])
                prior_count = len(prior_results[key].get("results", []))
                if delta:
                    print(f"  - {m}  (delta: {len(delta)} new cases, {prior_count} cached)")
                else:
                    print(f"  - {m}  (up-to-date, {prior_count} cached — skip)")
            else:
                print(f"  - {m}  (full run)")
    elif args.models:
        models_to_test = args.models
    elif args.model:
        models_to_test = [args.model]
    else:
        # No model specified — test whatever is currently loaded
        loaded = lms_get_loaded()
        if loaded:
            model_id = discover_loaded_model_id(config.completion_url) or loaded[0]
            print(f"\nRunning {len(suite)} test cases against loaded model: {model_id}\n")
            results = run_benchmark(client, model_id, system_messages, tools, suite)
            print_scorecard(model_id, results, tools)
            save_results(model_id, results, tools, args.output_dir)
            return
        else:
            print("No model loaded and no --model/--models/--all specified.")
            print("Either load a model in LM Studio or use --model/--all.")
            return

    # Cycle through models: load -> test -> unload -> repeat
    all_run_results: list[tuple[str, list[TestResult]]] = []

    for i, model_key in enumerate(models_to_test, 1):
        print(f"\n{'#' * 60}")
        print(f"  Model {i}/{len(models_to_test)}: {model_key}")
        print(f"{'#' * 60}")

        # Pass prior data for delta merging (unless --retest)
        key = _sanitize_model_key(model_key)
        prior = prior_results.get(key) if not args.retest else None

        results = benchmark_single_model(
            client, config, model_key, tools, system_messages, suite, args.output_dir,
            prior_data=prior,
        )
        if results:
            all_run_results.append((model_key, results))

    # Print comparison — includes both new runs and any models not in this batch
    print_comparison(all_run_results, tools, prior_results)


if __name__ == "__main__":
    main()
