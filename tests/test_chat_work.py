"""The chat remembers what it works on (ISSUES_LOG O103, O104, O107): its server and project carry to follow-ups,
"Which server?" waits for the reply, and "uk" names "uk server" — from the live uk-server chat of 2026-11-19."""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from src.core.config import Config
from src.core.models_db import Base, ChatThreadRow, Job, User
from src.jobs import chat_work, worker
from src.llm.assistant import BY_NAME
from src.servers import server_vault
from src.servers.server_vault import ServerLogin

PK = ServerLogin(id="s-pk", label="pk server", host="10.0.0.1", port=22, username="root")
UK = ServerLogin(id="s-uk", label="uk server", host="10.0.0.2", port=22, username="root")
USA = ServerLogin(id="s-usa", label="usa server", host="10.0.0.3", port=22, username="root")
CFG = Config.from_env()


# ── naming a server ────────────────────────────────────────────────────

@pytest.fixture
def three(monkeypatch):
    servers = [PK, UK, USA]
    monkeypatch.setattr(server_vault, "list_servers", lambda user_id, kind="ssh": servers)
    return servers


@pytest.mark.parametrize("said, server", [
    ("uk , Clickmastersoftware", "s-uk"),          # the label's own word, not the whole label
    ("uk server", "s-uk"),
    ("check 10.0.0.3", "s-usa"),
    ("restart the server", None),                  # "server" names none of them
    ("did we pull the latest?", None),
])
def test_a_server_is_named_by_its_own_word(three, said, server):
    assert server_vault.server_in(said, three) == server


def test_the_chats_server_is_used_when_the_message_names_none(three):
    assert server_vault.resolve(1, None, "did we pull the latest?", "s-uk").id == "s-uk"
    assert server_vault.resolve(1, None, "now check pk", "s-uk").id == "s-pk"         # the message wins
    assert server_vault.resolve(1, "web99", "on usa please", None).id == "s-usa"      # a wrong name: the message
    assert server_vault.resolve(1, None, "check it", None) is None
    login, ask = server_vault.login_for(1, None, "check it")
    assert login is None and ask == "Which server? Your servers: pk server, uk server, usa server."


# ── the chat's memory ──────────────────────────────────────────────────

@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = Session(bind=engine)
    session.add_all([User(id=1, username="ana", password_hash="x"), ChatThreadRow(id="t1", user_id=1)])
    for job_id in ("j1", "j2", "j3"):
        session.add(Job(id=job_id, user_id=1, mode="chat", query="q", payload_json=json.dumps({"thread_id": "t1"})))
    session.flush()

    @contextmanager
    def scope(_config=None):
        yield session
        session.flush()

    monkeypatch.setattr("src.core.db.session_scope", scope)
    yield session
    session.close()


def test_what_one_job_saves_the_next_job_of_the_chat_reads(db):
    chat_work.remember(CFG, "j1", server="s-uk", project="/var/www/Clickmastersoftware", branch="changes11/17")
    chat_work.remember(CFG, "j2", branch=None, deploy="pulled origin/changes11/17; built")
    work = chat_work.get(CFG, "j3")
    assert work == {"server": "s-uk", "project": "/var/www/Clickmastersoftware",
                    "deploy": "pulled origin/changes11/17; built"}
    assert chat_work.describe(work, "uk server") == ("This chat is working on uk server, project "
                                                     "/var/www/Clickmastersoftware. Last deploy here: pulled "
                                                     "origin/changes11/17; built")
    assert chat_work.get(CFG, "no-such-job") == {}


# ── the worker: fill in, wait for the reply ────────────────────────────

@pytest.fixture
def saved(monkeypatch, three):
    store: dict = {}
    monkeypatch.setattr(chat_work, "remember", lambda config, job_id, **f: store.update(
        {k: v for k, v in f.items()}) or [store.pop(k) for k, v in f.items() if v is None])
    monkeypatch.setattr(server_vault, "find", lambda user_id, name, kind="ssh": next(
        (s for s in three if name in (s.id, s.label) or name in s.label), None))
    return store


def test_a_follow_up_runs_on_the_chats_server_and_project(saved):
    work = {"server": "s-uk", "project": "/var/www/Clickmastersoftware"}
    args = worker._server_context(CFG, "j", 1, BY_NAME["deploy_project"], {}, "deploy again", work)
    assert args["server"] == "s-uk" and args["project"] == "Clickmastersoftware"
    assert args["chat_work"].startswith("This chat is working on uk server, project /var/www/Clickmastersoftware")
    assert "pending" not in saved


def test_another_server_forgets_the_old_project(saved):
    work = {"server": "s-uk", "project": "/var/www/Clickmastersoftware"}
    args = worker._server_context(CFG, "j", 1, BY_NAME["diagnose_server"], {}, "check the disk on pk", work)
    assert args["server"] == "s-pk" and args["chat_work"] == "This chat is working on pk server."
    assert saved == {"server": "s-pk"}


def test_which_server_waits_for_the_reply(saved):
    args = worker._server_context(CFG, "j", 1, BY_NAME["diagnose_server"], {}, "ok check again if new changes come",
                                  {})
    assert "server" not in args
    assert saved["pending"] == {"tool": "diagnose_server", "args": {}, "query": "ok check again if new changes come"}

    tool, args, query = worker._resume_pending(CFG, "j2", 1, "uk server, Clickmastersoftware ,", dict(saved))
    assert (tool.name, args, query) == ("diagnose_server", {"server": "s-uk"}, "ok check again if new changes come")
    assert "pending" not in saved                                     # answered once


def test_a_long_or_unnamed_reply_is_a_new_message(saved):
    pending = {"pending": {"tool": "diagnose_server", "args": {}, "query": "check it"}}
    assert worker._resume_pending(CFG, "j", 1, "what is the weather in London tomorrow, and the day after?",
                                  dict(pending)) is None
    assert worker._resume_pending(CFG, "j", 1, "never mind", dict(pending)) is None
