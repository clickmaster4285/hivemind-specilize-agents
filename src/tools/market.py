"""Which search market the SEO tools look at — a country, or worldwide (BACKLOG L15 P6, ISSUES_LOG O14).

Without one, SearXNG searches from its own region (the UK on this machine), so "best laptop price" for a Pakistani
shop was compared with currys.co.uk instead of paklap.pk. The market is decided per request, first that wins:
  1. the user's words — the model fills the tools' `market` argument ("PK", "GB", "worldwide")
  2. the website — the locale it declares (<html lang="en-PK">, og:locale, its own hreflang), else its country domain
  3. nobody decided → ask once; the user's own country (from the browser's time zone) is offered first
Countries, their languages and time zones come from the CLDR data that ships with Babel — no lists of our own.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass
from urllib.parse import urlsplit

from babel.core import Locale, get_global

from src.brain.web import WebClient, WebError
from src.core.clock import current_time_zone
from src.tools.site_audit import AUDIT_AGENT, AuditError, Page, analyze_page, check_public

WORLDWIDE = "worldwide"            # what the model writes for "global"; SearXNG's own word for it is "all"
# country domains sold worldwide as generic names (.io, .ai, .co, .tv, .me, …): they say nothing about the market
GENERIC_COUNTRY_DOMAINS = frozenset({"io", "ai", "co", "tv", "me", "fm", "ly", "cc", "to", "gg", "ws"})
# IANA kept .uk when ISO 3166 named the United Kingdom GB — the one country domain that is not its country code
DOMAIN_COUNTRY = {"uk": "GB"}

_NAMES = Locale("en").territories                    # "PK" → "Pakistan"
_LANGUAGES = get_global("territory_languages")       # "PK" → {"ur": {...}, "en": {...}}: real countries only
_SCRIPTS = Locale("en").scripts                      # "Arab" → "Arabic"


@dataclass(frozen=True)
class Market:
    country: str                   # ISO 3166 code; "" = worldwide
    language: str                  # e.g. "en"; "" = worldwide
    how: str                       # why this market, shown in the report: "you asked", "the site's .pk domain"

    @property
    def search_language(self) -> str:
        """SearXNG's `language` parameter: "en-PK", or "all" for worldwide."""
        return f"{self.language}-{self.country}" if self.country else "all"

    @property
    def label(self) -> str:
        return _NAMES.get(self.country, self.country) if self.country else "Worldwide"


class MarketUnknown(Exception):
    """Nothing decided the market: the tool asks the user (ask_market)."""


def country_code(value: str) -> str | None:
    """'PK' / 'pk' / 'Pakistan' → 'PK'; 'worldwide' → ''; anything else → None."""
    value = value.strip()
    if value.lower() in (WORLDWIDE, "all"):
        return ""
    if value.upper() in _LANGUAGES:
        return value.upper()
    lowered = value.lower()
    return next((code for code in _LANGUAGES if _NAMES.get(code, "").lower() == lowered), None)


def _script(text: str) -> str:
    """The writing system of the text's first letter, in Unicode's words ('LATIN', 'ARABIC', …)."""
    letter = next((ch for ch in text if ch.isalpha()), None)
    return unicodedata.name(letter, "").split(" ")[0] if letter else ""


def _language(country: str, text: str = "", declared: str = "") -> str:
    """The language to search in: the one the site declares, else the country's most-spoken official language
    written in the same script as the text (English for a Latin-script keyword in Pakistan, Urdu for an Urdu one)."""
    if declared:
        return declared.replace("_", "-").split("-")[0].lower()
    spoken = sorted(((code.split("_")[0], info["population_percent"])
                     for code, info in (_LANGUAGES.get(country) or {}).items() if info.get("official_status")),
                    key=lambda pair: -pair[1])
    script = _script(text)
    subtags = get_global("likely_subtags")
    for code, _ in spoken:
        likely = (subtags.get(code) or "").split("_")
        if script and len(likely) > 1 and _SCRIPTS.get(likely[1], "").upper() == script:
            return code
    return spoken[0][0] if spoken else "en"


def requested(value: str | None, text: str = "") -> Market | None:
    """The market the user asked for (the model's `market` argument), or None when they named none."""
    code = country_code(value or "")
    if code is None:
        return None
    return Market(code, _language(code, text) if code else "", "you asked")


def from_locale(locale: str, text: str = "") -> Market | None:
    """'en-PK' / 'en_PK' → Pakistan in English; a locale without a country says nothing."""
    parts = locale.replace("_", "-").split("-")
    country = next((p.upper() for p in parts[1:] if p.upper() in _LANGUAGES), None)
    return Market(country, _language(country, text, parts[0]), f"the site declares {locale}") if country else None


def from_domain(host: str, text: str = "", declared: str = "") -> Market | None:
    """A country domain (.pk, .co.uk, .de) → that country; generic domains (.com, .io) say nothing."""
    tld = host.lower().rstrip(".").rsplit(".", 1)[-1]
    country = DOMAIN_COUNTRY.get(tld) or ("" if tld in GENERIC_COUNTRY_DOMAINS else tld.upper())
    if country not in _LANGUAGES:
        return None
    return Market(country, _language(country, text, declared), f"the site's .{tld} domain")


def from_page(page: Page, keyword: str = "") -> Market | None:
    """What a site's page says about its market: its declared locale, else its domain. Without a declared language
    the search language follows the keyword's script (else the page's own text)."""
    text = keyword or page.text
    return (from_locale(page.locale, text)
            or from_domain(urlsplit(page.url).hostname or "", text, page.locale))


def for_site(url: str, keyword: str = "") -> Market | None:
    """Read the site's start page (politely, public addresses only) and see what market it is for."""
    try:
        check_public(url)
        resp = WebClient(user_agent=AUDIT_AGENT).get(url)
    except (AuditError, WebError):
        return from_domain(urlsplit(url).hostname or "", keyword)
    if resp.text and resp.content_type.startswith(("text/html", "application/xhtml")):
        return from_page(analyze_page(resp.text, resp.url), keyword)
    return from_domain(urlsplit(url).hostname or "", keyword)


def home_country() -> str:
    """The user's own country, from the time zone their browser sent ('Asia/Karachi' → 'PK'); '' when unknown."""
    zone = current_time_zone()
    return get_global("zone_territories").get(zone, "") if zone else ""


def ask_market(subject: str) -> list[dict]:
    """The clarify question (ask_clarification's format) when nothing decided the market."""
    home = home_country()
    options = ([_NAMES[home]] if home in _NAMES else []) + ["Worldwide"]
    return [{"question": f"Which market should I check {subject} in? (Or type a country.)", "options": options}]
