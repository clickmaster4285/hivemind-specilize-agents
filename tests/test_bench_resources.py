"""The resource benchmark's report and its size check (src/llm/bench_resources.py, ISSUES_LOG O58). Offline: the
measuring itself needs Ollama and is run by the user."""

import pytest

from src.llm.bench_resources import Run, main, report


def test_the_report_puts_each_context_size_side_by_side():
    small = Run(8192, size_gb=5.7, gpu_gb=5.7, ram_gb=[6.0], cpu_pct=[100.0], chat_still_loaded=True)
    large = Run(16384, size_gb=6.7, gpu_gb=5.6, ram_gb=[10.0], cpu_pct=[900.0], chat_still_loaded=False)
    table = report([small, large], 8192)
    assert table.splitlines()[0].split()[-2:] == ["8192", "16384"]
    cpu_share = next(line for line in table.splitlines() if "on the CPU" in line).split()
    assert cpu_share[-2:] == ["0%", "16%"]
    assert "NO (pushed out)" in table and "GPU memory peak (MB of 8192)" in table


def test_a_prompt_that_does_not_fit_the_smallest_size_is_refused():
    with pytest.raises(SystemExit):
        main(["--ctx", "4096,8192", "--prompt-tokens", "6000"])
