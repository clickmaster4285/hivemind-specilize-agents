"""The chat recall eval v2 (O63): scoring, folding and the report run offline; the real run needs Ollama and the
database."""

from __future__ import annotations

import dataclasses

from src.core.config import Config
from src.llm import bench_recall as br

NVR, HAIKU = br.PROBES[0], br.PROBES[-2]
CFG = dataclasses.replace(Config.from_env(), llm_num_ctx=12288, context_history_share=0.35)   # budget ~4,300
LONG_ANSWER = "word " * 480                                 # ~600 tokens: a written answer's size


def _exchanges() -> list[list[dict[str, str]]]:
    return [[{"role": "user", "content": message}, {"role": "assistant", "content": given or LONG_ANSWER}]
            for message, given in br.SCRIPT]


def test_an_answer_is_right_when_it_holds_every_expected_value():
    backup = br.Probe("when?", ("2:30", "nas01"))
    assert br._holds(backup, "It runs at 02:30 and writes to \\\\NAS01\\backups.")
    assert not br._holds(backup, "It runs at 02:30.")
    assert br._holds(br.Probe("x", ("12288",)), "12,288 tokens")


def test_every_fact_is_in_the_chat_and_unrelated_probes_are_there():
    text = br._normal(" ".join(f"{m} {a or ''}" for m, a in br.SCRIPT))
    for probe in br.PROBES:
        if probe.related and probe.expected != ("channel 7",):            # "Channel 7 is our parking camera"
            assert all(value in text for value in probe.expected), probe.question
    assert "channel 7" in text and any(not p.related for p in br.PROBES)


def test_the_chat_folds_in_rounds_like_the_app_and_every_fact_is_folded():
    summaries: list[str] = []

    def summarize(previous, turns, *, config):
        summaries.append(previous)
        return f"summary #{len(summaries)}"

    chat = br.fold_like_the_app(CFG, _exchanges(), summarize_fn=summarize)
    assert chat.rounds >= 4 and chat.rounds == len(summaries)
    assert summaries[1] == "summary #1"                             # each round rewrites the previous summary
    assert len(chat.folded) + len(chat.recent) == len(br.SCRIPT)
    recent = br._normal(" ".join(t["content"] for e in chat.recent for t in e))
    for probe in br.PROBES:
        assert not (probe.related and br._holds(probe, recent)), f"not folded: {probe.question}"


def test_the_report_compares_before_and_after():
    chat = br.Folded("**Goal** run the office network", [[]] * 20, [[]] * 3, 6)
    outcomes = [br.Outcome("summary only", NVR, "I do not know.", 2.0, False),
                br.Outcome("summary + recall", NVR, "192.168.10.45", 2.4, True, 1, True, 0.71, 0.71, 95),
                br.Outcome("summary only", HAIKU, "rain falls", 1.0, True),
                br.Outcome("summary + recall", HAIKU, "rain falls", 1.0, True, 0, False, 0.31, 0.0, 80)]
    text = br.report(chat, outcomes, 64, 40.5)
    assert "20 of 23 exchanges folded in 6 rounds" in text and "64 pieces stored in 40.5 s" in text
    assert f"| {NVR.question} | ✗ | ✗ | ✓ | ✓ | 1 | 0.71 | 0.71 | 95 |" in text
    assert f"| {HAIKU.question} | – | ✓ | ✓ | – | 0 | – | 0.31 | 80 |" in text
    assert "**summary only**: 0/1 right" in text and "**summary + recall**: 1/1 right" in text
