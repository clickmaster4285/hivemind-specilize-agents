"""The events every tool runner sends (src/tools/tool_run.py, ISSUES_LOG O97) — the order the chat feed expects."""

from src.tools.tool_run import ToolRun


def test_open_status_answer_end_in_that_order():
    events: list[dict] = []
    run = ToolRun(events.append, query="ping 10.0.0.5", job_id="j1", role="Ping", model="ping_host", accent="#3aa89a")
    run.status("Pinging…")
    out = run.finish("10.0.0.5 is up", [], questions=["which one?"])
    assert [e["type"] for e in events] == ["session_start", "agent_start", "status", "agent_done", "session_done"]
    assert events[1] == {"type": "agent_start", "agent_id": "assistant", "name": "Assistant", "role": "Ping",
                         "stage": "chat", "accent": "#3aa89a", "job_id": "j1", "model": "ping_host"}
    assert events[3]["output"] == "10.0.0.5 is up" and events[3]["questions"] == ["which one?"]
    assert out[:2] == ("10.0.0.5 is up", []) and out[2] == events[4]["total_seconds"] >= 0


def test_no_listener_and_no_accent():
    run = ToolRun(None, query="q", job_id=None, role="Weather", model="open-meteo")
    assert "accent" not in run.agent
    assert run.finish("sunny", ["x"])[:2] == ("sunny", ["x"])
