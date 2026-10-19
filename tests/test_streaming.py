"""L3: streamed answers and Stop — against a fake Ollama that writes slowly (no real model needed)."""

from __future__ import annotations

import dataclasses
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from src import live
from src.config import Config
from src.llm_client import LLMClient, LLMError, LLMStopped

WORDS = ["Hello", " there", ",", " this", " is", " a", " streamed", " answer", "."]


class FakeOllama(BaseHTTPRequestHandler):
    requests_seen: list[dict] = []
    reject_think = False
    delay = 0.05

    def log_message(self, *args):  # keep test output clean
        pass

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests_seen.append(body)
        if FakeOllama.reject_think and "think" in body:
            msg = b'{"error":"model does not support thinking"}'
            self.send_response(400)
            self.send_header("Content-Length", str(len(msg)))
            self.end_headers()
            self.wfile.write(msg)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        try:
            for word in WORDS:
                self.wfile.write((json.dumps({"message": {"content": word}, "done": False}) + "\n").encode())
                self.wfile.flush()
                time.sleep(FakeOllama.delay)
            self.wfile.write((json.dumps({"message": {"content": ""}, "done": True}) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass  # the client stopped reading: exactly what Stop does


@pytest.fixture
def ollama():
    FakeOllama.requests_seen = []
    FakeOllama.reject_think = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), FakeOllama)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    cfg = dataclasses.replace(Config.from_env(), llm_base_url=f"http://127.0.0.1:{server.server_port}",
                              llm_model="qwen3.5:9b", llm_think=False, timeout_seconds=5)
    yield cfg
    server.shutdown()


def test_text_arrives_piece_by_piece(ollama):
    seen = []
    text = LLMClient(ollama).chat(system="s", user="u", on_delta=seen.append, max_retries=0)
    assert text == "".join(WORDS)
    assert len(seen) == len(WORDS) and seen[0] == "Hello" and seen[-1] == text
    assert FakeOllama.requests_seen[-1]["stream"] is True


def test_stop_ends_the_answer_midway_and_keeps_the_text(ollama):
    seen = []
    started = time.monotonic()
    with pytest.raises(LLMStopped) as stopped:
        LLMClient(ollama).chat(system="s", user="u", on_delta=seen.append, max_retries=0,
                               should_continue=lambda: len(seen) < 3)
    assert stopped.value.partial == "Hello there,"
    assert time.monotonic() - started < 0.5          # did not wait for the whole answer


def test_think_flag_resend_is_not_a_retry(ollama):
    FakeOllama.reject_think = True
    text = LLMClient(ollama).chat(system="s", user="u", on_delta=lambda t: None, max_retries=0)
    assert text == "".join(WORDS)
    assert "think" in FakeOllama.requests_seen[0] and "think" not in FakeOllama.requests_seen[1]


def test_unreachable_model_gives_a_clear_error():
    cfg = dataclasses.replace(Config.from_env(), llm_base_url="http://127.0.0.1:9", llm_model="qwen3.5:9b")
    with pytest.raises(LLMError, match="not reachable"):
        LLMClient(cfg).chat(system="s", user="u", on_delta=lambda t: None, max_retries=0)


def test_non_streaming_calls_are_unchanged(ollama, monkeypatch):
    # no on_delta / should_continue → the old single-response path
    called = {}
    monkeypatch.setattr(LLMClient, "_chat_ollama", lambda self, *a, **k: called.setdefault("plain", "ok"))
    assert LLMClient(ollama).chat(system="s", user="u") == "ok"


def test_live_buffer_and_stop_flag():
    live.clear("job-x")
    live.set_text("job-x", "assistant", "Hel")
    live.set_text("job-x", "assistant", "Hello")
    assert live.snapshot("job-x") == {"assistant": "Hello"}
    live.finish_agent("job-x", "assistant")
    assert live.snapshot("job-x") == {}
    assert not live.is_stopped("job-x")
    live.stop("job-x")
    assert live.is_stopped("job-x")
    live.clear("job-x")
    assert not live.is_stopped("job-x")
    live.set_text(None, "assistant", "ignored")         # jobs without id (tests, CLI) are fine


def test_run_chat_streams_and_stops(ollama, monkeypatch):
    from src import chat_runner

    monkeypatch.setattr(chat_runner, "model_loaded", lambda config, model: True)
    events = []
    live.clear("job-s")
    seen_live = []
    original = live.set_text
    monkeypatch.setattr(live, "set_text", lambda j, a, t: (seen_live.append(t), original(j, a, t)))
    result = chat_runner.run_chat("hi there friend", config=ollama, job_id="job-s", on_progress=events.append,
                                  should_continue=lambda: len(seen_live) < 4)
    assert result.stopped and result.output.startswith("Hello there, this") and "stopped" in result.output
    done = [e for e in events if e["type"] == "agent_done"][0]
    assert done["role"] == "Stopped"
    statuses = [e["message"] for e in events if e["type"] == "status"]
    assert statuses == ["Thinking…", "Writing…"]
    assert live.snapshot("job-s") == {}                  # nothing left to stream after the final event


def test_friendly_errors():
    from src.chat_runner import friendly_error

    cfg = Config.from_env()
    assert "not running" in friendly_error(LLMError("connection refused — Ollama is not reachable"), cfg)
    assert "no new text" in friendly_error(LLMError("Last error: timeout"), cfg)
    assert "GPU memory" in friendly_error(LLMError("CUDA error: out of memory"), cfg)
