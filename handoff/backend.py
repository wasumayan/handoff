"""Minimal OpenAI-compatible chat client (stdlib only).

Works against OpenAI, OpenRouter, Ollama (/v1), LM Studio, vLLM, llama.cpp server, etc.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass

PRESETS = {
    "openai": ("https://api.openai.com/v1", "gpt-5"),
    "ollama": ("http://localhost:11434/v1", "llama3.1"),
    "lmstudio": ("http://localhost:1234/v1", "local-model"),
    "openrouter": ("https://openrouter.ai/api/v1", "openai/gpt-5"),
}


class BackendError(RuntimeError):
    pass


@dataclass
class Backend:
    base_url: str
    model: str
    api_key: str = ""
    timeout: int = 180

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

    def chat(self, messages: list[dict], *, json_mode: bool = True, temperature: float = 0.2,
             max_tokens: int = 4000) -> str:
        body: dict = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=data, method="POST",
            headers={"Content-Type": "application/json",
                     **({"Authorization": f"Bearer {self.api_key}"} if self.api_key else {})},
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                payload = json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            if json_mode and e.code == 400 and "response_format" in detail:
                return self.chat(messages, json_mode=False, temperature=temperature,
                                 max_tokens=max_tokens)
            raise BackendError(f"{self.host} returned HTTP {e.code}: {detail}") from e
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            raise BackendError(f"could not reach {self.host}: {e}") from e
        try:
            return payload["choices"][0]["message"]["content"] or ""
        except (KeyError, IndexError, TypeError) as e:
            raise BackendError(f"unexpected response shape from {self.host}: {str(payload)[:300]}") from e
