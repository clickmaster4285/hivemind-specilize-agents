"""Skills learned from tasks (BACKLOG L26 H2, idea from Hermes Agent): "Save as skill" under an answer.

The model reads what one finished job did — the question, the tools it used with their arguments, the answer — and
drafts a reusable skill from it: a /name, when to use it, the steps and the shape of the answer, and what the user
must give next time (the specifics of this one request become `inputs`). The draft is NOT saved: it opens in the
skill editor, filled in, and only the user's Save stores it (skills.create, with all its checks).
"""

from __future__ import annotations

import json
import re
from typing import Any

from src.core.config import Config
from src.llm.skills import NAME_MAX, Skill, SkillError, check, instruction_budget
from src.llm.specialists import tool_names

ANSWER_CHARS = 3000            # the answer as the model sees it: its shape, not every word
ARG_CHARS = 200
MAX_STEPS = 12
DRAFT_MAX_TOKENS = 900
DRAFT_SYSTEM = """You turn one finished task into a reusable SKILL for an assistant: saved instructions the user can \
run again later with /name for a similar request.

Write it for the NEXT time, not this one:
- Generalise: the specifics of this request (addresses, names, dates, figures, the server or company) are NOT in the \
skill; list what the user must give next time as "inputs" (short phrases, e.g. "the device's IP address").
- "instructions": Markdown, at most about 250 words — the numbered steps the assistant should follow (name the tool \
when one was used, e.g. "Run security_check on the address"), then "## Answer format" describing the shape of a good \
answer (sections, table columns, length) as this answer had it.
- "description": one or two sentences — what the skill does and when to use it.
- "title": 2–5 words.
- Never copy a password, key, token or personal detail into the skill.

Reply with JSON only: {"title": "...", "description": "...", "instructions": "...", "inputs": ["..."]}"""


class DraftError(RuntimeError):
    """Nothing usable to draft from, or the model's reply could not be read."""


def trace(query: str, events: list[dict[str, Any]]) -> dict[str, Any]:
    """A job's saved events → {question, steps: [(tool, args)], answer}."""
    steps: list[tuple[str, dict]] = []
    answer = ""
    for e in events:
        kind = e.get("type")
        if kind in ("tool_chosen", "tool_call") and e.get("tool"):
            args = e.get("args") if isinstance(e.get("args"), dict) else {}
            steps.append((str(e["tool"]), {k: str(v)[:ARG_CHARS] for k, v in args.items() if k != "user_id"}))
        elif kind == "agent_done" and e.get("output"):
            answer = str(e["output"])          # the last one is the final answer
    return {"question": query.strip(), "steps": steps[:MAX_STEPS], "answer": answer}


def _prompt(t: dict[str, Any]) -> str:
    steps = "\n".join(f"{i}. {tool}({json.dumps(args, ensure_ascii=False)})" for i, (tool, args)
                      in enumerate(t["steps"], start=1)) or "none — answered directly"
    answer = t["answer"][:ANSWER_CHARS] + (" …" if len(t["answer"]) > ANSWER_CHARS else "")
    return f"The user asked:\n{t['question']}\n\nTools used:\n{steps}\n\nThe answer given:\n{answer}"


def _reply_json(raw: str) -> dict[str, Any]:
    text = re.sub(r"<think>.*?</think>", "", raw or "", flags=re.S)
    match = re.search(r"\{.*\}", text, re.S)
    try:
        data = json.loads(match.group(0)) if match else None
    except json.JSONDecodeError:
        data = None
    if not isinstance(data, dict):
        raise DraftError("the model's draft could not be read — try again")
    return data


def slug(title: str) -> str:
    """"Monthly SEO check" → "monthly-seo-check" (as the skill editor makes it)."""
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:NAME_MAX].strip("-")


def draft(config: Config, query: str, events: list[dict[str, Any]], *, specialist: str = "",
          chat=None) -> Skill:
    """One job → a checked, unsaved skill draft."""
    t = trace(query, events)
    if not t["question"] or not t["answer"]:
        raise DraftError("this answer has no finished task to learn from")
    if chat is None:
        from src.llm.llm_client import LLMClient

        chat = LLMClient(config).chat
    raw = chat(system=DRAFT_SYSTEM, user=_prompt(t), model=config.model_fast or config.llm_model,
               max_tokens=DRAFT_MAX_TOKENS, temperature=0.2, max_retries=0)
    data = _reply_json(raw)
    title = " ".join(str(data.get("title") or "").split())[:120] or t["question"][:40]
    inputs = data.get("inputs") if isinstance(data.get("inputs"), list) else []
    known = set(tool_names())
    skill = Skill(name=slug(title) or "my-skill", title=title,
                  description=str(data.get("description") or "").strip(),
                  instructions=str(data.get("instructions") or "").strip(),
                  specialist=specialist if specialist not in ("", "auto") else "",
                  tools=[tool for tool, _ in t["steps"] if tool in known],
                  inputs=[" ".join(str(i).split())[:120] for i in inputs if str(i).strip()][:8])
    try:
        return check(skill, budget=instruction_budget(config))
    except SkillError as exc:
        raise DraftError(f"the draft needs work before it can be a skill: {exc}") from exc
