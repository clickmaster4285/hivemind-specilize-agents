"""Image request handling without a GPU: flags, sizes, model presets, prompt rewrite parsing."""

from __future__ import annotations

import dataclasses

import pytest

from src.images import image_gen
from src.core.config import Config
from src.images.image_models import (
    CHECKPOINT_DIR,
    parse_image_flags,
    preset_for,
    resolve_checkpoint,
    size_for_aspect,
)
from src.images.image_prompt import PromptRewrite, fit_prompt, parse_rewrite


# ── flags ───────────────────────────────────────────────────────────────

def test_midjourney_flags_are_parsed_and_removed():
    f = parse_image_flags("a red fox in snow --ar 16:9 --v 6.0 --style raw --seed 42")
    assert f.prompt == "a red fox in snow"
    assert f.aspect == (16, 9) and f.seed == 42
    assert f.ignored == ["--v 6.0", "--style raw"]


def test_no_flag_becomes_negative_prompt():
    f = parse_image_flags("city street at night --no people, cars --ar 3:2")
    assert f.prompt == "city street at night"
    assert f.negative == "people, cars" and f.aspect == (3, 2)


def test_aspect_in_words_and_plain_prompt():
    assert parse_image_flags("mountain lake, aspect ratio 21:9").aspect == (21, 9)
    assert parse_image_flags("poster in 9:16 format").aspect == (9, 16)
    plain = parse_image_flags("a cat -- sitting")
    assert plain.prompt == "a cat -- sitting" and plain.aspect is None and plain.seed is None


def test_bad_flag_values_are_not_swallowed():
    f = parse_image_flags("--ar wide a cat --seed abc")
    assert f.aspect is None and f.seed is None
    assert "a cat" in f.prompt


# ── sizes ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "aspect, base, expected",
    [
        (None, 768, (768, 768)),
        ((1, 1), 1024, (1024, 1024)),
        ((16, 9), 1024, (1344, 768)),
        ((9, 16), 1024, (768, 1344)),
        ((2, 3), 1024, (832, 1216)),
        ((16, 9), 768, (1024, 576)),
        ((5, 4), 1024, (1152, 896)),       # snaps to the nearest SDXL size
        ((10, 1), 1024, (1536, 640)),
    ],
)
def test_size_for_aspect(aspect, base, expected):
    w, h = size_for_aspect(aspect, base, base)
    assert (w, h) == expected and w % 64 == 0 and h % 64 == 0


# ── presets and checkpoints ─────────────────────────────────────────────

def test_presets_by_file_name():
    assert preset_for("models/checkpoints/RealVisXL_V5.0_fp16.safetensors").key == "realvisxl"
    lightning = preset_for("RealVisXL_V5.0_Lightning_fp16.safetensors")
    assert (lightning.key, lightning.steps, lightning.sampler) == ("realvisxl-lightning", 6, "dpmpp_sde_karras")
    assert preset_for("Juggernaut-XL_v9_RunDiffusionPhoto_v2.safetensors").key == "juggernaut"
    assert preset_for("sd_xl_base_1.0.safetensors").key == "sdxl-base"
    assert preset_for("something_else.safetensors").key == "custom"
    assert preset_for(None).key == "custom"


@pytest.mark.parametrize("bad", ["../secret.safetensors", "a/b.safetensors", "x.ckpt", "C:\\x.safetensors"])
def test_checkpoint_names_cannot_leave_the_folder(bad):
    with pytest.raises(ValueError):
        resolve_checkpoint(bad, None)


def test_checkpoint_default_and_name():
    assert resolve_checkpoint(None, CHECKPOINT_DIR / "d.safetensors") == CHECKPOINT_DIR / "d.safetensors"
    assert resolve_checkpoint("m.safetensors", None) == CHECKPOINT_DIR / "m.safetensors"


def test_quality_profile_sets_1024():
    cfg = Config.from_env().with_image_overrides(profile="quality")
    assert (cfg.image_width, cfg.image_height) == (1024, 1024)
    assert Config.from_env().with_image_overrides(profile="fast").image_width == 768


# ── LLM rewrite parsing ─────────────────────────────────────────────────

