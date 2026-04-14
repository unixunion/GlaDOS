"""NLP threshold auto-tuner.

Runs all intent test cases, collects confidence scores per tool, and
recommends optimal per-tool NLP thresholds. Optionally writes them
to glados_config.yml.

Usage:
    python tools/tune_nlp_thresholds.py              # summary + recommendations
    python tools/tune_nlp_thresholds.py --report      # detailed per-tool breakdown
    python tools/tune_nlp_thresholds.py --apply       # write recommended thresholds to config
"""
import argparse
import os
import sys
import tempfile
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from loguru import logger
logger.remove()
logger.add(sys.stderr, level="WARNING")


def load_test_cases():
    """Import INTENT_TEST_CASES from the test module."""
    from tests.test_nlp import INTENT_TEST_CASES
    return INTENT_TEST_CASES


def run_classification(test_cases):
    """Classify every test phrase and collect results."""
    # Use temp dirs to avoid polluting real data
    tmpdir = tempfile.mkdtemp(prefix="glados_tune_")
    os.environ["PANTRY_DATA_DIR"] = os.path.join(tmpdir, "pantry")
    os.environ["TIMER_DATA_DIR"] = os.path.join(tmpdir, "timers")
    os.environ["ALARM_DATA_DIR"] = os.path.join(tmpdir, "alarms")
    for d in ("pantry", "timers", "alarms"):
        os.makedirs(os.path.join(tmpdir, d), exist_ok=True)

    from glados.system.plugin import load_plugins
    load_plugins("plugins")

    from glados.system.intent_classifier import IntentClassifier
    ic = IntentClassifier()

    results = []
    for phrase, expected_tool, min_conf in test_cases:
        predicted, confidence = ic.predict_intent(phrase)
        results.append({
            "phrase": phrase,
            "expected": expected_tool,
            "predicted": str(predicted),
            "confidence": float(confidence),
            "correct": str(predicted) == expected_tool,
            "min_conf": min_conf,
        })

    # Cleanup env
    for key in ("PANTRY_DATA_DIR", "TIMER_DATA_DIR", "ALARM_DATA_DIR"):
        os.environ.pop(key, None)

    return results


