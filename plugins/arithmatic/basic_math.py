import re

from loguru import logger

from glados.context.activity import Activity
from glados.nlp.extractors import word_to_number
from glados.mcp.decorators import mcp_tool


def _calc_extract(text: str) -> dict:
    """Extract two numbers and an operation from natural language."""
    cleaned = re.sub(r'[?.!,]', '', text.lower())

    # Detect operation from keywords
    op = "add"
    if any(w in cleaned for w in ["plus", "add", "sum"]):
        op = "add"
    elif any(w in cleaned for w in ["minus", "subtract", "take away"]):
        op = "subtract"
    elif any(w in cleaned for w in ["times", "multiply", "multiplied"]):
        op = "multiply"
    elif any(w in cleaned for w in ["divided", "divide", "over"]):
        op = "divide"

    # Extract numbers
    tokens = cleaned.split()
    numbers = []
    skip_next = False
    for i, token in enumerate(tokens):
        if skip_next:
            skip_next = False
            continue
        if re.match(r'^\d+(\.\d+)?$', token):
            numbers.append(float(token) if '.' in token else int(token))
            continue
        # Try two-word number first ("twenty five")
        if i + 1 < len(tokens):
            pair = f"{token} {tokens[i+1]}"
            n = word_to_number(pair)
            if n is not None:
                numbers.append(n)
                skip_next = True
                continue
        n = word_to_number(token)
        if n is not None:
            numbers.append(n)

    a = numbers[0] if len(numbers) >= 1 else 0
    b = numbers[1] if len(numbers) >= 2 else 0
    return {"a": a, "b": b, "operation": op}


def _calc_response(result) -> str:
    if isinstance(result, dict):
        if result.get("error"):
            return result["error"]
        return result.get("message", "Done.")
    return f"The answer is {result}."


@mcp_tool(
    description=(
        "Perform basic arithmetic: add, subtract, multiply, or divide two numbers."
    ),
    parameters={
        "a": {"type": "number", "description": "The first number"},
        "b": {"type": "number", "description": "The second number"},
        "operation": {
            "type": "string",
            "description": "The arithmetic operation",
            "enum": ["add", "subtract", "multiply", "divide"],
        },
    },
    required=["a", "b", "operation"],
    intents=[
        "what is 5 plus 7",
        "what is 2 plus 2",
        "what is 3 plus 3",
        "what is 100 plus 200",
        "add 2 and 2",
        "add nine and four together",
        "add 5 and 10",
        "what is the sum of 7 and 3",
        "what is 56 minus 12",
        "what is 10 minus 3",
        "what is 8 minus 2",
        "subtract 5 from 20",
        "take away 3 from 10",
        "what is 6 times 6",
        "what is 3 times 4",
        "multiply 7 by 2",
        "what is 10 multiplied by 5",
        "what is 7 divided by 2",
        "divide 10 by 3",
        "what is 100 divided by 4",
    ],
    process_output=False,
    activity=[Activity.GENERAL, Activity.UTILITIES, Activity.COOKING],
    nlp_extract_fn=_calc_extract,
    nlp_response=_calc_response,
)
def calculate(a: float, b: float, operation: str = "add") -> dict:
    """Perform basic arithmetic."""
    op = operation.lower().strip()
    logger.info(f"[Math] {a} {op} {b}")

    if op == "add":
        result = a + b
    elif op == "subtract":
        result = a - b
    elif op == "multiply":
        result = a * b
    elif op == "divide":
        if b == 0:
            return {"error": "I can't divide by zero."}
        result = a / b
    else:
        return {"error": f"Unknown operation: {op}"}

    # Format: drop .0 for whole numbers
    if isinstance(result, float) and result == int(result):
        result = int(result)

    return {"message": f"The answer is {result}."}
