"""O52: the chat model is loaded the way real calls use it, and loaded back after an image job — never against
the real Ollama here (requests and threads are stubbed)."""

from __future__ import annotations

import dataclasses

from src.core.config import Config
from src.jobs import worker
from src.llm import ollama_mgmt

CFG = Config.from_env()


class _Resp:
    def __init__(self, data=None):
        self._data, self.ok = data or {}, True

    def json(self):
        return self._data

    def raise_for_status(self):
        pass


def test_the_warm_up_uses_the_real_calls_context_size(monkeypatch):
    sent = []
    monkeypatch.setattr(ollama_mgmt.requests, "post", lambda url, json, timeout: sent.append(json) or _Resp())
    assert ollama_mgmt.warm_model(CFG)
    assert sent[0]["options"] == {"num_ctx": CFG.llm_num_ctx}          # else Ollama loads it a second time


def test_only_models_on_the_gpu_are_unloaded_for_a_picture(monkeypatch):
    ps = {"models": [{"name": "qwen3.5:9b", "size_vram": 5_900_000_000},
                     {"name": "qwen3-embedding:0.6b", "size_vram": 0}]}          # the embedding model is on the CPU
    monkeypatch.setattr(ollama_mgmt.requests, "get", lambda url, timeout: _Resp(
        ps if url.endswith("/api/ps") else {"models": [{"name": "qwen3.5:9b"}, {"name": "qwen3-embedding:0.6b"}]}))
    sent = []
    monkeypatch.setattr(ollama_mgmt.requests, "post", lambda url, json, timeout: sent.append(json["model"]) or _Resp())
    assert ollama_mgmt.unload_ollama_models(CFG) == ["qwen3.5:9b"] and sent == ["qwen3.5:9b"]


def test_after_a_picture_the_chat_model_is_loaded_back_unless_a_job_waits(monkeypatch):
    started = []

    class _Thread:
        def __init__(self, target, args, name, daemon):
            started.append(name)

        def start(self):
            pass

    monkeypatch.setattr(worker.threading, "Thread", _Thread)
    on = dataclasses.replace(CFG, image_unload_ollama=True)
    worker._rewarm_after_image(on, waiting=1)                          # the next job loads or unloads it itself
    worker._rewarm_after_image(dataclasses.replace(CFG, image_unload_ollama=False), waiting=0)
    assert started == []
    worker._rewarm_after_image(on, waiting=0)
    assert started == ["chat-model-rewarm"]
