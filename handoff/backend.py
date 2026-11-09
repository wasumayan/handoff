"""Minimal OpenAI-compatible chat client (stdlib only).

Works against OpenAI, OpenRouter, Ollama (/v1), LM Studio, vLLM, llama.cpp server, etc.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from urllib.parse import urlparse

PRESETS = {
    "openai": ("https://api.openai.com/v1", "gpt-5"),
    "ollama": ("http://localhost:11434/v1", "qwen3.5"),
    "lmstudio": ("http://localhost:1234/v1", "local-model"),
    "openrouter": ("https://openrouter.ai/api/v1", "openai/gpt-5"),
}


# Largest context window to ask a local Ollama model for. The window is sized to each prompt up to
# this; more costs memory. Override with HANDOFF_NUM_CTX.
NUM_CTX_MAX = 32768


class BackendError(RuntimeError):
    pass


@dataclass
class Backend:
    base_url: str
    model: str
    api_key: str = ""
    timeout: int = 300
    last_usage: dict | None = field(default=None, repr=False)  # of the last OpenAI-style call

    @classmethod
    def from_env(cls, preset: str | None = None, model: str | None = None,
                 base_url: str | None = None) -> Backend | None:
        url = base_url or os.environ.get("HANDOFF_BASE_URL") or os.environ.get("OPENAI_BASE_URL")
        mdl = model or os.environ.get("HANDOFF_MODEL")
        key = os.environ.get("HANDOFF_API_KEY") or os.environ.get("OPENAI_API_KEY") or ""
        if preset:
            if preset not in PRESETS:
                raise BackendError(f"unknown backend preset {preset!r}; known: {', '.join(PRESETS)}")
            purl, pmodel = PRESETS[preset]
            url = base_url or purl
            mdl = mdl or pmodel
        if not url:
            return None
        if not mdl:
            raise BackendError("no model: set HANDOFF_MODEL or pass --model")
        return cls(base_url=url.rstrip("/"), model=mdl, api_key=key)

    @property
    def host(self) -> str:
        return self.base_url.split("//", 1)[-1].split("/", 1)[0]

    @property
    def is_ollama(self) -> bool:
        return urlparse(self.base_url).port == 11434

    def chat(self, messages: list[dict], *, json_mode: bool = True, temperature: float = 0.2,
             max_tokens: int = 4000) -> str:
        if self.is_ollama:
            return self._ollama_chat(messages, json_mode, temperature, max_tokens)
        msg = self.complete(messages, json_mode=json_mode, temperature=temperature,
                            max_tokens=max_tokens)
        return msg.get("content") or ""

    def _ollama_chat(self, messages: list[dict], json_mode: bool, temperature: float,
                     max_tokens: int) -> str:
        """Ollama's native API, so the context window fits the prompt. Its OpenAI-compatible endpoint
        uses the server default (4,096 tokens) and silently drops the start of longer prompts."""
        limit = int(os.environ.get("HANDOFF_NUM_CTX") or NUM_CTX_MAX)
        need = sum(len(m["content"]) for m in messages) // 3 + max_tokens  # ~3 chars/token, generous
        if need > limit:
            raise BackendError(f"prompt needs about {need:,} tokens, over HANDOFF_NUM_CTX={limit:,}")
        body: dict = {"model": self.model, "messages": messages, "stream": False, "think": False,
                      "options": {"num_ctx": max(8192, -(-need // 4096) * 4096),
                                  "temperature": temperature, "num_predict": max_tokens}}
        if json_mode:
            body["format"] = "json"
        root = self.base_url.removesuffix("/v1")
        payload = self._post(f"{root}/api/chat", body)
        self.last_usage = None  # sized above, so no truncation to detect
        msg = payload.get("message")
        if not isinstance(msg, dict):
            raise BackendError(f"unexpected response shape from {self.host}: {str(payload)[:300]}")
        return msg.get("content") or ""

    def _post(self, url: str, body: dict) -> dict:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            raise BackendError(f"{self.host} returned HTTP {e.code}: "
                               f"{e.read().decode(errors='replace')[:500]}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise BackendError(f"could not reach {self.host}: {e}") from e

    def complete(self, messages: list[dict], *, tools: list[dict] | None = None,
                 json_mode: bool = False, temperature: float = 0.2,
                 max_tokens: int = 4000) -> dict:
        """One chat completion; returns the assistant message dict (content, tool_calls, ...)."""
        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            body["tools"] = tools
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        try:
            payload = self._post(f"{self.base_url}/chat/completions", body)
        except BackendError as e:
            if json_mode and "HTTP 400" in str(e) and "response_format" in str(e):
                return self.complete(messages, tools=tools, json_mode=False,
                                     temperature=temperature, max_tokens=max_tokens)
            raise
        try:
            msg = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as e:
            raise BackendError(f"unexpected response shape from {self.host}: {str(payload)[:300]}") from e
        if not isinstance(msg, dict):
            raise BackendError(f"unexpected message shape from {self.host}")
        usage = payload.get("usage") or {}
        msg["_usage"] = self.last_usage = {"prompt_tokens": usage.get("prompt_tokens", 0),
                                           "completion_tokens": usage.get("completion_tokens", 0)}
        return msg
