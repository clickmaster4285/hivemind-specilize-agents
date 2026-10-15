"""Heading-aware chunking (doc/BRAIN_DESIGN.md §4 step 5).

1. Split the Markdown into sections at headings; each section remembers its heading path
   ("DNS zones > Zone types > Primary zone").
2. Inside a section, pack whole blocks (paragraph / list / table / code block) into chunks of
   about TARGET_TOKENS, never more than MAX_TOKENS. A code block larger than that stays whole;
   a table larger than that is split by rows, repeating its header row in every part.
3. Consecutive chunks of the same section overlap by ~15% (trailing prose blocks are repeated)
   so a fact on a boundary is still found.
4. Tiny sections are merged into the next one so we do not store "heading + one line" chunks.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

# Bump when chunking output changes: ingestion re-chunks stored documents made by an older version.
CHUNKER_VERSION = 2

TARGET_TOKENS = 450
MAX_TOKENS = 600
MIN_TOKENS = 80
OVERLAP_RATIO = 0.15

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_FENCE = re.compile(r"^\s*(```|~~~)")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass
class Chunk:
    index: int
    heading_path: str
    content: str
    token_count: int


def estimate_tokens(text: str) -> int:
    """~4 characters per token for English technical text; good enough for budgeting."""
    return max(1, math.ceil(len(text) / 4))


def _is_code(block: str) -> bool:
    return bool(_FENCE.match(block.lstrip().split("\n", 1)[0]))


def _is_table(block: str) -> bool:
    return block.lstrip().startswith("|")


def _is_atomic(block: str) -> bool:
    """Code blocks and tables are never cut mid-way (big tables are split only at row borders)."""
    return _is_code(block) or _is_table(block)


def _split_table(block: str) -> list[str]:
    """Split a large Markdown table into parts of whole rows, each starting with the header."""
    lines = block.strip().split("\n")
    has_separator = len(lines) > 1 and set(lines[1].replace("|", "").strip()) <= set("-: ")
    header = lines[:2] if has_separator else lines[:1]
    rows = lines[len(header):]
    parts: list[str] = []
    current: list[str] = []
    for row in rows:
        if current and estimate_tokens("\n".join(header + current + [row])) > TARGET_TOKENS:
            parts.append("\n".join(header + current))
            current = []
        current.append(row)
    if current:
        parts.append("\n".join(header + current))
    return parts or [block]


def _sections(md: str) -> list[tuple[list[str], str]]:
    """[(heading path, body text)] in document order; headings inside code are ignored."""
    sections: list[tuple[list[str], str]] = []
    stack: list[tuple[int, str]] = []
    body: list[str] = []
    in_code = False

    def flush() -> None:
        text = "\n".join(body).strip()
        if text:
            sections.append(([h for _, h in stack], text))
        body.clear()

    for line in md.split("\n"):
        if _FENCE.match(line):
            in_code = not in_code
        heading = None if in_code else _HEADING.match(line)
        if heading:
            flush()
            level = len(heading.group(1))
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading.group(2).strip()))
        else:
            body.append(line)
    flush()
    return sections


def _blocks(text: str) -> list[str]:
    """Split a section body at blank lines, keeping fenced code blocks whole."""
    blocks: list[str] = []
    current: list[str] = []
    in_code = False
    for line in text.split("\n"):
        if _FENCE.match(line):
            in_code = not in_code
        if not in_code and not line.strip():
            if current:
                blocks.append("\n".join(current))
                current = []
            continue
        current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _split_long_prose(block: str) -> list[str]:
    """A single paragraph longer than MAX_TOKENS is cut at sentence ends."""
    parts: list[str] = []
    current = ""
    for sentence in _SENTENCE_END.split(block):
        candidate = f"{current} {sentence}".strip()
        if current and estimate_tokens(candidate) > TARGET_TOKENS:
            parts.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def _tail_sentences(block: str, budget: int) -> str:
    """The last whole sentences of a paragraph that fit in `budget` tokens ('' if none fit)."""
    tail: list[str] = []
    for sentence in reversed(_SENTENCE_END.split(block.strip())):
        if estimate_tokens(" ".join([sentence] + tail)) > budget:
            break
        tail.insert(0, sentence)
    return " ".join(tail)


def _overlap(previous: list[str]) -> list[str]:
    """Trailing prose of the previous chunk to repeat at the start of the next one."""
    budget = int(TARGET_TOKENS * OVERLAP_RATIO)
    carried: list[str] = []
    for prev in reversed(previous):
        if _is_atomic(prev):
            break  # never repeat code or tables
        if estimate_tokens("\n\n".join([prev] + carried)) <= budget:
            carried.insert(0, prev)
            continue
        remaining = budget - (estimate_tokens("\n\n".join(carried)) if carried else 0)
        tail = _tail_sentences(prev, remaining)
        if tail:
            carried.insert(0, tail)
        break
    return carried


def _pack(blocks: list[str]) -> list[str]:
    """Greedy packing of blocks into chunk texts with overlap between neighbours."""
    units: list[str] = []
    for block in blocks:
        if estimate_tokens(block) <= MAX_TOKENS or _is_code(block):
            units.append(block)
        elif _is_table(block):
            units.extend(_split_table(block))
        else:
            units.extend(_split_long_prose(block))

    chunks: list[str] = []
    current: list[str] = []
    for unit in units:
        candidate = "\n\n".join(current + [unit])
        if current and estimate_tokens(candidate) > TARGET_TOKENS:
            chunks.append("\n\n".join(current))
            current = _overlap(current) + [unit]
            if estimate_tokens("\n\n".join(current)) > MAX_TOKENS:
                current = [unit]
        else:
            current.append(unit)
    if current:
        chunks.append("\n\n".join(current))
    return chunks


def chunk_markdown(md: str) -> list[Chunk]:
    """Split clean Markdown into searchable chunks."""
    pending_path: list[str] | None = None
    pending_text = ""
    raw: list[tuple[str, str]] = []

    for path, body in _sections(md):
        if pending_text:
            # merge a tiny previous section into this one, keeping this section's heading visible
            sub = path[-1] if path else ""
            heading = f"{'#' * min(len(path) + 1, 6)} {sub}\n\n" if sub else ""
            body = f"{pending_text}\n\n{heading}{body}"
            path = pending_path if pending_path is not None else path
            pending_text, pending_path = "", None
        if estimate_tokens(body) < MIN_TOKENS:
            pending_text, pending_path = body, path
            continue
        for text in _pack(_blocks(body)):
            raw.append((" > ".join(path), text))

    if pending_text:
        tail_path = " > ".join(pending_path or [])
        if raw and estimate_tokens(raw[-1][1] + pending_text) <= MAX_TOKENS:
            last_path, last_text = raw[-1]
            raw[-1] = (last_path, f"{last_text}\n\n{pending_text}")
        else:
            raw.append((tail_path, pending_text))

    return [
        Chunk(index=i, heading_path=path, content=text, token_count=estimate_tokens(text))
        for i, (path, text) in enumerate(raw)
    ]
