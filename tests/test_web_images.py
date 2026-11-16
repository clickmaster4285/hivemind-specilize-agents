"""Web images (R4) — the SSRF guard blocks private/internal hosts (offline, IP literals only)."""

from __future__ import annotations

from src.tools.web_images import (
    PER_ANCHOR,
    PER_ITEM,
    Topic,
    _is_public,
    _norm_anchor,
    _round_robin,
    _topics,
    image_anchors,
    wants_images,
)


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


def _pairs(topics):
    return [(t.anchor, t.term) for t in topics]


def test_topics_are_contextual_search_terms_per_heading():
    answer = (
        "Intro paragraph.\n\n"
        "## 📘 Phase 4: React.js (Frontend Framework)\nReact.js builds the UI.\n\n"
        "### 1. Istanbul – History, culture & shopping\nIstanbul spans two continents.\n\n"
        "## MongoDB\nMongoDB stores documents.\n"
    )
    # one entry per heading, in order, with the "Phase N:" / numbering / dash tail stripped from the search
    assert _pairs(_topics(answer)) == [("phase 4 react js frontend framework", "React.js"),
                                       ("1 istanbul history culture shopping", "Istanbul"),
                                       ("mongodb", "MongoDB")]
    assert all(t.keep == PER_ANCHOR for t in _topics(answer))
    assert _topics("just a paragraph, no headings at all") == []


def test_a_heading_that_names_no_one_thing_gets_no_pictures():
    # O53: these headings were searched with the subject added and got GTA memes, a DaVinci Resolve
    # screenshot and Ray-Ban glasses; none is a thing its section talks about
    answer = (
        "## The Math\nAt 5 Mbps the file is big.\n\n"
        "## Who Controls the Ban?\nThe government decides.\n\n"
        "## Estimated File Size\nAbout 2 GB.\n"
    )
    assert _topics(answer, "how big is a 2 hour video") == []
    assert wants_images(answer) is False


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
    assert _pairs(topics) == [("jeju island", "Jeju Island"), ("jeonju food capital", "Jeonju")]


def test_a_group_heading_pictures_each_named_item_under_that_item():
    answer = (
        "Here are the best places to visit in South Korea.\n\n"
        "## Top Cities\nThe big ones.\n- **Seoul:** the capital.\n- **Busan:** the coast.\n\n"
        "## Gangneung\nGangneung has a sea-side castle.\n"
    )
    topics = _topics(answer, "places to visit in korea")
    # "Top Cities" itself gets nothing; its items get one picture each, anchored to the item and searched with
    # the subject ("Korea" is a name in the answer; "places" is not, the answer writes it in lowercase)
    assert topics == [Topic("seoul", "Seoul Korea", PER_ITEM), Topic("busan", "Busan Korea", PER_ITEM),
                      Topic("gangneung", "Gangneung", PER_ANCHOR)]


def test_a_list_of_named_places_without_headings_gets_one_picture_per_item():
    # O75: a numbered list with bold names and no headings got no pictures at all
    answer = (
        "Turkey has a lot to see.\n\n"
        "1. **Istanbul**: where Europe meets Asia.\n"
        "2. **Cappadocia**: fairy chimneys and balloon rides.\n"
        "3. **Ephesus**: an ancient Roman city.\n\n"
        "**Best time to visit:** spring or autumn.\n"
    )
    topics = _topics(answer, "top 3 places to visit in Turkey")
    # "Best time to visit" is a label, not a thing: it has lowercase words
    assert _pairs(topics) == [("istanbul", "Istanbul Turkey"), ("cappadocia", "Cappadocia Turkey"),
                              ("ephesus", "Ephesus Turkey")]
    assert {t.keep for t in topics} == {PER_ITEM}
    assert wants_images(answer) is True
    assert image_anchors(answer) == ["istanbul", "cappadocia", "ephesus"]


def test_round_robin_gives_every_list_its_first_item_before_any_second():
    assert _round_robin([["a1", "a2", "a3"], ["b1"], ["c1", "c2"]]) == ["a1", "b1", "c1", "a2", "c2", "a3"]
    assert _round_robin([]) == []


def test_wants_images_is_answer_driven_not_query_keywords():
    # a structured, topical answer (travel) wants images — even though no query keyword said "educational"
    travel = ("South Korea is great.\n\n## Seoul\nSeoul is the capital.\n\n## Busan\nBusan is on the coast.\n\n"
              "## Jeju Island\nJeju is an island.\n")
    assert wants_images(travel) is True
    # a greeting / one-liner has nothing to illustrate
    assert wants_images("Hi! How can I help you today?") is False
    # a code-dominated answer is skipped (stock photos would be noise)
    code = "## Setup\n```py\n" + "x = 1\n" * 60 + "```\n\n## Run\n```py\n" + "y = 2\n" * 60 + "```\n"
    assert wants_images(code) is False
    # O71: a how-to with a few command blocks, mostly prose, gets no stock photos either
    howto = ("## Current Situation\nThe app was built and nginx serves it. " * 8 + "\n\n## What You Can Do\n"
             + "Install PM2 and start the app. " * 8 + "\n```bash\nnpm install -g pm2\n```\n")
    assert wants_images(howto) is False
