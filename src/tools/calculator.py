"""Exact arithmetic for the model (ISSUES_LOG O51): a 9B model gets sums wrong (a GTA download time said 8 h
for 2.2 h) or stops before the math. It calls `calculate` with an expression; Python works it out and the model
writes the answer with the result.

Only numbers, units from UNITS, + - * / // % ** and brackets are evaluated (Python's own parser, walked node by
node): no other names, no calls, no attribute access, so nothing in an expression can run code.

Units (O57): with plain numbers the model still got the conversions wrong ('210 * 8 / 100' "hours" for a 4.7-hour
download). It writes '212 GB / 100 Mbps in hours'; the conversions come from the table.
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

# Each unit in a base: bits for data, seconds for time, so rates are bits per second. Decimal prefixes, as
# connection speeds and download sizes are sold; "i" units are binary (1 GiB = 2**30 bytes).
_DECIMAL = {"k": 10**3, "K": 10**3, "M": 10**6, "G": 10**9, "T": 10**12}
_BINARY = {"Ki": 2**10, "Mi": 2**20, "Gi": 2**30, "Ti": 2**40}
_BITS, _BYTES = 1, 8
_SECOND, _MINUTE, _HOUR, _DAY = 1, 60, 3600, 86400
UNITS: dict[str, float] = (
    {"b": _BITS, "bit": _BITS, "bits": _BITS, "B": _BYTES, "byte": _BYTES, "bytes": _BYTES, "bps": _BITS}
    | {f"{p}b": f * _BITS for p, f in _DECIMAL.items()} | {f"{p}B": f * _BYTES for p, f in _DECIMAL.items()}
    | {f"{p}bps": f * _BITS for p, f in _DECIMAL.items()} | {f"{p}B": f * _BYTES for p, f in _BINARY.items()}
    | {"s": _SECOND, "sec": _SECOND, "second": _SECOND, "seconds": _SECOND, "min": _MINUTE, "minute": _MINUTE,
       "minutes": _MINUTE, "h": _HOUR, "hr": _HOUR, "hour": _HOUR, "hours": _HOUR, "day": _DAY, "days": _DAY}
)

CALCULATE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": CALCULATE,
        "description": (
            "Exact arithmetic. Use it for EVERY calculation with numbers (sums, percentages, averages, unit "
            "conversions, download or travel times, costs) instead of working it out yourself, then give the "
            "result in your answer. Numbers, + - * / ** ( ) and units, e.g. '0.15 * 2400'. Write the units after "
            "the numbers as given and end with 'in <unit>': the tool does every conversion, so add no factor "
            "yourself (no * 8 or / 3600). E.g. a download time '212 GB / 100 Mbps in hours', a size '1500 MB in "
            "GB'. Units: b, B, kb, MB, GB, TB, GiB, Mbps, Gbps, s, min, hours, days (b = bits, B = bytes)."
        ),
        "parameters": {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "the arithmetic, with units if any"}},
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
_QUANTITY = re.compile(r"(\d+(?:\.\d+)?(?:[eE][+-]?\d+)?)\s*([A-Za-z]+)\b")   # "212 GB" → (212*GB)
_TARGET = re.compile(r"\s+in\s+([A-Za-z]+)\s*$")                            # "… in hours" → (…)/hours


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
        return node.value
    if isinstance(node, ast.Name) and node.id in UNITS:
        return UNITS[node.id]
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPS:
        return _OPS[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _OPS:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > MAX_EXPONENT:
            raise ValueError(f"exponent above {MAX_EXPONENT}")
        return _OPS[type(node.op)](left, right)
    if isinstance(node, ast.Name):
        raise ValueError(f"unknown unit {node.id!r}; units: b, B, kb, MB, GB, TB, GiB, Mbps, s, min, hours, days")
    raise ValueError("use numbers, units and + - * / ** ( ) only")


def calculate(expression: str) -> str:
    """The result as text, or "error: …" the model can act on (it sees it as the tool's reply)."""
    expr = _THOUSANDS.sub("", (expression or "").strip())
    if not expr or len(expr) > MAX_EXPRESSION_CHARS:
        return "error: give one short arithmetic expression"
    expr = _TARGET.sub(lambda m: f") / {m[1]}", f"({expr}") if _TARGET.search(expr) else expr
    expr = _QUANTITY.sub(r"(\1*\2)", expr)       # an unknown unit then fails by name, not as a syntax error
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
