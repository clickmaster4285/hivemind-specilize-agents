"""The chat recall eval (O63) scores and reports offline; the run itself needs Ollama and the database."""

from __future__ import annotations

from src.llm import bench_recall as br

NVR, HAIKU = br.PROBES[0], br.PROBES[6]


def test_an_answer_is_right_when_it_holds_every_expected_value():
    backup = br.Probe("when?", ("2:30", "nas01"))
    assert br._right(backup, "It runs at 02:30 and writes to \\\\NAS01\\backups.")
    assert not br._right(backup, "It runs at 02:30.")
    assert br._right(br.Probe("x", ("12288",)), "12,288 tokens")


def test_the_probes_cover_every_fact_and_some_unrelated_messages():
    assert any(not p.related for p in br.PROBES) and all(p.expected for p in br.PROBES if p.related)
    assert len(br.SCRIPT) >= len([p for p in br.PROBES if p.related])


def test_the_report_compares_before_and_after():
    outcomes = [br.Outcome("summary only", NVR, "I do not know.", 2.0, False),
                br.Outcome("summary + recall", NVR, "192.168.10.45", 2.4, True, 1, 0.71, 95),
                br.Outcome("summary only", HAIKU, "rain falls", 1.0, True),
                br.Outcome("summary + recall", HAIKU, "rain falls", 1.0, True, 0, 0.31, 80)]
    text = br.report("**Goal** run the office network", outcomes, 14, 9.5)
    assert f"| {NVR.question} | no | ✗ | ✓ | 1 | 0.71 | 95 |" in text
    assert f"| {HAIKU.question} | – | ✓ | ✓ | 0 | 0.31 | 80 |" in text
    assert "**summary only**: 0/1 right" in text and "**summary + recall**: 1/1 right" in text
    assert "14 pieces stored" in text and "## The summary" in text
