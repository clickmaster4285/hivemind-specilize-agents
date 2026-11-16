"""The diagram rules are joined into prompts that go through str.format: no braces, and the examples stay."""

from __future__ import annotations

from src.llm.diagram_rules import DIAGRAM_RULES


def test_the_rules_survive_str_format_and_keep_each_diagram_kind():
    assert DIAGRAM_RULES.format() == DIAGRAM_RULES          # no { } that format would eat or choke on
    for kind in ("flowchart TD", "architecture-beta", "usecase-beta"):
        assert f"```mermaid\n{kind}" in DIAGRAM_RULES, kind
