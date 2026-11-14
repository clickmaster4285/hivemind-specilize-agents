"""Chat recall (ISSUES_LOG O63): a finished exchange is stored in pieces with its embedding; a long chat gets the
folded turns' original words back when the new message needs them.

The database tests run on the test brain database (tests/conftest.py) with fake embeddings, so no Ollama is
needed; they skip without the database (docker compose up -d db).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from src.brain import db as brain_db
from src.brain.chunking import MAX_TOKENS
from src.brain.embed import EMBED_DIM
from src.brain.migrate import migrate
from src.core.config import Config
from src.core.conversation import estimate_tokens
from src.jobs import chat_recall as cr

T0 = datetime(2026, 11, 14, 9, 0, tzinfo=timezone.utc)
THREAD = "test-recall-thread"
NVR = ("The NVR is at 192.168.10.45", "Noted: NVR 192.168.10.45.")
PARKING = ("Channel 7 is the parking camera", "Noted: channel 7 = parking.")
VLAN = ("How do I add VLAN 20 on the MikroTik?", "Use /interface vlan add vlan-id=20 interface=bridge.")


# ── pieces (no database) ───────────────────────────────────────────────────

def test_a_short_exchange_is_one_piece():
    assert cr.pieces(" what is docker ", "a tool") == ["User: what is docker\n\nAssistant: a tool"]


def test_a_long_answer_is_cut_and_each_piece_names_the_question():
    answer = "\n\n".join(f"## Part {n}\n\n" + "word " * 400 for n in range(4))
    parts = cr.pieces("explain VLANs", answer)
    assert parts[0] == "User: explain VLANs"
    assert len(parts) > 2 and all(p.startswith('Assistant, answering "explain VLANs":\n') for p in parts[1:])
    label = estimate_tokens('Assistant, answering "explain VLANs":\nPart 3\n')
    assert all(estimate_tokens(p) <= MAX_TOKENS + label for p in parts)


def test_the_recall_block_says_the_newer_text_wins():
    block = cr.recall_block([cr.Recalled("User: a", T0, 0.9), cr.Recalled("User: b", T0, 0.8)])
    assert block.endswith("User: a\n\nUser: b") and "newer" in block


# ── the database ───────────────────────────────────────────────────────────

def _vec(text: str) -> list[float]:
    seed = hashlib.sha256(text.encode()).digest()
    return [seed[i % len(seed)] / 255 - 0.5 for i in range(EMBED_DIM)]


def _fake_embed(texts, _config):
    return [_vec(t) for t in texts]


def _aimed_at(piece: str):
    """A query vector equal to this piece's vector: similarity 1.0 with it, about 0 with the others."""
    return lambda _message, _config, task: _vec(piece)


def _store(config, user, job, minutes, exchange):
    return cr.remember(config, user_id=user, thread_id=THREAD, job_id=job, asked_at=T0 + timedelta(minutes=minutes),
                       question=exchange[0], answer=exchange[1], embed=_fake_embed)


def _one_piece(exchange) -> str:
    return cr.pieces(*exchange)[0]


@pytest.fixture(scope="module")
def config() -> Config:
    cfg = Config.from_env()
    try:
        migrate(cfg)
    except (brain_db.BrainDBError, psycopg.OperationalError) as exc:
        pytest.skip(f"brain database not available: {exc}")
    return cfg


@pytest.fixture
def chat(config):
    _store(config, 1, "test-recall-nvr", 1, NVR)
    _store(config, 1, "test-recall-parking", 2, PARKING)
    _store(config, 1, "test-recall-vlan", 3, VLAN)
    _store(config, 2, "test-recall-other", 4, ("My NVR is at 10.0.0.9", "Noted."))
    yield
    with psycopg.connect(brain_db.owner_url(config), autocommit=True) as conn:
        conn.execute("DELETE FROM brain.chat_passages WHERE thread_id = %s", (THREAD,))


def _recall(config, message, *, embed, user=1, job_ids=("test-recall-nvr", "test-recall-parking", "test-recall-vlan"),
            budget=2000):
    return cr.recall(config, user_id=user, thread_id=THREAD, job_ids=list(job_ids), message=message, budget=budget,
                     embed=embed)


def test_the_best_match_comes_back_word_for_word(config, chat):
    found = _recall(config, "which channel is parking?", embed=_aimed_at(_one_piece(PARKING)))
    assert [r.content for r in found] == [_one_piece(PARKING)]
    assert found[0].asked_at == T0 + timedelta(minutes=2) and found[0].similarity == pytest.approx(1.0)


def test_a_piece_with_the_exact_identifiers_comes_first_whatever_its_meaning_score(config, chat):
    found = _recall(config, "what is on 192.168.10.45?", embed=_aimed_at("unrelated"))
    assert [r.content for r in found] == [_one_piece(NVR)]


def test_only_the_listed_jobs_are_searched(config, chat):
    """chat_memory lists the folded, not-deleted jobs: the others never come back."""
    found = _recall(config, "what is on 192.168.10.45?", embed=_aimed_at(_one_piece(NVR)),
                    job_ids=["test-recall-parking"])
    assert found == []


def test_another_users_pieces_are_never_seen(config, chat):
    """Row-level security: user 2 asking with user 1's jobs listed gets nothing, and user 1 never sees user 2's."""
    assert _recall(config, "what is on 192.168.10.45?", embed=_aimed_at(_one_piece(NVR)), user=2) == []
    assert _recall(config, "my NVR 10.0.0.9", embed=_aimed_at("x"), job_ids=["test-recall-other"]) == []


def test_recall_keeps_to_its_budget(config, chat):
    tight = estimate_tokens(_one_piece(NVR)) - 1
    assert _recall(config, "what is on 192.168.10.45?", embed=_aimed_at(_one_piece(NVR)), budget=tight) == []


def test_a_weak_match_is_left_out(config, chat):
    assert _recall(config, "tell me a joke", embed=_aimed_at("nothing stored")) == []


def test_a_regenerated_answer_replaces_the_jobs_pieces(config, chat):
    assert _store(config, 1, "test-recall-parking", 2, ("Channel 7 is the parking camera", "Channel 7: parking.")) == 1
    with brain_db.connect(config, account_id="1") as conn:
        rows = conn.execute("SELECT content FROM brain.chat_passages WHERE job_id = 'test-recall-parking'").fetchall()
    assert rows == [("User: Channel 7 is the parking camera\n\nAssistant: Channel 7: parking.",)]
