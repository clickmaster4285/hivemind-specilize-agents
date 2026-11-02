"""Web images (R4) — the SSRF guard blocks private/internal hosts (offline, IP literals only)."""

from __future__ import annotations

from src.tools.web_images import _is_public, _norm_anchor, _round_robin, _topics, wants_images


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
    assert "React.js" in terms                     # "(Frontend Framework)" notes are dropped from the search
    assert "Istanbul" in terms                     # the dash tail ("– History…") is dropped
    # no headings → empty
    assert _topics("just a paragraph, no headings at all") == []


def test_topics_search_bold_entities_under_a_category_heading():
    # a category heading ("Top Cities") is searched by its bold places, all anchored to that section so
    # the UI groups Seoul/Busan photos under it; a specific heading (React.js) stays its own entity.
    answer = (
        "## 🏙️ Top Cities (The Must-Sees)\n"
        "**Seoul:** the capital.\n**Busan:** the coast.\n\n"
        "## React.js\nThe frontend library.\n"
    )
    topics = _topics(answer)
    cat = _norm_anchor("🏙️ Top Cities (The Must-Sees)")
    assert (cat, "Seoul") in topics and (cat, "Busan") in topics   # bold places searched, anchored to the section
    assert any(k == "react js" and t == "React.js" for k, t in topics)   # specific heading = its own entity


def test_a_named_heading_is_searched_itself_even_if_it_contains_a_common_noun():
    # regression: "Island" / "Food" were on a category word list, so Jeju got photos of its sub-items and
    # "Bibimbap Street" was searched with no place at all. Jeju is named by its own section → search Jeju.
    answer = (
        "## 🌊 Jeju Island\nA volcanic island off the coast.\n"
        "- **Manjanggul Cave:** a lava tube.\n- **Jeju Olle Trail:** a coastal walk.\n\n"
        "## 🌸 Jeonju (Food Capital)\nFamous for bibimbap.\n- **Jeonju Hanok Village:** old houses.\n"
        "- **Bibimbap Street:** try it here.\n"
    )
    topics = _topics(answer, "places to visit in korea")
    assert topics == [("jeju island", "Jeju Island"), ("jeonju food capital", "Jeonju")]


def test_a_group_heading_gets_the_subject_and_its_members_share_the_budget_fairly():
    answer = (
        "Here are the best places to visit in South Korea.\n\n"
        "## Top Cities\nThe big ones.\n- **Seoul:** the capital.\n- **Busan:** the coast.\n\n"
        "## Busan\nA coastal city with beaches.\n\n"
        "## Gangneung\nGangneung has a sea-side castle.\n"
    )
    terms = [t for _k, t in _topics(answer, "places to visit in korea")]
    # first round: one search per section ("Korea" is a name in the answer → the vague heading's context;
    # "The" is not, since the answer also writes "the"); the group's members only fill spare searches
    assert terms[:3] == ["Top Cities Korea", "Busan Korea", "Gangneung"]
    assert terms[3:] == ["Seoul Korea"]            # "Busan Korea" already searched → deduped


def test_round_robin_gives_every_list_its_first_item_before_any_second():
    assert _round_robin([["a1", "a2", "a3"], ["b1"], ["c1", "c2"]]) == ["a1", "b1", "c1", "a2", "c2", "a3"]
    assert _round_robin([]) == []


def test_wants_images_is_answer_driven_not_query_keywords():
    # a structured, topical answer (travel) wants images — even though no query keyword said "educational"
    travel = "South Korea is great.\n\n## Seoul\nThe capital.\n\n## Busan\nThe coast.\n\n## Jeju Island\nAn island.\n"
    assert wants_images(travel) is True
    # a greeting / one-liner has nothing to illustrate
    assert wants_images("Hi! How can I help you today?") is False
    # a code-dominated answer is skipped (stock photos would be noise)
    code = "## Setup\n```py\n" + "x = 1\n" * 60 + "```\n\n## Run\n```py\n" + "y = 2\n" * 60 + "```\n"
    assert wants_images(code) is False
