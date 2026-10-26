"""Commands in grounded answers are checked against the sources (src/brain/command_check.py)."""

from __future__ import annotations

from src.brain.command_check import NOTE_PREFIX, check_note, command_terms, unverified_terms

SOURCE = """Add-DnsServerConditionalForwarderZone -Name "contoso.com" -MasterServers 10.0.0.1
Use logging.basicConfig(filename='example.log', filemode='w') to write to a file.
handler = logging.handlers.RotatingFileHandler(LOG_FILENAME, maxBytes=20, backupCount=5)"""


def test_the_b6_case_a_cmdlet_the_sources_never_mention_is_flagged():
    answer = ("Run `Set-DnsServerConditionalForwarderZone` [1], or create it with "
              "`Add-DnsServerConditionalForwarderZone -Name contoso.com -MasterServers 10.0.0.1` [1].")
    assert unverified_terms(answer, [SOURCE]) == ["Set-DnsServerConditionalForwarderZone"]


def test_parameters_and_calls_inside_code_are_checked():
    answer = (
        "```powershell\nAdd-DnsServerConditionalForwarderZone -Name x -MasterServers 1.2.3.4 -ReplicationScope Forest\n```\n"
        "```python\nlogging.basicConfig(filename='app.log')\nlogging.handlers.RotatingFileHandler('a.log')\n"
        "logging.handlers.TimedRotatingFileHandler('b.log')\nname.lower()\n```"
    )
    assert unverified_terms(answer, [SOURCE]) == ["-ReplicationScope", "logging.handlers.TimedRotatingFileHandler"]


def test_plain_english_and_placeholders_are_not_commands():
    text = "Split-Brain DNS and a Hands-On guide: replace <ZoneName> and use the -Force option carefully."
    assert command_terms(text) == []            # -Force outside code is prose, not checked


def test_case_insensitive_and_note_text():
    assert command_terms("Use Get-DnsServerzoneaging") == ["Get-DnsServerzoneaging"]
    assert unverified_terms("Use Get-DnsServerzoneaging", ["Get-DnsServerZoneAging shows aging"]) == []
    assert check_note([]) == ""
    note = check_note(["Set-Foo", "-Bar"])
    assert note.startswith(NOTE_PREFIX) and "`Set-Foo`, `-Bar`" in note


def test_the_note_appears_under_a_knowledge_base_answer(monkeypatch):
    from dataclasses import replace

    from src.brain.answer import answer_question
    from src.brain.retrieve import Passage, SearchResult
    from src.core.config import Config

    monkeypatch.setattr("src.brain.answer._log_retrieval", lambda *a, **k: None)
    monkeypatch.setattr("src.brain.freshness.check_sources", lambda config, slugs: {})
    passage = Passage(chunk_id=1, document_id=1, title="Forwarders", url=None, heading_path=None,
                      content=SOURCE, token_count=50, updated_at=None, source="dns", publisher=None,
                      licence=None, score=1.0)
    found = SearchResult(query="q", mode="hybrid", passages=[passage], enough_evidence=True,
                         best_similarity=0.8, exact_terms=[], exact_match=False)
    reply = "Use `Set-DnsServerConditionalForwarderZone -Name contoso.com` [1]."
    result = answer_question("how do I add a conditional forwarder", config=replace(Config.from_env()),
                             chat=lambda **kw: reply, searcher=lambda *a, **k: found)
    assert result.unverified == ["Set-DnsServerConditionalForwarderZone"]
    assert result.text.startswith(reply) and NOTE_PREFIX in result.text