def test_parse_rewrite():
    raw = 'Sure!\n{"prompt": "portrait photo of an old tea seller, soft window light", "aspect": "2:3"}'
    assert parse_rewrite(raw) == PromptRewrite("portrait photo of an old tea seller, soft window light", (2, 3))
    assert parse_rewrite('{"prompt": "a long enough prompt here", "aspect": "7:5"}').aspect is None
    assert parse_rewrite("no json here") is None
    assert parse_rewrite('{"prompt": "short"}') is None
    assert parse_rewrite('{"prompt": broken') is None
    stray = parse_rewrite('{"prompt": "woman in a red saree on a rooftop, photorealistic style, 2:3", "aspect": "2:3"}')
    assert stray.prompt == "woman in a red saree on a rooftop, photorealistic style"
    other = parse_rewrite('{"prompt": "small wooden house in the rain, realistic photograph, 3:2 aspect ratio"}')
    assert other.prompt == "small wooden house in the rain, realistic photograph"


def test_long_prompts_are_cut_at_phrase_boundaries():
    phrases = [f"phrase number {i} with five" for i in range(20)]       # 100 words
    fitted = fit_prompt(", ".join(phrases))
    assert fitted == ", ".join(phrases[:11])                             # 55 words, whole phrases
    assert fit_prompt("short prompt, fine") == "short prompt, fine"


# ── run_image_gen wiring (fake image service) ───────────────────────────

class FakeClient:
    calls: list[dict] = []
    warmed: list = []

    def __init__(self, config):
        pass

    def health(self):
        return {"ok": True}

    def warm_in_background(self, checkpoint=None):
        FakeClient.warmed.append(checkpoint)

    def generate(self, mode, prompt, **kw):
        from PIL import Image

        FakeClient.calls.append({"mode": mode, "prompt": prompt, **kw})
        image = Image.new("RGB", (kw.get("width") or 64, kw.get("height") or 64))
        image.info["seed"] = kw.get("seed") if kw.get("seed") is not None else 777
        return image


@pytest.fixture
def fake_run(monkeypatch, tmp_path):
    FakeClient.calls = []
    FakeClient.warmed = []
    monkeypatch.setattr(image_gen, "ImageServiceClient", FakeClient)
    monkeypatch.setattr(image_gen, "unload_ollama_models", lambda config: [])
    monkeypatch.setattr(image_gen, "save_images", lambda config, job_id, rows: [{"filename": "generated.png"}])
    ckpt = tmp_path / "Juggernaut-XL_v9.safetensors"
    ckpt.write_bytes(b"x")
    cfg = dataclasses.replace(Config.from_env(), model_image_path=ckpt).with_image_overrides(profile="quality")
    return cfg


def test_run_uses_rewrite_preset_aspect_and_seed(monkeypatch, fake_run):
    monkeypatch.setattr(image_gen, "rewrite_prompt",
                        lambda q, c: PromptRewrite("portrait photo of an old fisherman, golden hour", (2, 3)))
    output, saved, errors, _ = image_gen.run_image_gen("old fisherman --seed 5 --v 6", job_id="j1", config=fake_run)
    call = FakeClient.calls[-1]
    assert not errors and saved
    assert call["prompt"] == "portrait photo of an old fisherman, golden hour"
    assert (call["width"], call["height"]) == (832, 1216)          # rewrite chose 2:3
    assert call["seed"] == 5
    assert call["guidance"] == 5.0                                  # Juggernaut caps the quality profile's 7.0
    assert call["negative_prompt"] == "lowres, blurry, watermark, deformed"
    assert "Juggernaut XL v9" in output and "--seed 5" in output and "--v 6" in output
    assert FakeClient.warmed == [None]      # loading started before the rewrite (None = the service default)


def test_user_aspect_wins_and_rewrite_failure_falls_back(monkeypatch, fake_run):
    monkeypatch.setattr(image_gen, "rewrite_prompt", lambda q, c: None)
    image_gen.run_image_gen("a red fox in snow --ar 16:9", job_id="j2", config=fake_run)
    call = FakeClient.calls[-1]
    assert (call["width"], call["height"]) == (1344, 768)
    assert call["prompt"].startswith("a red fox in snow")          # local polish
    assert call["seed"] is None                                     # service picks a random one