def analyze_results(results):
    """Compute per-tool score distributions and recommend thresholds."""
    tools = defaultdict(lambda: {
        "correct_scores": [],
        "false_positive_scores": [],  # predicted this tool but shouldn't have
        "missed_phrases": [],  # should have been this tool but wasn't
        "total": 0,
        "correct_count": 0,
    })

    for r in results:
        expected = r["expected"]
        predicted = r["predicted"]
        conf = r["confidence"]

        tools[expected]["total"] += 1

        if r["correct"]:
            tools[expected]["correct_count"] += 1
            tools[expected]["correct_scores"].append(conf)
        else:
            tools[expected]["missed_phrases"].append({
                "phrase": r["phrase"],
                "predicted_as": predicted,
                "confidence": conf,
            })
            # Record as false positive on the predicted tool
            if predicted:
                tools[predicted]["false_positive_scores"].append({
                    "phrase": r["phrase"],
                    "should_be": expected,
                    "confidence": conf,
                })

    # Calculate recommendations
    recommendations = {}
    for tool_name, data in sorted(tools.items()):
        correct = data["correct_scores"]
        fps = data["false_positive_scores"]

        if not correct:
            recommendations[tool_name] = {
                "threshold": None,
                "status": "no_data",
                "reason": "No correct classifications",
            }
            continue

        min_correct = min(correct)
        max_fp = max((fp["confidence"] for fp in fps), default=0.0)

        if min_correct > max_fp and max_fp > 0:
            # Clean separation — threshold between FP and correct
            threshold = round((min_correct + max_fp) / 2, 2)
            status = "clean"
        elif max_fp == 0:
            # No false positives — threshold just below min correct
            threshold = round(min_correct * 0.85, 2)
            status = "clean"
        else:
            # Overlap — use min correct with safety margin, warn
            threshold = round(min_correct * 0.9, 2)
            status = "overlap"

        recommendations[tool_name] = {
            "threshold": max(0.05, threshold),
            "status": status,
            "min_correct": round(min_correct, 3),
            "max_correct": round(max(correct), 3),
            "median_correct": round(sorted(correct)[len(correct) // 2], 3),
            "max_false_positive": round(max_fp, 3) if max_fp else None,
            "false_positive_count": len(fps),
        }

    # Global threshold recommendation
    all_correct_scores = [s for d in tools.values() for s in d["correct_scores"]]
    if all_correct_scores:
        sorted_scores = sorted(all_correct_scores)
        # 10th percentile — 90% of correct classifications are above this
        p10_idx = max(0, len(sorted_scores) // 10)
        global_threshold = round(sorted_scores[p10_idx] * 0.9, 2)
    else:
        global_threshold = 0.2

    return tools, recommendations, global_threshold


def get_current_thresholds():
    """Read current per-tool thresholds from config."""
    try:
        from glados.config import GladosConfig
        config = GladosConfig.from_yaml("glados_config.yml")
        current = {
            "hybrid_nlp_threshold": getattr(config, "hybrid_nlp_threshold", 0.8),
            "nlp_confidence_threshold": getattr(config, "nlp_confidence_threshold", 0.4),
        }
        # Per-tool from plugin configs
        for plugin_entry in (config.plugins or []):
            if isinstance(plugin_entry, dict):
                name = plugin_entry.get("name", "")
                cfg = plugin_entry.get("config", {})
            else:
                name = getattr(plugin_entry, "name", "")
                cfg = getattr(plugin_entry, "config", {})
            if cfg and isinstance(cfg, dict) and "nlp_threshold" in cfg:
                current[f"tool:{name}"] = cfg["nlp_threshold"]
        return current
    except Exception:
        return {}


def print_summary(results, tools, recommendations, global_threshold):
    """Print concise summary with recommendations."""
    total = len(results)
    correct = sum(1 for r in results if r["correct"])
    current = get_current_thresholds()

    print(f"\n{'═'*60}")
    print(f"  NLP Threshold Tuning Report")
    print(f"{'═'*60}")
    print(f"  Overall accuracy: {correct}/{total} ({correct/total*100:.1f}%)")
    print(f"  Current hybrid threshold: {current.get('hybrid_nlp_threshold', '?')}")
    print(f"  Recommended hybrid threshold: {global_threshold}")
    print()

    # Per-tool recommendations
    for tool_name, rec in sorted(recommendations.items()):
        data = tools[tool_name]
        total_t = data["total"]
        correct_t = data["correct_count"]
        pct = correct_t / total_t * 100 if total_t else 0

        status_icon = "✓" if correct_t == total_t else "⚠" if pct >= 80 else "✗"
        current_th = current.get(f"tool:{tool_name}", "-")

        if rec["threshold"] is None:
            print(f"  {status_icon} {tool_name:30s} ({correct_t}/{total_t})  no data")
            continue

        margin = ""
        if rec["status"] == "overlap":
            margin = " ⚠ overlaps with false positives"

        scores_str = f"{rec['min_correct']:.2f} - {rec['max_correct']:.2f}"
        print(f"  {status_icon} {tool_name:30s} ({correct_t}/{total_t})  "
              f"scores: {scores_str:12s}  "
              f"current: {str(current_th):5s}  "
              f"recommended: {rec['threshold']:.2f}{margin}")

    print(f"\n{'─'*60}")
    print(f"  Global recommendations:")
    print(f"    hybrid_nlp_threshold: {global_threshold}")
    print(f"    nlp_confidence_threshold: {max(0.05, global_threshold * 0.5):.2f}")
    print(f"{'═'*60}\n")


def print_detailed_report(results, tools, recommendations):
    """Print detailed per-tool breakdown with individual phrase scores."""
    for tool_name in sorted(tools.keys()):
        data = tools[tool_name]
        rec = recommendations[tool_name]
        total_t = data["total"]
        correct_t = data["correct_count"]

        print(f"\n{'─'*50}")
        print(f"  {tool_name} ({correct_t}/{total_t})")
        print(f"{'─'*50}")

        if data["correct_scores"]:
            print(f"  Correct classifications:")
            for r in sorted([r for r in results if r["expected"] == tool_name and r["correct"]],
                            key=lambda x: -x["confidence"]):
                print(f"    ✓ {r['confidence']:.3f}  {r['phrase'][:60]}")

        if data["missed_phrases"]:
            print(f"  Missed (should be {tool_name}):")
            for m in data["missed_phrases"]:
                print(f"    ✗ {m['confidence']:.3f}  {m['phrase'][:50]} → {m['predicted_as']}")

        if data["false_positive_scores"]:
            print(f"  False positives (predicted {tool_name} but shouldn't):")
            for fp in data["false_positive_scores"]:
                print(f"    ! {fp['confidence']:.3f}  {fp['phrase'][:50]} (should be {fp['should_be']})")

        if rec["threshold"]:
            print(f"  Recommended threshold: {rec['threshold']:.2f}")


def apply_thresholds(recommendations, global_threshold):
    """Write recommended thresholds to glados_config.yml."""
    import yaml

    config_path = "glados_config.yml"
    with open(config_path, "r") as f:
        config = yaml.safe_load(f)

    glados = config.get("Glados", config)

    # Update global thresholds
    glados["hybrid_nlp_threshold"] = global_threshold
    glados["nlp_confidence_threshold"] = max(0.05, round(global_threshold * 0.5, 2))

    # Update per-tool thresholds
    plugins = glados.get("plugins", [])
    existing_names = {p.get("name", "") for p in plugins if isinstance(p, dict)}

    for tool_name, rec in recommendations.items():
        if rec["threshold"] is None:
            continue

        if tool_name in existing_names:
            for p in plugins:
                if isinstance(p, dict) and p.get("name") == tool_name:
                    if "config" not in p:
                        p["config"] = {}
                    p["config"]["nlp_threshold"] = rec["threshold"]
        else:
            plugins.append({
                "name": tool_name,
                "config": {"nlp_threshold": rec["threshold"]},
            })

    glados["plugins"] = plugins

    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)

    print(f"Written thresholds to {config_path}")
    print(f"  Global: hybrid={global_threshold}, confidence={max(0.05, round(global_threshold * 0.5, 2))}")
    print(f"  Per-tool: {len([r for r in recommendations.values() if r['threshold']])} tools configured")


def main():
    parser = argparse.ArgumentParser(description="Auto-tune NLP intent thresholds")
    parser.add_argument("--report", action="store_true", help="Detailed per-tool breakdown")
    parser.add_argument("--apply", action="store_true", help="Write recommended thresholds to config")
    args = parser.parse_args()

    print("Loading test cases...")
    test_cases = load_test_cases()
    print(f"Running {len(test_cases)} classifications...")

    results = run_classification(test_cases)
    tools, recommendations, global_threshold = analyze_results(results)

    print_summary(results, tools, recommendations, global_threshold)

    if args.report:
        print_detailed_report(results, tools, recommendations)

    if args.apply:
        apply_thresholds(recommendations, global_threshold)


if __name__ == "__main__":
    main()
