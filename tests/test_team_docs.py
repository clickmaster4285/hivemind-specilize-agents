"""Team knowledge (BACKLOG L30 T4, src/brain/team_docs.py, migration 005): documents an admin uploads for one team,
found by that team's members only. The database tests run on the test brain database with fake embeddings (no
Ollama); they skip without it."""

from __future__ import annotations

import hashlib
import io

import psycopg
import pytest

from src.brain import db as brain_db
from src.brain import team_docs
from src.brain.embed import EMBED_DIM
from src.brain.migrate import migrate
from src.brain.retrieve import search
from src.core.config import Config

IT, SALES = "testteam-it", "testteam-sales"
RUNBOOK = ("# Backup runbook\n\nThe nightly backup of the file server starts at 01:30 and is copied to the NAS "
           "called vault-02. If it fails, restart the backup agent and check the free space on vault-02.\n")


def _docx(*paragraphs: tuple[str, str]) -> bytes:
    import docx

    document = docx.Document()
    for style, text in paragraphs:
        document.add_paragraph(text, style=style)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def _pdf(text: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("helvetica", size=12)
    pdf.multi_cell(0, 8, text)
    return bytes(pdf.output())


# ── reading files (no database) ─────────────────────────────────────────────

def test_word_headings_become_markdown_headings():
    doc = team_docs.read_upload("vpn_policy.docx", _docx(("Heading 1", "Remote access"),
                                                         ("Normal", "Staff connect through WireGuard only."),
                                                         ("Heading 2", "Keys"), ("Normal", "Keys rotate every 90 days.")))
    assert doc.title == "vpn policy"
    assert "## Remote access" in doc.content_md and "### Keys" in doc.content_md and "90 days" in doc.content_md


def test_a_pdf_is_read_page_by_page():
    doc = team_docs.read_upload("printers.pdf", _pdf("Printer PR-7 is on the second floor; its queue is pr7-color."))
    assert "## Page 1" in doc.content_md and "pr7-color" in doc.content_md


@pytest.mark.parametrize("name, data, message", [
    ("setup.exe", b"MZ", "use PDF, Word"),
    ("empty.txt", b"   ", "no readable text"),
    ("big.txt", b"x" * (team_docs.MAX_UPLOAD_BYTES + 1), "larger than"),
])
def test_files_that_cannot_become_knowledge_are_refused(name, data, message):
    with pytest.raises(team_docs.TeamDocError, match=message):
        team_docs.read_upload(name, data)


# ── the database ────────────────────────────────────────────────────────────

def _vec(text: str) -> list[float]:
    seed = hashlib.sha256(text.encode()).digest()
    return [seed[i % len(seed)] / 255 - 0.5 for i in range(EMBED_DIM)]


def _fake_embed(texts):
    return [_vec(t) for t in texts]


@pytest.fixture(scope="module")
def config() -> Config:
    cfg = Config.from_env()
    try:
        migrate(cfg)
    except (brain_db.BrainDBError, psycopg.OperationalError) as exc:
        pytest.skip(f"brain database not available: {exc}")
    return cfg


@pytest.fixture
def teams(config, monkeypatch):
    """Accounts 9001 (IT) and 9002 (sales); their teams come from this map instead of the app database."""
    members = {"9001": IT, "9002": SALES}
    monkeypatch.setattr(brain_db, "team_of", lambda account: members.get(account or "", ""))
    yield
    with psycopg.connect(brain_db.owner_url(config), autocommit=True) as conn:
        conn.execute("DELETE FROM brain.sources WHERE slug LIKE 'team-testteam-%'")


def _found(config, account: str | None) -> list[str]:
    target = _vec("Backup runbook > " + "x")      # any vector: the keyword search finds the exact word
    result = search("vault-02 backup", config=config, account_id=account, embed=lambda _q: target, mode="keyword")
    return [p.title for p in result.passages]


def test_a_teams_documents_are_found_by_that_team_only(config, teams):
    added = team_docs.add(config, IT, "backup-runbook.md", RUNBOOK.encode(), uploaded_by="root", embed=_fake_embed)
    assert added["chunks"] >= 1 and not added["replaced"]
    assert "Backup runbook" in _found(config, "9001")             # an IT member
    assert "Backup runbook" not in _found(config, "9002")         # sales
    assert "Backup runbook" not in _found(config, None)           # nobody signed in / public only


def test_the_same_name_replaces_and_a_document_is_removed_from_its_team_only(config, teams):
    first = team_docs.add(config, IT, "backup-runbook.md", RUNBOOK.encode(), uploaded_by="root", embed=_fake_embed)
    second = team_docs.add(config, IT, "backup-runbook.md", (RUNBOOK + "\nUpdated.\n").encode(), uploaded_by="root",
                           embed=_fake_embed)
    assert second["replaced"] and [d["id"] for d in team_docs.listing(config, IT)] == [second["id"]]
    assert first["id"] != second["id"]
    assert not team_docs.remove(config, SALES, second["id"])     # another team's: untouched
    assert team_docs.remove(config, IT, second["id"]) and team_docs.listing(config, IT) == []


def test_a_users_own_connection_cannot_write_team_knowledge(config, teams):
    team_docs.add(config, IT, "backup-runbook.md", RUNBOOK.encode(), uploaded_by="root", embed=_fake_embed)
    with pytest.raises(psycopg.Error):
        with brain_db.connect(config, account_id="9001") as conn:          # an IT member, not the system
            conn.execute("UPDATE brain.documents SET title = 'changed' WHERE owner_id = %s RETURNING id",
                         (team_docs.owner_of(IT),)).fetchone()
            conn.execute("INSERT INTO brain.documents (source_id, url, title, fetched_at, content_md, content_hash, "
                         "collection_id, scope, owner_id) SELECT source_id, 'x', 't', now(), 'c', 'h', collection_id, "
                         "'team', owner_id FROM brain.documents WHERE owner_id = %s LIMIT 1", (team_docs.owner_of(IT),))
