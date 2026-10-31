"""Web images (R4) — the SSRF guard blocks private/internal hosts (offline, IP literals only)."""

from __future__ import annotations

from src.tools.web_images import _is_public, _norm_anchor, _topics, wants_images


def test_blocks_private_and_local_hosts():
    for url in [
        "http://127.0.0.1/x.png",
        "http://10.0.0.5/a.jpg",
        "http://192.168.1.10/b.png",
        "http://169.254.1.1/c.png",   # link-local
        "http://[::1]/d.png",         # ipv6 loopback
        "not a url",
        "http:///nohost.png",
    ]:
        assert _is_public(url) is False, url


def test_norm_anchor_folds_emoji_numbering_and_accents():
    # the key must survive emoji / numbering / markdown so it matches the frontend's normAnchor
    assert _norm_anchor("## 📘 Phase 1: The Foundation") == "phase 1 the foundation"
    assert _norm_anchor("**React.js**") == "react js"


def test_topics_are_contextual_search_terms_per_heading():
    answer = (
        "Intro paragraph.\n\n"
        "## 📘 Phase 1: The Foundation (HTML, CSS, JavaScript)\nbody\n\n"
        "## Phase 4: React.js (Frontend Framework)\nbody\n\n"
        "### 1. Istanbul – History, culture & shopping\nbody\n"
    )
    topics = _topics(answer)
    keys = [k for k, _ in topics]
    terms = [t for _, t in topics]
    # one entry per heading, in order, with the "Phase N:" / numbering / dash tail stripped from the search
    assert keys == ["phase 1 the foundation html css javascript",
                    "phase 4 react js frontend framework",
                    "1 istanbul history culture shopping"]
    assert "React.js (Frontend Framework)" in terms
    assert "Istanbul" in terms                     # the dash tail ("– History…") is dropped
    # no headings → empty
    assert _topics("just a paragraph, no headings at all") == []


def test_wants_images_is_answer_driven_not_query_keywords():
    # a structured, topical answer (travel) wants images — even though no query keyword said "educational"
    travel = "South Korea is great.\n\n## Seoul\nThe capital.\n\n## Busan\nThe coast.\n\n## Jeju Island\nAn island.\n"
    assert wants_images(travel) is True
    # a greeting / one-liner has nothing to illustrate
    assert wants_images("Hi! How can I help you today?") is False
    # a code-dominated answer is skipped (stock photos would be noise)
    code = "## Setup\n```py\n" + "x = 1\n" * 60 + "```\n\n## Run\n```py\n" + "y = 2\n" * 60 + "```\n"
    assert wants_images(code) is False