def test_fast_profile_uses_the_fast_checkpoint(monkeypatch, fake_run, tmp_path):
    monkeypatch.setattr(image_gen, "rewrite_prompt", lambda q, c: None)
    monkeypatch.setattr(image_gen, "CHECKPOINT_DIR", tmp_path)
    (tmp_path / "RealVisXL_V5.0_Lightning_fp16.safetensors").write_bytes(b"x")
    cfg = dataclasses.replace(fake_run, image_fast_checkpoint="RealVisXL_V5.0_Lightning_fp16.safetensors")
    output, *_ = image_gen.run_image_gen("a red fox", job_id="j3", config=cfg.with_image_overrides(profile="fast"))
    call = FakeClient.calls[-1]
    assert call["checkpoint"] == "RealVisXL_V5.0_Lightning_fp16.safetensors"
    assert (call["steps"], call["guidance"], call["width"]) == (6, 1.0, 1024)     # Lightning's own settings
    assert "Lightning" in output
    image_gen.run_image_gen("a red fox", job_id="j4", config=cfg)                 # quality profile
    assert FakeClient.calls[-1]["checkpoint"] == "Juggernaut-XL_v9.safetensors"       # quality: the default model


@pytest.mark.parametrize("url, expected", [
    ("http://localhost:11434", "http://127.0.0.1:11434"),
    ("http://localhost", "http://127.0.0.1"),
    ("https://localhost/api", "https://127.0.0.1/api"),
    ("http://localhostish:1", "http://localhostish:1"),
    ("http://192.168.1.5:11434", "http://192.168.1.5:11434"),
])
def test_localhost_becomes_ipv4(url, expected):
    from src.core.config import _loopback

    assert _loopback(url) == expected


def test_profiles_choose_model_size_and_hires(monkeypatch, fake_run, tmp_path):
    monkeypatch.setattr(image_gen, "rewrite_prompt", lambda q, c: None)
    monkeypatch.setattr(image_gen, "CHECKPOINT_DIR", tmp_path)
    (tmp_path / "RealVisXL_V5.0_Lightning_fp16.safetensors").write_bytes(b"x")
    cfg = dataclasses.replace(fake_run, image_fast_checkpoint="RealVisXL_V5.0_Lightning_fp16.safetensors")
    expected = {"fast": ("RealVisXL_V5.0_Lightning_fp16.safetensors", None),
                "balanced": ("RealVisXL_V5.0_Lightning_fp16.safetensors", 1.5),
                "quality": ("Juggernaut-XL_v9.safetensors", 1.5)}
    for profile, (ckpt, hires) in expected.items():
        image_gen.run_image_gen("a red fox", job_id=profile, config=cfg.with_image_overrides(profile=profile))
        call = FakeClient.calls[-1]
        assert (call["checkpoint"], call["hires"]) == (ckpt, hires), profile
        assert call["hires_checkpoint"] == "RealVisXL_V5.0_Lightning_fp16.safetensors"   # Lightning redraws


def test_long_prompts_are_condensed_not_cut():
    from src.images.image_prompt import needs_rewrite

    gta = ("YouTube thumbnail design for GTA V streaming. Style: Hyper-realistic, vibrant 8k, dynamic angle. "
           "Main subject: A muscular character in a luxury sports car speeding through a neon-lit city at night "
           "with rain effects. Text overlay area: Leave space on the right side for bold text like 'EASY PISA "
           "WITHDRAWAL'. Visual cues: A glowing green arrow pointing to a phone screen showing an 'EasyPaisa' logo "
           "and a JazzCash balance increasing rapidly. Color palette: Neon blues, purples, and bright gold accents.")
    assert len(gta.split()) > 55 and needs_rewrite(gta)
    assert needs_rewrite("a red fox")                                   # short: add detail
    rich = ("cinematic photo of a lighthouse at dusk, highly detailed, sharp focus, volumetric fog, "
            "8k, long exposure waves")
    assert not needs_rewrite(rich)                                      # already a good SDXL prompt


@pytest.mark.parametrize("query, warned", [
    ("thumbnail with bold text like 'EASY PAISA WITHDRAWAL'", True),
    ("an EasyPaisa logo on a phone screen", True),
    ("security room 500 sq ft with 10-foot walls, cameras angled at 45 degrees", True),
    ("a red sports car at night in the rain", False),
])
def test_text_and_logo_requests_get_a_note(monkeypatch, fake_run, query, warned):
    monkeypatch.setattr(image_gen, "rewrite_prompt", lambda q, c: None)
    output, *_ = image_gen.run_image_gen(query, job_id="t", config=fake_run)
    assert ("cannot write readable text" in output) is warned
