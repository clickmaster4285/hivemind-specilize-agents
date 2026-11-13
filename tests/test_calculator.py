"""The calculate tool (src/tools/calculator.py, ISSUES_LOG O51): exact arithmetic, nothing else runs."""

from __future__ import annotations

import pytest

from src.tools.calculator import CALCULATE, CALCULATE_TOOL, MAX_CALC_ROUNDS, calculate, chat_tools_with_calculator


@pytest.mark.parametrize("expression, result", [
    ("212 * 8 * 1000 / 100", "16960"),          # GTA 6 at 100 Mbps, in seconds
    ("0.15 * 2400", "360"),
    ("2,400 * 0.15", "360"),                    # thousands separators are tolerated
    ("0.1 + 0.2", "0.3"),                       # no float noise
    ("-(3 - 5) ** 2", "-4"),
    ("17 // 5 + 17 % 5", "5"),
])
def test_arithmetic_is_exact(expression, result):
    assert calculate(expression) == result


@pytest.mark.parametrize("expression", [
    "__import__('os').system('id')", "open('x')", "a + 1", "(1).real", "[1, 2]", "9 ** 9 ** 9", "1 / 0", "", "True + 1",
])
def test_anything_but_arithmetic_is_refused(expression):
    assert calculate(expression).startswith("error:")


class FakeClient:
    """Replies in turn; records the messages each call saw."""

    def __init__(self, replies):
        self.replies, self.seen = list(replies), []

    def chat_tools(self, *, messages, tools, **_options):
        self.seen.append((list(messages), [t["function"]["name"] for t in tools]))
        return self.replies.pop(0)


def _sum(expression):
    return {"name": CALCULATE, "arguments": {"expression": expression}}


def test_a_calculation_is_worked_out_and_handed_back():
    client = FakeClient([("", [_sum("212 * 8 * 1000 / 100")]), ("About 4 h 43 min (16960 s).", [])])
    text, calls = chat_tools_with_calculator(client, messages=[{"role": "user", "content": "q"}],
                                             tools=[CALCULATE_TOOL])
    assert text == "About 4 h 43 min (16960 s)." and calls == []
    second_call_messages = client.seen[1][0]
    assert second_call_messages[-1] == {"role": "tool", "content": "16960"}
    assert second_call_messages[-2]["tool_calls"][0]["function"]["name"] == CALCULATE


def test_another_tool_goes_back_to_the_caller():
    web = {"name": "web_search", "arguments": {"query": "GTA 6 size"}}
    client = FakeClient([("", [web])])
    assert chat_tools_with_calculator(client, messages=[], tools=[CALCULATE_TOOL]) == ("", [web])


def test_endless_calculating_must_end_in_an_answer():
    client = FakeClient([("", [_sum("1 + 1")])] * MAX_CALC_ROUNDS + [("Two.", [])])
    text, _calls = chat_tools_with_calculator(client, messages=[], tools=[CALCULATE_TOOL])
    assert text == "Two." and client.seen[-1][1] == []                 # the last call has no calculate tool
