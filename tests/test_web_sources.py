"""Web answers: honest source dates, the model's search words, no talk of "passages" (src/tools/web_search.py,
ISSUES_LOG O47 and O49)."""

from types import SimpleNamespace

from src.tools import web_search as ws

CFG = SimpleNamespace(web_search_url="http://127.0.0.1:8888")


def test_a_result_date_is_kept_as_a_day_and_a_bad_one_is_dropped():
    assert ws._day("2026-11-10T14:03:00Z") == "2026-11-10"
    assert ws._day("2026-11-10") == "2026-11-10"
    assert ws._day("") is None and ws._day(None) is None and ws._day("last Tuesday") is None


def test_search_keeps_the_published_date(monkeypatch):
    monkeypatch.setattr(ws, "_results", lambda q, c, category=None: [
        {"url": "https://a.test/1", "title": "A", "publishedDate": "2026-11-09T08:00:00"},
        {"url": "https://b.test/2", "title": "B"},
    ])
    assert [h.published for h in ws.search("q", CFG)] == ["2026-11-09", None]


def test_a_source_row_shows_the_page_date_never_the_fetch_date():
    dated = ws.web_source(1, ws.Passage("T", "https://a.test/x", "", "text", "2026-11-09"), {1})
    undated = ws.web_source(2, ws.Passage("T", "https://a.test/y", "", "text"), {1})
    assert dated["updated_at"] == "2026-11-09" and dated["cited"]
    assert undated["updated_at"] is None and not undated["cited"]


def _answer(monkeypatch, **kwargs) -> tuple[dict, str, list[dict]]:
    seen: dict = {}

    def fake_search(query, config, news=False):
        seen["search"] = query
        return [ws.Hit("Page", "https://a.test/p", "snippet", "bing", "2026-11-09")]

    def fake_gather(query, hits, config):
        seen["rank"] = query
        return [ws.Passage("Page", "https://a.test/p", "", "the facts", hits[0].published)]

    def fake_chat(system, user, model, temperature):
        seen["system"], seen["user"] = system, user
        return "An answer [1]."

    monkeypatch.setattr(ws, "search", fake_search)
    monkeypatch.setattr(ws, "gather", fake_gather)
    text, sources = ws.answer_from_web("tell me about the controvery?", CFG, chat=fake_chat, model="m",
                                       now="now", **kwargs)
    return seen, text, sources


def test_the_model_search_words_find_and_rank_the_pages(monkeypatch):
    seen, _text, sources = _answer(monkeypatch, search_query="Jensen Huang controversy")
    assert seen["search"] == seen["rank"] == "Jensen Huang controversy"
    assert "tell me about the controvery?" in seen["user"]          # the model still answers the question as asked
    assert "(published 2026-11-09)" in seen["user"] and sources[0]["updated_at"] == "2026-11-09"


def test_without_search_words_the_question_is_searched(monkeypatch):
    seen, _text, _sources = _answer(monkeypatch, search_query="  ")
    assert seen["search"] == "tell me about the controvery?"


def test_the_prompt_speaks_of_sources_not_passages(monkeypatch):
    seen, _text, _sources = _answer(monkeypatch)
    assert "passages" not in seen["user"].lower()
    assert "numbered web passages" not in seen["system"] and "according to" in seen["system"]


def _hits(n: int) -> list:
    return [ws.Hit(f"r{i}", f"https://site{i}.test/p", f"snippet {i}", "e") for i in range(n)]


def test_an_unreadable_page_frees_its_slot_for_the_next_result(monkeypatch):
    # the first 4 results refuse page readers (as Reddit, X, Facebook did); result 5 is the official page
    def read(hit):
        return [] if hit.title in {"r0", "r1", "r2", "r3"} else [ws.Passage(hit.title, hit.url, "", "page text")]

    monkeypatch.setattr(ws, "_read", read)
    read_pages = ws.read_pages(_hits(ws.MAX_RESULTS))
    assert "https://site4.test/p" in read_pages
    assert len(read_pages) == ws.MAX_RESULTS - ws.MAX_PAGES      # every remaining result got its turn


def test_reading_stops_once_enough_pages_are_read(monkeypatch):
    tried = []

    def read(hit):
        tried.append(hit.url)
        return [ws.Passage(hit.title, hit.url, "", "page text")]

    monkeypatch.setattr(ws, "_read", read)
    assert len(ws.read_pages(_hits(ws.MAX_RESULTS))) == ws.MAX_PAGES
    assert len(tried) == ws.MAX_PAGES                             # no page beyond the first ones was opened


def test_unread_results_still_count_by_their_snippet(monkeypatch):
    monkeypatch.setattr(ws, "_read", lambda hit: [])
    passages = ws.gather("snippet", _hits(3), CFG)
    assert {p.text for p in passages} <= {"snippet 0", "snippet 1", "snippet 2"} and passages
