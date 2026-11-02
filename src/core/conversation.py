"""Normalize prior chat turns for follow-up jobs (e.g. “write that as a docx”).

A turn is {"role": "user" | "assistant" | "summary", "content": ...}. A "summary" turn (BACKLOG L12) stands
for the earlier part of a long chat that was folded into a running summary; it always leads and is never
trimmed away.
"""

from __future__ import annotations

from typing import Any

MAX_TURNS = 12
MAX_CHARS_PER_TURN = 6000
MAX_TOTAL_CHARS = 24000
SUMMARY_ROLE = "summary"
CHARS_PER_TOKEN = 4          # rough size of a token for English/Hinglish text; used to budget, never billed


def estimate_tokens(text: str) -> int:
    return (len(text or "") + CHARS_PER_TOKEN - 1) // CHARS_PER_TOKEN


def split_summary(turns: list[dict[str, str]]) -> tuple[dict[str, str] | None, list[dict[str, str]]]:
    """→ (the leading summary turn or None, the user/assistant turns)."""
    if turns and turns[0].get("role") == SUMMARY_ROLE:
        return turns[0], turns[1:]
    return None, turns


def normalize_conversation(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    summary = None
    if raw and isinstance(raw[0], dict) and raw[0].get("role") == SUMMARY_ROLE and str(raw[0].get("content") or "").strip():
        summary = {"role": SUMMARY_ROLE, "content": str(raw[0]["content"]).strip()}
        raw = raw[1:]
    out: list[dict[str, str]] = []
    for row in raw[-MAX_TURNS:]:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        if role in {"assistant", "agent", "model"}:
            role = "assistant"
        elif role != "user":
            continue
        content = str(row.get("content") or row.get("body") or "").strip()
        if not content:
            continue
        if len(content) > MAX_CHARS_PER_TURN:
            content = content[: MAX_CHARS_PER_TURN - 1] + "…"
        out.append({"role": role, "content": content})
    # Cap total size from the end (keep most recent)
    total = 0
    kept: list[dict[str, str]] = []
    for turn in reversed(out):
        total += len(turn["content"])
        if total > MAX_TOTAL_CHARS and kept:
            break
        kept.append(turn)
    kept.reverse()
    return ([summary] if summary else []) + kept


def format_conversation_block(turns: list[dict[str, str]]) -> str:
    if not turns:
        return ""
    lines = ["## Prior conversation", "Use this context for the current request (e.g. export/summarize prior answers)."]
    for turn in turns:
        label = {"user": "User", SUMMARY_ROLE: "Earlier in this chat (summary)"}.get(turn["role"], "Assistant")
        lines.append(f"### {label}\n{turn['content']}")
    return "\n\n".join(lines)
