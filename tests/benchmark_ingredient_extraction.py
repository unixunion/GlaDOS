"""Benchmark LLM ingredient extraction — tests accuracy and speed across models.

Runs a sample of real recipe ingredient lines through each available LLM model
and compares against hand-labeled ground truth.

Usage:
    python tests/benchmark_ingredient_extraction.py
    python tests/benchmark_ingredient_extraction.py --url http://localhost:1234/v1
"""
import argparse
import time
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Ground truth: (raw ingredient line, expected core ingredient)
GROUND_TRUTH = [
    # Simple
    ("salt", "salt"),
    ("eggs", "eggs"),
    ("sugar", "sugar"),
    ("water", "water"),
    ("honey", "honey"),

    # With quantities/units baked in
    ("teaspoon salt", "salt"),
    ("tablespoons olive oil", "olive oil"),
    ("teaspoon vanilla extract", "vanilla extract"),
    ("tablespoons unsalted butter", "butter"),
    ("teaspoon ground cinnamon", "cinnamon"),
    ("teaspoon freshly ground black pepper", "black pepper"),
    ("tablespoons extra-virgin olive oil", "olive oil"),
    ("kosher salt, freshly ground pepper", "salt and pepper"),

    # With preparation instructions
    ("garlic cloves, minced", "garlic"),
    ("garlic cloves, finely chopped", "garlic"),
    ("onion, chopped", "onion"),
    ("shallot, finely chopped", "shallot"),
    ("scallions, thinly sliced", "scallions"),
    ("fresh lemon juice", "lemon juice"),
    ("freshly grated parmesan cheese", "parmesan cheese"),

    # With qualifiers
    ("skinless, boneless chicken breasts", "chicken breast"),
    ("bone-in, skin-on chicken breasts", "chicken breast"),
    ("boneless, skinless chicken thighs", "chicken thigh"),
    ("extra-virgin olive oil", "olive oil"),
    ("low-sodium chicken broth", "chicken broth"),
    ("reduced-sodium soy sauce", "soy sauce"),
    ("whole milk", "milk"),
    ("heavy cream", "heavy cream"),
    ("dark brown sugar", "brown sugar"),
    ("all-purpose flour", "flour"),

    # Complex / verbose
    ("tablespoons cold unsalted butter, cut into 1/2-inch cubes", "butter"),
    ("one 15 1/2 ounce can red kidney beans, rinsed and drained", "kidney beans"),
    ("red bell peppers, halved, ribs and seeds removed", "red bell pepper"),
    ("fresh chorizo, casings removed", "chorizo"),
    ("sweet italian sausage, casings removed, divided", "italian sausage"),
    ("vanilla bean, split lengthwise", "vanilla bean"),
    ("eggs, lightly beaten", "eggs"),
    ("nonstick vegetable oil spray", "cooking spray"),
    ("unsalted butter, room temperature", "butter"),

    # Regional variants (would need synonym map too, but core extraction should still work)
    ("ground beef", "ground beef"),
    ("ground beef chuck", "ground beef"),
    ("lean ground turkey", "ground turkey"),
    ("italian sausage links", "italian sausage"),
    ("thick-cut bacon", "bacon"),

    # Unusual / edge cases
    ("desired fillings", "fillings"),
    ("mixed fresh shiitake, chanterelle, and porcini mushrooms", "mushrooms"),
    ("assorted crackers, for serving", "crackers"),
    ("whipping cream", "whipping cream"),
    ("packed light brown sugar", "brown sugar"),
    ("dry white wine", "white wine"),
    ("dry red wine", "red wine"),
]

SYSTEM_PROMPT = (
    "Extract the main ingredient from this recipe line. "
    "Reply with ONLY the core ingredient name, lowercase, no quantities, units, or preparation instructions.\n"
    "Examples:\n"
    '"tablespoons cold unsalted butter, cut into 1/2-inch cubes" → butter\n'
    '"skinless, boneless chicken breasts, about 1 pound" → chicken breast\n'
    '"garlic cloves, finely chopped" → garlic\n'
    '"freshly ground black pepper" → black pepper\n'
    '"extra-virgin olive oil" → olive oil\n'
    '"teaspoon salt" → salt\n'
)


def extract_ingredient(client, model: str, ingredient: str) -> tuple[str, float]:
    """Call LLM to extract core ingredient. Returns (result, latency_ms)."""
    t0 = time.perf_counter()
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": ingredient},
        ],
        max_tokens=20,
        temperature=0,
    )
    latency = (time.perf_counter() - t0) * 1000
    result = response.choices[0].message.content.strip().lower()
    return result, latency


def score_result(predicted: str, expected: str) -> bool:
    """Check if the extraction is acceptable (fuzzy match)."""
    from rapidfuzz import fuzz
    # Exact match
    if predicted == expected:
        return True
    # One contains the other
    if expected in predicted or predicted in expected:
        return True
    # Handle "salt and pepper" vs "salt & pepper" etc.
    if fuzz.ratio(predicted, expected) >= 85:
        return True
    # Core word match: "chicken breast" matches "chicken breasts"
    if fuzz.ratio(predicted.rstrip("s"), expected.rstrip("s")) >= 90:
        return True
    return False


def run_benchmark(url: str, models: list[str]):
    from openai import OpenAI

    client = OpenAI(base_url=url, api_key="not-needed")

    for model in models:
        print(f"\n{'='*60}")
        print(f"Model: {model}")
        print(f"{'='*60}")

        correct = 0
        total = len(GROUND_TRUTH)
        total_latency = 0
        failures = []

        for ingredient, expected in GROUND_TRUTH:
            try:
                result, latency = extract_ingredient(client, model, ingredient)
                total_latency += latency
                ok = score_result(result, expected)
                if ok:
                    correct += 1
                else:
                    failures.append((ingredient, expected, result))
            except Exception as e:
                failures.append((ingredient, expected, f"ERROR: {e}"))

        avg_latency = total_latency / total if total else 0
        accuracy = correct / total * 100 if total else 0

        print(f"Accuracy: {correct}/{total} ({accuracy:.1f}%)")
        print(f"Avg latency: {avg_latency:.0f}ms")
        print(f"Total time: {total_latency/1000:.1f}s")
        if failures:
            print(f"\nFailures ({len(failures)}):")
            for ing, exp, got in failures:
                print(f"  {ing[:50]:50s} expected='{exp}' got='{got}'")


def main():
    parser = argparse.ArgumentParser(description="Benchmark ingredient extraction across LLMs")
    parser.add_argument("--url", default="http://localhost:1234/v1", help="LLM API URL")
    parser.add_argument("--models", nargs="+", help="Model names to test (default: auto-detect)")
    args = parser.parse_args()

    # Auto-detect available models
    if not args.models:
        try:
            from openai import OpenAI
            client = OpenAI(base_url=args.url, api_key="not-needed")
            models_resp = client.models.list()
            args.models = [m.id for m in models_resp.data]
            print(f"Detected models: {', '.join(args.models)}")
        except Exception as e:
            print(f"Could not detect models at {args.url}: {e}")
            sys.exit(1)

    run_benchmark(args.url, args.models)


if __name__ == "__main__":
    main()
