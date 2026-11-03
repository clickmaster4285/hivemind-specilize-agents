"""L15 P6: the search market — the user's words, else the website, else ask (src/tools/market.py) — and page text
without menus and footers (ISSUES_LOG O14). No internet, no model."""

from __future__ import annotations

from src.core.config import Config
from src.tools import compare_sites, market
from src.tools.market import Market, ask_market, country_code, from_domain, from_locale, from_page, requested
from src.tools.site_audit import analyze_page


def test_country_codes_and_names():
    assert country_code("PK") == country_code("pk") == country_code("Pakistan") == "PK"
    assert country_code("worldwide") == ""
    assert country_code("Narnia") is None and country_code("") is None
    assert country_code("EU") is None                      # a region, not a country: no search market


def test_what_the_user_asked_for():
    assert requested("GB", "best laptop") == Market("GB", "en", "you asked")
    assert requested("worldwide").search_language == "all" and requested("worldwide").label == "Worldwide"
    assert requested("") is None and requested(None) is None


def test_the_search_language_follows_the_keywords_script():
    assert requested("PK", "best laptop price").search_language == "en-PK"     # Latin script → English
    assert requested("PK", "لیپ ٹاپ قیمت").search_language == "ur-PK"         # Arabic script → Urdu
    assert requested("IN", "best laptop").search_language == "en-IN"
    assert requested("DE", "beste laptop").search_language == "de-DE"


def test_a_declared_locale_and_a_country_domain():
    assert from_locale("en-PK") == Market("PK", "en", "the site declares en-PK")
    assert from_locale("en_GB").search_language == "en-GB"
    assert from_locale("en") is None                       # a language alone says nothing about the country
    assert from_domain("shop.example.pk").country == "PK"
    assert from_domain("www.example.co.uk") == Market("GB", "en", "the site's .uk domain")
    assert from_domain("example.com") is None and from_domain("startup.io") is None


def test_a_page_says_where_it_is_for():
    declared = analyze_page('<html lang="en"><head><meta property="og:locale" content="en_PK"></head>'
                            "<body><p>Hello</p></body></html>", "https://shop.example.com/")
    assert declared.locale == "en_PK" and from_page(declared).country == "PK"
    own = analyze_page('<html><head><link rel="alternate" hreflang="en-gb" href="https://ex.com/">'
                       '<link rel="alternate" hreflang="de-de" href="https://ex.com/de/"></head><body></body></html>',
                       "https://ex.com/")
    assert own.locale == "en-gb"                           # its own alternate, not the German one
    by_domain = analyze_page('<html lang="ur"><body><p>قیمت</p></body></html>', "https://example.pk/")
    assert from_page(by_domain) == Market("PK", "ur", "the site's .pk domain")
    assert from_page(analyze_page("<html><body>Hi</body></html>", "https://example.com/")) is None
    empty = analyze_page("<html><body></body></html>", "https://example.pk/")        # a JavaScript-only shop
    assert from_page(empty, "laptop price").search_language == "en-PK"              # the keyword's script decides


def test_ask_offers_the_users_own_country_first(monkeypatch):
    monkeypatch.setattr(market, "current_time_zone", lambda: "Europe/London")
    assert ask_market("who ranks")[0]["options"] == ["United Kingdom", "Worldwide"]
    monkeypatch.setattr(market, "current_time_zone", lambda: None)
    assert ask_market("who ranks")[0]["options"] == ["Worldwide"]


def test_page_text_leaves_out_menus_header_and_footer():
    page = analyze_page("""<html><body>
        <header><a href="/">Shop Home</a></header><nav>Solar Panels Batteries Contact</nav>
        <div role="navigation">Account Basket</div>
        <article><header><h1>Panel prices</h1></header><p>Prices fell this year.</p><footer>By Ali</footer></article>
        <aside>Popular posts</aside><footer>Copyright Example Shop</footer></body></html>""", "https://ex.com/")
    assert page.text == "Panel prices Prices fell this year. By Ali"
    assert page.h1 == 1 and [link.text for link in page.links] == ["Shop Home"]     # links are still seen


def test_compare_by_keyword_asks_when_the_site_does_not_say(monkeypatch):
    events: list[dict] = []
    monkeypatch.setattr(compare_sites, "for_site", lambda url, keyword: None)
    monkeypatch.setattr(compare_sites, "serp", lambda *a, **k: (_ for _ in ()).throw(AssertionError("searched")))
    output, errors, _ = compare_sites.run_compare_sites("compare me.com with who ranks for solar", job_id="t-mk",
                                                        config=Config.from_env(), url="me.com", keyword="solar",
                                                        on_progress=events.append)
    done = next(e for e in events if e["type"] == "agent_done")
    assert not errors and done["clarify"] and "which market" in output
