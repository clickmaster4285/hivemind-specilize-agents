"""SDXL memory fixes: load on first use, unload after idle, never unload mid-generation.

Uses fake pipeline objects — the real 7 GB model is never loaded here.
"""

from __future__ import annotations

import time

import pytest

from src.images import sdxl_pipeline as sdxl


@pytest.fixture(autouse=True)
def fake_loaded_model():
    sdxl._txt2img, sdxl._img2img = object(), object()
    sdxl._pipeline_path, sdxl._warm_state = "fake.safetensors", "ready"
    sdxl._in_use, sdxl._last_used = 0, time.monotonic()
    yield
    sdxl._txt2img = sdxl._img2img = sdxl._pipeline_path = None
    sdxl._warm_state, sdxl._in_use = "cold", 0


def test_not_unloaded_before_idle_time():
    assert sdxl.unload_if_idle(600) is False
    assert sdxl.pipeline_status()["loaded"] is True


def test_unloaded_after_idle_time():
    sdxl._last_used = time.monotonic() - 601
    assert sdxl.unload_if_idle(600) is True
    status = sdxl.pipeline_status()
    assert (status["loaded"], status["state"], status["path"]) == (False, "cold", None)


def test_never_unloaded_while_generating():
    sdxl._last_used = time.monotonic() - 10_000
    seen: dict = {}

    @sdxl._tracked
    def fake_generation():
        seen["in_use"] = sdxl.pipeline_status()["in_use"]
        seen["unloaded_mid_job"] = sdxl.unload_if_idle(0)
        return "image"

    assert fake_generation() == "image"
    assert seen == {"in_use": 1, "unloaded_mid_job": False}
    assert sdxl.pipeline_status()["in_use"] == 0
    # the job just finished, so the idle clock restarted
    assert sdxl.unload_if_idle(600) is False


def test_in_use_count_recovers_after_a_failed_generation():
    @sdxl._tracked
    def broken():
        raise RuntimeError("CUDA out of memory")

    with pytest.raises(RuntimeError):
        broken()
    assert sdxl.pipeline_status()["in_use"] == 0


def test_generate_functions_are_tracked():
    for fn in (sdxl.generate, sdxl.generate_from_image, sdxl.generate_inpaint):
        assert hasattr(fn, "__wrapped__"), f"{fn.__name__} must be wrapped by _tracked"
