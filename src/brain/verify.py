"""Check a grounded answer against its sources, claim by claim — BACKLOG L5c (doc/ISSUES_LOG.md #38).

Used twice: in the chat, right after a knowledge-base answer is written (src/chat_runner.py: the user
reads the answer while it is checked, then sees "✓ 6 of 7 statements confirmed"), and by the B6 test
(src/brain/answer_eval.py). One model call per answer, temperature 0:

  split_claims     the answer's cited sentences (a sentence without [n] inherits its line's citations)
  judge            the model must first QUOTE the source words behind each claim, then say yes/partly/no
  apply_judgement  the quote is looked up in the cited passage: "yes" without a real quote → partly,
                   "no" although the judge quoted real supporting text → unsure (a person decides)
"""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any

from src.brain.command_check import NOTE_PREFIX
from src.brain.retrieve import Passage

MIN_CLAIM_WORDS = 4

JUDGE_TEMPERATURE = 0.0

JUDGE_SYSTEM = """You check whether an answer is faithful to its sources.
You get numbered sources and numbered claims from the answer; each claim lists the sources it cites.
For each claim, FIRST copy the words from its cited sources that support it (an exact quote, up to
about 25 words), THEN decide:
  yes    — the quoted words support the whole claim (same meaning; a short summary or a code example
           that follows the source counts as supported)
  partly — some of the claim is supported, some is added or changed
  no     — the cited sources do not say this, or say something different
Judge meaning, not wording: claims may be in Hinglish (Roman-script Hindi mixed with English).
Ignore style and formatting. Use only the cited sources, never your own knowledge.
Reply with exactly one line per claim and nothing else, in this form:
1: "exact words from the source" => yes
2: - => no
(write - instead of a quote when nothing in the cited sources supports the claim)"""

JUDGE_LINE_RE = re.compile(
    r"^\s*(\d{1,3})\s*[:.)]\s*(?:\"(?P<quote>.*?)\"|“(?P<quote2>.*?)”|-)?\s*=?>?\s*"
    r"(?P<verdict>yes|partly|partial(?:ly)?|no)\b", re.I | re.M)

QUOTE_MATCH = 0.85          # share of the quote that must appear in the passage (tolerates small slips)

CITE_RE = re.compile(r"\[(\d{1,2}(?:\s*,\s*\d{1,2})*)\]")

@dataclass
class Claim:
    text: str
    cites: list[int]
    verdict: str | None = None          # yes | partly | no | unsure (None = not judged)
    quote: str = ""                     # the judge's evidence from the cited sources
    quote_found: bool | None = None     # the quote really is in a cited passage (None = no quote given)

def _cites(text: str) -> list[int]:
    return sorted({int(n) for group in CITE_RE.findall(text) for n in group.split(",")})

# code brackets are hidden while citations are read, then put back in the claim text
_HIDE_BRACKETS = str.maketrans("[]", "\x01\x02")

_SHOW_BRACKETS = str.maketrans("\x01\x02", "[]")

def _plain(line: str) -> str:
    """Markdown markers off a line: list bullets, numbering, bold, inline code ticks.
    Brackets inside inline code are code, not citations (`[1, 2, 3]` is a list)."""
    line = re.sub(r"`([^`]*)`", lambda m: m.group(1).translate(_HIDE_BRACKETS), line)
    line = re.sub(r"^\s*(?:[-*+>]|\d+[.)])\s+", "", line)
    return line.replace("**", "").strip()

def split_claims(text: str) -> tuple[list[Claim], int]:
    """(cited claims, number of uncited factual-looking sentences).

    Code blocks are skipped (they are copied commands, judged through the sentence that cites them).
    A sentence without its own [n] inherits the citations of its line — models often cite once per bullet —
    and a line that is only citations ("[1][2]" under a code block) backs the uncited sentences above it.
    """
    text = re.sub(r"```.*?```", "", text, flags=re.S)
    claims: list[Claim] = []
    pending: list[str] = []             # uncited sentences since the last heading
    uncited = 0
    for raw in text.splitlines():
        line = _plain(raw)
        if raw.lstrip().startswith("#"):
            uncited += len(pending)
            pending = []
        if not line or raw.lstrip().startswith(("#", "|")):
            continue
        line_cites = _cites(line)
        if line_cites and not CITE_RE.sub("", line).strip(" .,;"):
            claims += [Claim(text=s.translate(_SHOW_BRACKETS), cites=line_cites) for s in pending]
            pending = []
            continue
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", line):
            sentence = sentence.strip()
            if len(re.findall(r"\w+", CITE_RE.sub("", sentence))) < MIN_CLAIM_WORDS or sentence.endswith(":"):
                continue
            cites = _cites(sentence) or line_cites
            if cites:
                claims.append(Claim(text=CITE_RE.sub("", sentence).strip().translate(_SHOW_BRACKETS), cites=cites))
            else:
                pending.append(sentence)
    return claims, uncited + len(pending)

