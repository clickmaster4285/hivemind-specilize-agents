"""Embeddings with qwen3-embedding via Ollama (doc/BRAIN_DESIGN.md §4 step 6, §5 step 2).

Runs on the CPU (num_gpu=0) so the chat model keeps the 8GB GPU to itself.
Documents are embedded as-is; questions get Qwen3's instruction prefix, which is how the
model was trained to tell "a question looking for an answer" from "a passage".
"""

from __future__ import annotations

import requests

from src.core.config import Config

EMBED_DIM = 1024
BATCH_SIZE = 16
DEFAULT_TASK = "Given an IT question, retrieve documentation passages that answer it"


class EmbedError(RuntimeError):
    """The embedding model could not produce vectors."""


def _embed_batch(texts: list[str], config: Config) -> list[list[float]]:
    url = f"{config.llm_base_url}/api/embed"
    try:
        response = requests.post(
            url,
            json={
                "model": config.model_embed,
                "input": texts,
                "truncate": True,
                "options": {"num_gpu": 0},
            },
            timeout=config.timeout_seconds,
        )
        response.raise_for_status()
    except requests.ConnectionError as exc:
        raise EmbedError(f"Ollama is not reachable at {config.llm_base_url} — is it running?") from exc
    except requests.RequestException as exc:
        raise EmbedError(f"Embedding request failed ({config.model_embed}): {exc}") from exc

    vectors = response.json().get("embeddings") or []
    if len(vectors) != len(texts):
        raise EmbedError(f"Expected {len(texts)} embeddings, got {len(vectors)}")
    for vec in vectors:
        if len(vec) != EMBED_DIM:
            raise EmbedError(
                f"{config.model_embed} returned {len(vec)} dimensions; the database expects {EMBED_DIM}. "
                "Changing the embedding model needs a migration and re-embedding all chunks."
            )
    return vectors


def embed_texts(texts: list[str], config: Config | None = None) -> list[list[float]]:
    """Embed document passages, in batches."""
    config = config or Config.from_env()
    out: list[list[float]] = []
    for start in range(0, len(texts), BATCH_SIZE):
        out.extend(_embed_batch(texts[start : start + BATCH_SIZE], config))
    return out


def embed_query(question: str, config: Config | None = None, *, task: str = DEFAULT_TASK) -> list[float]:
    """Embed a user question with the query instruction."""
    return embed_texts([f"Instruct: {task}\nQuery: {question}"], config)[0]


def passage_text(title: str, heading_path: str, content: str) -> str:
    """What we embed for a chunk: its document title and section path give it context."""
    header = " > ".join(part for part in (title, heading_path) if part)
    return f"{header}\n\n{content}" if header else content
