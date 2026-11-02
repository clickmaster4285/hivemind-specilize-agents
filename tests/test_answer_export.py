"""An answer already in the chat becomes a file without a model call — whole, never cut (BACKLOG L10)."""

from __future__ import annotations

import contextlib
import dataclasses
import json
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.core.config import Config
from src.core.db import Base
from src.core.models_db import Job, JobEvent
from src.tools import answer_export as ax

ANSWER = """# Docker networking

Docker gives every container its own network stack.

## Bridge network

- default for containers
- **isolated** from the host

```bash
# list networks
docker network ls
```

## Host network

Shares the host's stack.
"""


@pytest.fixture
def config(tmp_path):
    return dataclasses.replace(Config.from_env(), artifacts_dir=tmp_path)


def test_sections_follow_the_answers_own_headings():
    title, sections = ax.split_sections(ANSWER, "fallback question")
    assert title == "Docker networking"                       # the lone H1 is the title
    assert [s["heading"] for s in sections] == ["Overview", "Bridge network", "Host network"]
    assert "# list networks" in sections[1]["body"]            # a comment in a code block is not a heading


def test_an_answer_without_headings_is_one_section_named_after_the_question():
    title, sections = ax.split_sections("Just a short reply.", "what is dns")
    assert title == "what is dns"
    assert sections == [{"heading": "what is dns", "body": "Just a short reply."}]


@pytest.mark.parametrize("raw, kind", [("Word", "docx"), (".PDF", "pdf"), ("slides", "pptx"), ("markdown", "md"),
                                       ("xlsx", None), ("", None)])
def test_export_format(raw, kind):
    assert ax.export_format(raw) == kind


@pytest.mark.parametrize("fmt", ax.FORMATS)
def test_every_format_writes_the_whole_answer(config, fmt):
    sources = [{"n": 1, "title": "Docker docs", "url": "https://docs.docker.com/network/"}]
    meta = ax.export_answer(ANSWER, fmt, title="q", job_id="job1", config=config, sources=sources)
    path = config.artifacts_dir / "job1" / "files" / meta["filename"]
    assert meta["filename"] == f"Docker_networking.{fmt}" and path.stat().st_size > 0
    if fmt in ("md", "txt", "html"):
        text = path.read_text(encoding="utf-8")
        assert "Shares the host" in text and "docs.docker.com" in text    # last section and the sources kept
        if fmt == "txt":
            assert "**" not in text and "## " not in text


def test_tables_and_code_stay_tables_and_code(config):
    from docx import Document

    answer = ("## Compare\n\n| Mode | Isolation |\n|---|:---:|\n| **bridge** | yes |\n| host | no |\n\n"
              "```bash\ndocker run --network host nginx\n```\n")
    html = ax.export_answer(answer, "html", title="modes", job_id="j", config=config)
    page = (config.artifacts_dir / "j" / "files" / html["filename"]).read_text(encoding="utf-8")
    assert "<th>Mode</th>" in page and "<td><strong>bridge</strong></td>" in page
    assert "<pre><code>docker run --network host nginx</code></pre>" in page
    docx = ax.export_answer(answer, "docx", title="modes", job_id="j", config=config)
    doc = Document(str(config.artifacts_dir / "j" / "files" / docx["filename"]))
    assert [[c.text for c in row.cells] for row in doc.tables[0].rows] == [
        ["Mode", "Isolation"], ["bridge", "yes"], ["host", "no"]]
    txt = ax.export_answer(answer, "txt", title="modes", job_id="j", config=config)
    assert "bridge | yes" in (config.artifacts_dir / "j" / "files" / txt["filename"]).read_text(encoding="utf-8")


def test_answer_in_events_skips_an_earlier_export_reply():
    events = [
        {"type": "agent_done", "agent_id": "assistant", "output": "The real answer", "sources": [{"n": 1}]},
        {"type": "agent_done", "agent_id": "assistant", "role": ax.EXPORT_ROLE, "output": "Here is x.pdf"},
    ]
    assert ax.answer_in_events(events) == ("The real answer", [{"n": 1}])
    assert ax.answer_in_events(events, "The real answer")[0] == "The real answer"
    assert ax.answer_in_events(events, "something else") is None


@pytest.fixture
def db(monkeypatch):
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(bind=engine)

    @contextlib.contextmanager
    def scope(_config=None):
        yield session
        session.flush()

    monkeypatch.setattr("src.core.db.session_scope", scope)
    yield session
    session.close()


def _job(session, job_id, *, thread, minutes, query="q", answer=None):
    session.add(Job(id=job_id, user_id=1, mode="chat", query=query, payload_json=json.dumps({"thread_id": thread}),
                    created_at=datetime(2026, 11, 2, tzinfo=timezone.utc) + timedelta(minutes=minutes)))
    if answer:
        session.add(JobEvent(job_id=job_id, seq=1, event_json=json.dumps(
            {"type": "agent_done", "agent_id": "assistant", "output": answer})))
    session.flush()


def test_make_this_a_pdf_exports_the_full_previous_answer_of_the_same_chat(config, db):
    long_answer = ANSWER + "\n".join(f"- point {i}" for i in range(2000))     # far past the 6,000-char history cap
    _job(db, "other", thread="t2", minutes=2, answer="another chat's answer")
    _job(db, "a1", thread="t1", minutes=1, query="docker networking", answer=long_answer)
    _job(db, "now", thread="t1", minutes=3, query="make this a pdf")
    events: list[dict] = []
    errors = ax.run_answer_export("make this a pdf", job_id="now", config=config, user_id=1,
                                  on_progress=events.append, format="pdf")
    assert errors == []
    ready = next(e for e in events if e["type"] == "artifact_ready")
    assert ready["filename"] == "Docker_networking.pdf"
    md = ax.export_answer(long_answer, "md", title="x", job_id="check", config=config)
    assert "point 1999" in (config.artifacts_dir / "check" / "files" / md["filename"]).read_text(encoding="utf-8")


def test_nothing_to_export_falls_back_to_generating(config, db):
    _job(db, "now", thread="t1", minutes=3)
    assert ax.run_answer_export("make a pdf about dns", job_id="now", config=config, user_id=1, format="pdf") is None
    assert ax.run_answer_export("as xlsx", job_id="now", config=config, user_id=1, format="xlsx") is None
