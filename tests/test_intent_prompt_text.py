"""Router: asking to improve/write a prompt is text (chat), not image generation."""

from __future__ import annotations

import pytest

from src.config import Config
from src.intent_router import classify_query


def _route(text: str):
    return classify_query(text, requested_mode="auto", config=Config.from_env(), allow_llm=False)


@pytest.mark.parametrize(
    "text",
    [
        # the message from the screenshot (image words inside, but it asks for TEXT)
        "improve this prompt ,\n\ntitanic ship skining in the antratic ocean, highly detailed, sharp focus, natural lighting",
        "rewrite this prompt to be more cinematic: a cat on a roof, 8k",
        "give me a better prompt for a photorealistic portrait of an old man",
        "can you suggest prompt ideas for a fantasy landscape?",
    ],
)
def test_prompt_text_requests_go_to_chat(text):
    route = _route(text)
    assert route.resolved_mode == "chat"
    assert route.intent == "creative"   # creative: skips the knowledge brain


@pytest.mark.parametrize(
    "text",
    [
        "improve this prompt and generate the image: titanic in the antarctic ocean, highly detailed",
        "create an image of a seahorse in Antarctica",
        "photorealistic portrait of an old fisherman, 8k",
        "draw a lighthouse on a cliff at sunset",
    ],
)
def test_real_image_requests_still_draw(text):
    assert _route(text).intent == "image_gen"
