"""A change to the chat's last picture keeps its subject (ISSUES_LOG O111): "add a person beside this horse" after a
black Bulgarian horse in Russian snow was drawn as two brown horses in a sunlit meadow."""

from __future__ import annotations

import pytest

from src.core.config import Config
from src.images import image_prompt

HORSE = ("A black Bulgarian horse standing in a snowy Russian village, snow falling, overcast winter light, "
         "realistic photograph")
CHAT = [
    {"role": "user", "content": "create an image of a hourse black bulgarian horse that in the russia , and it is snow falling ,"},
    {"role": "assistant", "content": f"Generated with **RealVisXL V5.0 Lightning** at 1216×832, 6 steps.\n\n"
                                     f"Prompt used: `{HORSE}`\n\nSame image again: add `--seed 1383781807`."},
]


@pytest.mark.parametrize("query, change", [
    ("add a person beside this horse , with a simle face", True),
    ("now make it night", True),
    ("same scene but in summer", True),
    ("remove the snow", True),
    ("create an image of a red sports car on a mountain road", False),
    ("a cat sleeping on a sofa", False),
])
def test_a_change_is_told_from_a_new_picture(query, change):
    assert image_prompt.is_change(query) is change


def test_the_last_pictures_prompt_is_found():
    assert image_prompt.previous_prompt(CHAT) == HORSE
    plain = [CHAT[0], {"role": "assistant", "content": "Generated with **RealVisXL** at 1024×1024."}]
    assert image_prompt.previous_prompt(plain) == CHAT[0]["content"]          # drawn as asked: the request
    assert image_prompt.previous_prompt([{"role": "assistant", "content": "Hello"}]) == ""


def test_the_rewrite_is_given_the_previous_prompt(monkeypatch):
    sent = {}

    class FakeClient:
        def __init__(self, config):
            pass

        def chat(self, *, system, user, **_):
            sent.update(system=system, user=user)
            return '{"prompt": "a smiling man standing beside a black horse, snowy Russian village", "aspect": "3:2"}'

    monkeypatch.setattr("src.llm.llm_client.LLMClient", FakeClient)
    out = image_prompt.rewrite_prompt("add a person beside this horse", Config.from_env(), HORSE)
    assert sent["user"] == f"The previous picture's prompt: {HORSE}\nThe change the user asks for: add a person beside this horse"
    assert "put what is added or changed FIRST" in sent["system"]
    assert out is not None and out.prompt.startswith("a smiling man")