def judge_prompt(claims: list[Claim], passages: list[Passage]) -> str:
    """Only the passages the claims cite, under their original numbers (less to read = fewer slips)."""
    used = sorted({n for c in claims for n in c.cites if 1 <= n <= len(passages)})
    sources = "\n\n".join(
        f'<source id="{n}">\n[{n}] {passages[n - 1].title}'
        + (f"\nSection: {passages[n - 1].heading_path}" if passages[n - 1].heading_path else "")
        + f"\n\n{passages[n - 1].content}\n</source>"
        for n in used
    )
    numbered = "\n".join(
        f"{i}. {c.text}  (cites {', '.join(f'[{n}]' for n in c.cites)})" for i, c in enumerate(claims, 1)
    )
    return f"## Sources\n\n{sources}\n\n## Claims\n\n{numbered}"

def parse_judgement(reply: str, count: int) -> list[tuple[str | None, str]]:
    """[(verdict, quote)] per claim; (None, "") for a claim the judge skipped."""
    found: dict[int, tuple[str, str]] = {}
    for m in JUDGE_LINE_RE.finditer(reply):
        word = m.group("verdict").lower()
        quote = (m.group("quote") or m.group("quote2") or "").strip()
        found.setdefault(int(m.group(1)), ("partly" if word.startswith("part") else word, quote))
    return [found.get(i, (None, "")) for i in range(1, count + 1)]

def parse_verdicts(reply: str, count: int) -> list[str | None]:
    return [verdict for verdict, _ in parse_judgement(reply, count)]

def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*_`#>|]", " ", text)).strip().lower()

def _words(text: str) -> list[str]:
    return re.findall(r"\w+", _norm(text))

def _covered(quote_words: list[str], text_words: list[str]) -> float:
    """Share of the quote's words found in the text in runs of 3+ words in a row (single common words
    scattered over a long passage do not count)."""
    blocks = SequenceMatcher(None, quote_words, text_words, autojunk=False).get_matching_blocks()
    return sum(b.size for b in blocks if b.size >= 3) / len(quote_words)

def quote_in(quote: str, texts: list[str]) -> bool:
    """The quote really comes from one of the texts.

    Judges join pieces with "..." and change a word here and there, so each piece of 3+ words must have
    ≥ 85% of its words in the text, in runs of 3+ words. A sentence the judge made up — e.g. the claim
    itself, reworded — does not pass (B6 run 2026-10-19: en-nrpt)."""
    pieces = [w for w in (_words(p) for p in re.split(r"\.\.\.|…", quote)) if len(w) >= 3]
    if not pieces:
        return False
    for text in texts:
        words = _words(text)
        if words and all(_covered(p, words) >= QUOTE_MATCH for p in pieces):
            return True
    return False

def apply_judgement(claims: list[Claim], reply: str, passages: list[Passage]) -> None:
    """Verdicts onto the claims, checked against the judge's own evidence:
    "yes" whose quote is not in the cited passages → "partly" (support not shown);
    "no" although the judge quoted real supporting text → "unsure" (it contradicts itself — a person
    decides; B6 run 2026-10-19 had 4 of these, e.g. `Set-DnsServerForwarder -Timeout`)."""
    for claim, (verdict, quote) in zip(claims, parse_judgement(reply, len(claims))):
        claim.verdict, claim.quote = verdict, quote
        if quote:
            claim.quote_found = quote_in(quote, [passages[n - 1].content for n in claim.cites])
        if verdict == "yes" and not claim.quote_found:
            claim.verdict = "partly"
        elif verdict == "no" and claim.quote_found:
            claim.verdict = "unsure"


def verify_answer(text: str, passages: list[Passage], chat: Callable[..., str]) -> dict[str, Any] | None:
    """Check an answer's cited claims; None when it has none (nothing to check).

    chat = a non-streaming chat function (system=, user=, temperature=) — the judge's reply must never
    stream into the answer bubble. Returns counts plus the sentences that were not confirmed."""
    body = "\n".join(line for line in text.splitlines() if not line.startswith(NOTE_PREFIX))
    claims, _ = split_claims(body)
    claims = [c for c in claims if all(1 <= n <= len(passages) for n in c.cites)]
    if not claims:
        return None
    started = time.perf_counter()
    reply = chat(system=JUDGE_SYSTEM, user=judge_prompt(claims, passages), temperature=JUDGE_TEMPERATURE)
    apply_judgement(claims, reply, passages)
    supported = [c for c in claims if c.verdict == "yes"]
    unsupported = [c for c in claims if c.verdict == "no"]
    unclear = [c for c in claims if c.verdict not in ("yes", "no")]
    return {
        "checked": len(claims),
        "supported": len(supported),
        "unsupported": [{"text": c.text, "cites": c.cites} for c in unsupported],
        "unclear": [{"text": c.text, "cites": c.cites, "verdict": c.verdict or "not judged"} for c in unclear],
        "seconds": round(time.perf_counter() - started, 1),
        "claims": [asdict(c) for c in claims],
    }
