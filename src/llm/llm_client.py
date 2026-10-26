"""Thin HTTP client for Ollama and OpenAI-compatible chat APIs."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from typing import Any

import requests

from src.core.config import Config
from src.llm.llm_lock import llm_slot


class LLMError(RuntimeError):
    """Raised when the LLM cannot complete a request."""


class LLMStopped(LLMError):
    """The user pressed Stop while the model was writing; `partial` holds the text so far."""

    def __init__(self, partial: str = "") -> None:
        super().__init__("Stopped by the user.")
        self.partial = partial


def _llm_err(exc: BaseException | None) -> str:
    """Short, readable cause (avoid truncated 'HTTPConnectionPool…')."""
    if exc is None:
        return "unknown"
    if isinstance(exc, requests.ConnectionError):
        return "connection refused — Ollama is not reachable"
    if isinstance(exc, requests.Timeout):
        return "timeout"
    if isinstance(exc, requests.HTTPError) and exc.response is not None:
        return f"HTTP {exc.response.status_code}: {(exc.response.text or '')[:160]}"
    msg = str(exc).strip() or type(exc).__name__
    return msg if len(msg) <= 220 else msg[:217] + "…"


class LLMClient:
    def __init__(self, config: Config, *, model: str | None = None, backend: str | None = None) -> None:
        self.config = config.with_model(model) if model else config
        self.backend = backend or self._infer_backend(self.config.llm_model)

    def _infer_backend(self, model: str) -> str:
        if model.endswith(":cloud") or model.startswith("cloud:"):
            return "remote_openai_compatible"
        if self.config.remote_llm_base_url and model.startswith("remote:"):
            return "remote_openai_compatible"
        return "local_ollama"

    def chat(
        self,
        *,
        system: str,
        user: str,
        temperature: float | None = None,
        max_retries: int = 2,
        model: str | None = None,
        images: list[str] | None = None,
        on_delta: Callable[[str], None] | None = None,
        should_continue: Callable[[], bool] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """on_delta / should_continue switch Ollama to streaming: on_delta gets the text so far,
        and should_continue() returning False stops the model mid-answer (raises LLMStopped).
        max_tokens caps the reply (Ollama num_predict) — for one-word answers such as a choice."""
        cfg = self.config.with_model(model) if model else self.config
        backend = self._infer_backend(cfg.llm_model)
        temp = cfg.temperature if temperature is None else temperature
        pics = [img for img in (images or []) if img]

        with llm_slot(cfg):
            if backend == "remote_openai_compatible":
                return self._chat_openai_compatible(cfg, system, user, temp, max_retries, pics)
            if on_delta is not None or should_continue is not None:
                return self._chat_ollama_stream(cfg, system, user, temp, max_retries, pics,
                                                on_delta, should_continue)
            return self._chat_ollama(cfg, system, user, temp, max_retries, pics, max_tokens)

    def _chat_ollama(
        self,
        cfg: Config,
        system: str,
        user: str,
        temperature: float,
        max_retries: int,
        images: list[str] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        url = f"{cfg.llm_base_url}/api/chat"
        user_msg: dict[str, Any] = {"role": "user", "content": user}
        if images:
            user_msg["images"] = images
        payload: dict[str, Any] = {
            "model": cfg.llm_model,
            "stream": False,
            "options": {"temperature": temperature, "num_ctx": cfg.llm_num_ctx},
            "messages": [
                {"role": "system", "content": system},
                user_msg,
            ],
        }
        if max_tokens:
            payload["options"]["num_predict"] = max_tokens
        if cfg.llm_think is not None:
            payload["think"] = cfg.llm_think
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = requests.post(url, json=payload, timeout=cfg.timeout_seconds)
                if (
                    response.status_code == 400
                    and "think" in payload
                    and "thinking" in (response.text or "").lower()
                ):
                    # Model has no thinking support — drop the flag and resend once.
                    payload.pop("think")
                    response = requests.post(url, json=payload, timeout=cfg.timeout_seconds)
                response.raise_for_status()
                data = response.json()
                content = data.get("message", {}).get("content")
                if not content or not str(content).strip():
                    raise LLMError("Local LLM returned an empty response.")
                return str(content).strip()
            except (requests.RequestException, ValueError, LLMError) as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
        raise LLMError(
            f"Failed to reach local LLM at {url} (model={cfg.llm_model}). "
            f"Last error: {_llm_err(last_error)}. Is Ollama running? (ollama serve)"
        )

    def chat_tools(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        model: str | None = None,
        temperature: float = 0.2,
        on_delta: Callable[[str], None] | None = None,
        should_continue: Callable[[], bool] | None = None,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Ollama native tool calling, streamed. The model either writes an answer (text streams to
        on_delta) or calls tools. → (text, [{"name": ..., "arguments": {...}}, ...])."""
        cfg = self.config.with_model(model) if model else self.config
        payload: dict[str, Any] = {
            "model": cfg.llm_model, "stream": True, "tools": tools, "messages": messages,
            "options": {"temperature": temperature, "num_ctx": cfg.llm_num_ctx},
        }
        if cfg.llm_think is not None:
            payload["think"] = cfg.llm_think
        keep_going = should_continue or (lambda: True)
        url = f"{cfg.llm_base_url}/api/chat"
        text, calls = "", []
        with llm_slot(cfg):
            try:
                with requests.post(url, json=payload, stream=True, timeout=(10, cfg.timeout_seconds)) as response:
                    if response.status_code == 400 and "think" in payload:
                        payload.pop("think")       # model without thinking support: resend without the flag
                        response.close()
                        response = requests.post(url, json=payload, stream=True, timeout=(10, cfg.timeout_seconds))
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not keep_going():
                            raise LLMStopped(text)
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise LLMError(str(data["error"]))
                        message = data.get("message") or {}
                        for call in message.get("tool_calls") or []:
                            fn = call.get("function") or {}
                            args = fn.get("arguments")
                            if isinstance(args, str):
                                try:
                                    args = json.loads(args)
                                except ValueError:
                                    args = {}
                            calls.append({"name": str(fn.get("name") or ""), "arguments": args or {}})
                        piece = message.get("content") or ""
                        if piece:
                            text += piece
                            if on_delta is not None and not calls:
                                on_delta(text)
                        if data.get("done"):
                            break
            except (requests.RequestException, ValueError) as exc:
                raise LLMError(
                    f"Failed to reach local LLM at {url} (model={cfg.llm_model}). Last error: {_llm_err(exc)}."
                ) from exc
        return text.strip(), calls

    def _chat_ollama_stream(
        self,
        cfg: Config,
        system: str,
        user: str,
        temperature: float,
        max_retries: int,
        images: list[str] | None,
        on_delta: Callable[[str], None] | None,
        should_continue: Callable[[], bool] | None,
    ) -> str:
        """Streaming /api/chat. Closing the response makes Ollama stop generating."""
        url = f"{cfg.llm_base_url}/api/chat"
        user_msg: dict[str, Any] = {"role": "user", "content": user}
        if images:
            user_msg["images"] = images
        payload: dict[str, Any] = {
            "model": cfg.llm_model,
            "stream": True,
            "options": {"temperature": temperature, "num_ctx": cfg.llm_num_ctx},
            "messages": [{"role": "system", "content": system}, user_msg],
        }
        if cfg.llm_think is not None:
            payload["think"] = cfg.llm_think
        keep_going = should_continue or (lambda: True)
        last_error: Exception | None = None
        attempt = 0
        while attempt <= max_retries:
            if not keep_going():
                raise LLMStopped("")
            text = ""
            try:
                # (connect timeout, timeout between two pieces of text) — a stuck model fails, a slow one does not
                with requests.post(url, json=payload, stream=True, timeout=(10, cfg.timeout_seconds)) as response:
                    if response.status_code == 400 and "think" in payload and "thinking" in (response.text or "").lower():
                        payload.pop("think")          # model without thinking support: resend (not a retry)
                        continue
                    response.raise_for_status()
                    for line in response.iter_lines():
                        if not keep_going():
                            raise LLMStopped(text)    # leaving the with-block closes the connection
                        if not line:
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise LLMError(str(data["error"]))
                        piece = (data.get("message") or {}).get("content") or ""
                        if piece:
                            text += piece
                            if on_delta is not None:
                                on_delta(text)
                        if data.get("done"):
                            break
                if not text.strip():
                    raise LLMError("Local LLM returned an empty response.")
                return text.strip()
            except LLMStopped:
                raise
            except (requests.RequestException, ValueError, LLMError) as exc:
                last_error = exc
                if text:                 # part of the answer was already shown: do not start over
                    break
                attempt += 1
                if attempt <= max_retries:
                    time.sleep(1.5 * attempt)
                    continue
                break
        raise LLMError(
            f"Failed to reach local LLM at {url} (model={cfg.llm_model}). "
            f"Last error: {_llm_err(last_error)}. Is Ollama running? (ollama serve)"
        )

    def _chat_openai_compatible(
        self,
        cfg: Config,
        system: str,
        user: str,
        temperature: float,
        max_retries: int,
        images: list[str] | None = None,
    ) -> str:
        base = cfg.remote_llm_base_url or cfg.llm_base_url
        # Ollama cloud models can still use local /v1 endpoint
        if not cfg.remote_llm_base_url and cfg.llm_model.endswith(":cloud"):
            base = cfg.llm_base_url
        url = f"{base}/v1/chat/completions"
        model_name = cfg.llm_model.removeprefix("remote:")
        headers = {"Content-Type": "application/json"}
        if cfg.remote_llm_api_key:
            headers["Authorization"] = f"Bearer {cfg.remote_llm_api_key}"
        if images:
            content: Any = [{"type": "text", "text": user}]
            for img in images:
                content.append(
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{img}"},
                    }
                )
            user_content = content
        else:
            user_content = user
        payload = {
            "model": model_name,
            "temperature": temperature,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
        }
        last_error: Exception | None = None
        for attempt in range(max_retries + 1):
            try:
                response = requests.post(
                    url, json=payload, headers=headers, timeout=cfg.timeout_seconds
                )
                response.raise_for_status()
                data = response.json()
                choices = data.get("choices") or []
                if not choices:
                    raise LLMError("Remote LLM returned no choices.")
                content = choices[0].get("message", {}).get("content")
                if not content or not str(content).strip():
                    raise LLMError("Remote LLM returned an empty response.")
                return str(content).strip()
            except (requests.RequestException, ValueError, LLMError, IndexError, KeyError) as exc:
                last_error = exc
                if attempt < max_retries:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                break
        raise LLMError(f"Failed remote LLM at {url} (model={model_name}). Last error: {last_error}")

    def ping(self) -> bool:
        try:
            response = requests.get(f"{self.config.llm_base_url}/api/tags", timeout=10)
            response.raise_for_status()
            return True
        except requests.RequestException:
            if self.config.remote_llm_base_url:
                try:
                    response = requests.get(
                        f"{self.config.remote_llm_base_url}/models",
                        headers=(
                            {"Authorization": f"Bearer {self.config.remote_llm_api_key}"}
                            if self.config.remote_llm_api_key
                            else {}
                        ),
                        timeout=10,
                    )
                    return response.ok
                except requests.RequestException:
                    return False
            return False

    def list_tags(self) -> list[dict[str, Any]]:
        response = requests.get(f"{self.config.llm_base_url}/api/tags", timeout=15)
        response.raise_for_status()
        return list(response.json().get("models") or [])

    def show_model(self, name: str) -> dict[str, Any]:
        response = requests.post(
            f"{self.config.llm_base_url}/api/show",
            json={"model": name},
            timeout=30,
        )
        response.raise_for_status()
        data = response.json()
        return data if isinstance(data, dict) else {}

    def generate_image(self, *, prompt: str, model: str, size: str = "1024x1024") -> bytes:
        """Ollama experimental OpenAI-compatible image API (`/v1/images/generations`)."""
        import base64

        cfg = self.config.with_model(model)
        url = f"{cfg.llm_base_url}/v1/images/generations"
        payload = {
            "model": model,
            "prompt": prompt,
            "size": size,
            "response_format": "b64_json",
        }
        try:
            response = requests.post(url, json=payload, timeout=cfg.timeout_seconds)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise LLMError(
                f"Image generation failed at {url} (model={model}). {exc}"
            ) from exc
        data = response.json() if response.content else {}
        rows = data.get("data") if isinstance(data, dict) else None
        if not isinstance(rows, list) or not rows:
            raise LLMError("Image API returned no image data.")
        b64 = str(rows[0].get("b64_json") or "").strip()
        if not b64:
            raise LLMError("Image API returned empty b64_json.")
        try:
            blob = base64.b64decode(b64, validate=False)
        except Exception as exc:  # noqa: BLE001
            raise LLMError("Image API returned invalid base64.") from exc
        if not blob:
            raise LLMError("Decoded image was empty.")
        return blob
