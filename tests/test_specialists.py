"""L2 specialists: the store, the picker's parsing, tool permissions, collections-first search (no LLM)."""

from __future__ import annotations

import os
from dataclasses import replace

import pytest

from src import specialists as sp
from src.assistant import messages_for, speaker, tool_schemas
from src.brain.answer import answer_question
from src.brain.retrieve import SearchResult
from src.config import Config

CFG = replace(Config.from_env(), brain_enabled=True, network_scan_enabled=True)


@pytest.fixture
def store(tmp_path):
    return sp.SpecialistStore(tmp_path / "specialists.json")


def test_defaults_it_dev_marketing_and_network_scan_is_it_only(store):
    by_id = {s.id: s for s in store.load_all()}
    assert list(by_id) == ["it", "development", "marketing"]
    assert "network_scan" in by_id["it"].tools
    assert "network_scan" not in by_id["development"].tools + by_id["marketing"].tools
    assert by_id["it"].collections == ["it", "general"]


def test_store_create_update_delete_and_validation(store):
    s = store.create({"name": "Finance", "covers": "accounting, invoices, tax", "instructions": "- Be exact.",
                      "tools": ["web_search", "not_a_tool"], "collections": ["general", "nope"]})
    assert s.id == "finance" and s.tools == ["web_search"] and s.collections == ["general"]
    assert store.create({"name": "Finance", "covers": "x"}).id.startswith("finance-")   # no duplicate ids
    assert store.update("finance", {"enabled": False}).enabled is False
    assert "finance" not in [x.id for x in store.enabled()]
    with pytest.raises(ValueError):
        store.create({"name": "No covers"})
    with pytest.raises(ValueError):
        store.update("it", {"name": ""})
    store.delete("finance")
    with pytest.raises(KeyError):
        store.delete("finance")
    assert [x.id for x in store.reset_defaults()] == ["it", "development", "marketing"]


def test_general_auto_none_are_reserved_ids(store):
    assert store.create({"name": "General", "covers": "everything"}).id.startswith("general-")


def test_parse_pick():
    choices = sp.default_specialists()
    assert sp.parse_pick("it", choices).id == "it"
    assert sp.parse_pick(" Development.", choices).id == "development"
    assert sp.parse_pick("marketing\n", choices).id == "marketing"
    assert sp.parse_pick("general", choices) is None
    assert sp.parse_pick("", choices) is None
    assert sp.parse_pick("I think IT", choices) is None          # only the first word counts


def test_pick_prompt_lists_every_specialist_and_general():
    prompt = sp.pick_prompt(sp.default_specialists())
    assert "- it: IT —" in prompt and "- marketing: Marketing —" in prompt
    assert prompt.rstrip().endswith("it, development, marketing, general.")


def test_resolve_pinned_none_and_auto(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "STORE_FILE", tmp_path / "s.json")
    calls = []
    monkeypatch.setattr(sp, "pick", lambda q, **kw: calls.append(q) or kw["specialists"][2])
    assert sp.resolve("development", "q", config=CFG)[0].id == "development"
    assert sp.resolve("none", "q", config=CFG) == (None, "none")
    chosen, how = sp.resolve("auto", "write a slogan", config=CFG)
    assert (chosen.id, how) == ("marketing", "auto") and calls == ["write a slogan"]
    assert sp.resolve("deleted-one", "q2", config=CFG)[1] == "auto"   # unknown pin → picker


def test_specialist_limits_tools_and_joins_the_prompt():
    marketing = next(s for s in sp.default_specialists() if s.id == "marketing")
    names = [t["function"]["name"] for t in tool_schemas(CFG, marketing.tools)]
    assert "network_scan" not in names and "about_multeagent" in names
    every = [t["function"]["name"] for t in tool_schemas(CFG)]
    assert "network_scan" in every
    system = messages_for("hi", CFG, None, CFG.llm_model, marketing)[0]["content"]
    assert "Marketing specialist" in system and "2-3 variants" in system
    assert speaker(marketing) == {"name": "Marketing specialist", "accent": marketing.accent}
    assert speaker(None)["name"] == "Assistant"


def _result(enough: bool) -> SearchResult:
    return SearchResult(query="q", mode="hybrid", passages=[], enough_evidence=enough, best_similarity=0.1,
                        exact_terms=[], exact_match=False)


def test_collections_first_then_the_whole_knowledge_base(monkeypatch):
    monkeypatch.setattr("src.brain.answer._log_retrieval", lambda *a, **k: None)
    seen = []

    def searcher(text, *, config, account_id, collections):
        seen.append(collections)
        return _result(False)

    answer_question("which ports does DNS use", config=CFG, collections=["it", "general"],
                    chat=lambda **kw: "general answer", searcher=searcher)
    assert seen == [["it", "general"], None]

    seen.clear()
    answer_question("which ports does DNS use", config=CFG, chat=lambda **kw: "x", searcher=searcher)
    assert seen == [None]                                 # no specialist: one search, as before


def test_specialist_block_reaches_the_answer_prompt(monkeypatch):
    monkeypatch.setattr("src.brain.answer._log_retrieval", lambda *a, **k: None)
    prompts = []
    answer_question("q about dns", config=CFG, specialist_block="You answer as MulteAgent's IT specialist.",
                    chat=lambda **kw: prompts.append(kw["system"]) or "x",
                    searcher=lambda *a, **k: _result(False))
    assert "IT specialist" in prompts[0] and "grounding rules below always come first" in prompts[0]


@pytest.mark.skipif(os.getenv("RUN_LLM_EVAL") != "1", reason="real model: RUN_LLM_EVAL=1")
def test_picker_eval_set():
    result = sp.evaluate()
    wrong = [(r["text"], r["want"], r["got"]) for r in result["rows"] if not r["ok"]]
    assert result["right"] >= result["total"] - 2, wrong
