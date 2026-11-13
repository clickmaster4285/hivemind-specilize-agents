"""News questions take SearXNG's general and news results in turn (src/tools/web_search.py, O47)."""

from types import SimpleNamespace

from src.llm.assistant import tool_flag
from src.tools import web_search as ws

CFG = SimpleNamespace(web_search_url="http://127.0.0.1:8888")


def _fake(monkeypatch, news_count: int = ws.MAX_RESULTS, news_fails: bool = False) -> list:
    asked = []

    def results(query, config, category=None):
        asked.append(category)
        if category == "news":
            if news_fails:
                raise ws.WebSearchError("news engines down")
            return [{"url": f"https://news.test/{i}", "title": f"n{i}"} for i in range(news_count)]
        return [{"url": f"https://web.test/{i}", "title": f"g{i}"} for i in range(ws.MAX_RESULTS)]

    monkeypatch.setattr(ws, "_results", results)
    return asked


def test_a_plain_question_asks_the_general_results_only(monkeypatch):
    asked = _fake(monkeypatch)
    hits = ws.search("q", CFG)
    assert asked == [None] and all(h.url.startswith("https://web.test/") for h in hits)


def test_a_news_question_takes_general_and_news_in_turn(monkeypatch):
    _fake(monkeypatch)
    hits = ws.search("q", CFG, news=True)
    assert [h.url for h in hits[:4]] == ["https://web.test/0", "https://news.test/0",
                                         "https://web.test/1", "https://news.test/1"]
    assert len(hits) == ws.MAX_RESULTS


def test_few_news_results_leave_the_rest_to_general(monkeypatch):
    _fake(monkeypatch, news_count=1)
    urls = [h.url for h in ws.search("q", CFG, news=True)]
    assert urls[:3] == ["https://web.test/0", "https://news.test/0", "https://web.test/1"]
    assert len(urls) == ws.MAX_RESULTS


def test_a_failed_news_search_falls_back_to_general(monkeypatch):
    _fake(monkeypatch, news_fails=True)
    assert all(h.url.startswith("https://web.test/") for h in ws.search("q", CFG, news=True))


def test_the_news_argument_is_yes_only():
    assert tool_flag({"news": "yes"}, "news") and tool_flag({"news": " Yes "}, "news")
    assert not tool_flag({"news": ""}, "news") and not tool_flag({}, "news") and not tool_flag({"news": "no"}, "news")
