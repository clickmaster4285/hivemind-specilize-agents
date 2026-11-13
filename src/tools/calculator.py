"""Exact arithmetic for the model (ISSUES_LOG O51): a 9B model gets sums wrong (a GTA download time said 8 h
for 2.2 h) or stops before the math. It calls `calculate` with an expression; Python works it out and the model
writes the answer with the result.

Only numbers, + - * / // % ** and brackets are evaluated (Python's own parser, walked node by node): no names,
no calls, no attribute access, so nothing in an expression can run code.
"""

from __future__ import annotations

import ast
import logging
import operator
import re
from typing import Any

logger = logging.getLogger(__name__)

CALCULATE = "calculate"
MAX_EXPRESSION_CHARS = 300   # a real calculation is short; longer is a model loop, not maths
MAX_EXPONENT = 100           # 9 ** 9 ** 9 would freeze the worker; no real question needs more
MAX_CALC_ROUNDS = 4          # calculate calls in a row before the model must answer (a download time takes 1-2)
RESULT_DIGITS = 12           # significant digits shown: exact for money and sizes, no float noise (0.1 + 0.2)

CALCULATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": CALCULATE,
        "description": (
            "Exact arithmetic. Use it for EVERY calculation with numbers (sums, percentages, averages, unit "
            "conversions, download or travel times, costs) instead of working it out yourself, then give the "
            "result in your answer. Numbers and + - * / ** ( ) only, e.g. '0.15 * 2400', or a 212 GB download at "
            "100 Mbps in seconds: '212 * 8 * 1000 / 100'."
        ),
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "the arithmetic, numbers only"}},
            "required": ["expression"],
        },
    },
}

_OPS = {
    ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv, ast.Mod: operator.mod, ast.Pow: operator.pow,
    ast.UAdd: operator.pos, ast.USub: operator.neg,
}
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")       # "2,400" → "2400"


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ValueError(f"exponent above {MAX_EXPONENT}")
        return _OPS[type(node.op)](left, right)
    raise ValueError("use numbers and + - * / ** ( ) only")


def calculate(expression: str) -> str:
    """The result as text, or "error: …" the model can act on (it sees it as the tool's reply)."""
    expr = _THOUSANDS.sub("", (expression or "").strip())
    if not expr or len(expr) > MAX_EXPRESSION_CHARS:
        return "error: give one short arithmetic expression"
    try:
        value = _eval(ast.parse(expr, mode="eval").body)
    except ZeroDivisionError:
        return "error: division by zero"
    except (SyntaxError, ValueError, TypeError, OverflowError) as exc:
        return f"error: {exc}"
    return str(int(value)) if float(value).is_integer() else format(value, f".{RESULT_DIGITS}g")


def chat_tools_with_calculator(client: Any, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]],
                               **options: Any) -> tuple[str, list[dict[str, Any]]]:
    """client.chat_tools, with every `calculate` call worked out here and handed back to the model, which then
    goes on. → (text, the other tools' calls) as soon as the model answers or picks another tool. `tools` must
    list CALCULATE_TOOL; options (model, on_delta, should_continue, temperature) pass through."""
    messages = list(messages)
    for _ in range(MAX_CALC_ROUNDS):
        text, calls = client.chat_tools(messages=messages, tools=tools, **options)
        sums = [c for c in calls if c["name"] == CALCULATE]
        if not sums or len(sums) < len(calls):      # an answer, or another tool: the caller takes it from here
            return text, [c for c in calls if c["name"] != CALCULATE]
        messages.append({"role": "assistant", "content": text,
                         "tool_calls": [{"function": {"name": CALCULATE, "arguments": c["arguments"]}} for c in sums]})
        for call in sums:
            expression = str((call["arguments"] or {}).get("expression") or "")
            result = calculate(expression)
            logger.info("calculate %r = %s", expression, result)
            messages.append({"role": "tool", "content": result})
    tools_left = [t for t in tools if t.get("function", {}).get("name") != CALCULATE]
    return client.chat_tools(messages=messages, tools=tools_left, **options)   # rounds used up: answer now
