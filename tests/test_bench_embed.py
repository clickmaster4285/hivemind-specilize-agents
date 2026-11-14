"""bench_embed's report (no Ollama): one column per placement, every measured row present."""

from __future__ import annotations

from src.llm.bench_embed import PLACEMENTS, Result, report


def test_the_cpu_placement_is_the_apps():
    from src.brain.embed import CPU_ONLY

    assert PLACEMENTS["CPU (the app)"] is CPU_ONLY and PLACEMENTS["GPU"] == {}


def test_the_report_has_a_column_per_placement():
    cpu = Result("CPU (the app)", 1.7, "0%", -5, 78, 727, 118, 20.9)
    gpu = Result("GPU", 7.2, "10%", 207, 69, 219, 276, 20.6)
    table = report([cpu, gpu], 21.2).splitlines()
    assert table[0] == "| | CPU (the app) | GPU |"
    assert "| One search query (median) | 78 ms | 69 ms |" in table
    assert any("alone: 21.2 tok/s" in row and "20.9 tok/s" in row for row in table)
