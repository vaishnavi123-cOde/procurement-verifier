"""LLM provider abstraction.

Three backends:

- ``NoneProvider`` (deterministic mode) — no network, extraction agents rely on
  heuristics only. This is the default and guarantees the pipeline works offline.
- ``OllamaLLMProvider`` — local model through Ollama's HTTP API.
- ``APILLMProvider`` — any OpenAI-compatible ``/chat/completions`` endpoint.

Agents never bypass this facade to talk to a model. All LLM output is best-effort:
agents must fall back to deterministic logic on any failure.
"""

from __future__ import annotations

import json
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional

import httpx

from backend.app.config import settings


@dataclass
class LLMResponse:
    text: str = ""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None


class LLMProvider(ABC):
    provider_name: str = "none"

    @property
    @abstractmethod
    def available(self) -> bool:
        ...

    @abstractmethod
    def complete(self, messages: list[dict], temperature: float = 0.0,
                 max_tokens: int = 2048, json_mode: bool = False) -> LLMResponse:
        ...


class NoneProvider(LLMProvider):
    provider_name = "none"

    @property
    def available(self) -> bool:
        return False

    def complete(self, messages, temperature=0.0, max_tokens=2048, json_mode=False) -> LLMResponse:
        return LLMResponse(error="LLM provider disabled (LLM_PROVIDER=none).")


class OllamaLLMProvider(LLMProvider):
    provider_name = "ollama"

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 timeout: float | None = None):
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.model = model or settings.ollama_model
        self.timeout = timeout or settings.llm_timeout_seconds

    @property
    def available(self) -> bool:
        try:
            with httpx.Client(timeout=2.0) as client:
                resp = client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False

    def complete(self, messages, temperature=0.0, max_tokens=2048, json_mode=False) -> LLMResponse:
        try:
            with httpx.Client(timeout=self.timeout) as client:
                payload: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "options": {"temperature": temperature, "num_predict": max_tokens},
                }
                if json_mode:
                    payload["format"] = "json"
                resp = client.post(f"{self.base_url}/api/chat", json=payload)
                resp.raise_for_status()
                data = resp.json()
                text = data.get("message", {}).get("content", "")
                return LLMResponse(text=text)
        except Exception as exc:
            return LLMResponse(error=f"Ollama error: {exc}")


class APILLMProvider(LLMProvider):
    provider_name = "api"

    def __init__(self, base_url: str | None = None, model: str | None = None,
                 api_key: str = "", timeout: float | None = None):
        self.base_url = (base_url or settings.llm_api_base_url).rstrip("/")
        self.model = model or settings.llm_api_model
        self.api_key = api_key or settings.llm_api_key
        self.timeout = timeout or settings.llm_timeout_seconds

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(self, messages, temperature=0.0, max_tokens=2048, json_mode=False) -> LLMResponse:
        if not self.api_key:
            return LLMResponse(error="API provider requires LLM_API_KEY.")
        try:
            with httpx.Client(timeout=self.timeout) as client:
                payload: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                }
                if json_mode:
                    payload["response_format"] = {"type": "json_object"}
                resp = client.post(
                    f"{self.base_url}/chat/completions",
                    json=payload,
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                resp.raise_for_status()
                data = resp.json()
                choice = data["choices"][0]["message"]["content"]
                usage = data.get("usage", {})
                return LLMResponse(
                    text=choice,
                    prompt_tokens=usage.get("prompt_tokens", 0),
                    completion_tokens=usage.get("completion_tokens", 0),
                )
        except Exception as exc:
            return LLMResponse(error=f"API error: {exc}")


class LLM:
    """Facade with best-effort structured generation and observability."""

    def __init__(self, provider: LLMProvider | None = None):
        self.provider = provider or self._build_provider()

    @staticmethod
    def _build_provider() -> LLMProvider:
        mode = settings.llm_provider
        if mode == "ollama":
            return OllamaLLMProvider()
        if mode == "api":
            return APILLMProvider()
        return NoneProvider()

    @property
    def available(self) -> bool:
        return self.provider.available

    @property
    def model_name(self) -> str:
        return getattr(self.provider, "model", "")

    def complete(self, messages: list[dict], **kwargs) -> LLMResponse:
        return self.provider.complete(messages, **kwargs)

    def generate_json(self, system: str, user: str, *,
                      example: str = "", max_tokens: int = 2048,
                      case_id: str | None = None, request_id: str | None = None,
                      agent: str = "") -> Optional[dict[str, Any]]:
        """Request structured JSON. Returns parsed dict or None on any failure."""
        if not self.available:
            return None
        messages = [{"role": "system", "content": system}]
        if example:
            messages.append({"role": "user", "content": example})
        messages.append({"role": "user", "content": user})
        start = time.monotonic()
        resp = self.provider.complete(messages, json_mode=True, max_tokens=max_tokens)
        duration_ms = int((time.monotonic() - start) * 1000)
        self._log_call(messages, resp, duration_ms, case_id, request_id, agent)
        if not resp.ok:
            return None
        try:
            cleaned = resp.text.strip()
            if cleaned.startswith("```"):
                cleaned = cleaned.strip("`")
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            data = json.loads(cleaned)
            return data if isinstance(data, dict) else None
        except (json.JSONDecodeError, ValueError):
            # Try to salvage a JSON object embedded in text
            return self._salvage_json(resp.text)

    @staticmethod
    def _salvage_json(text: str) -> Optional[dict]:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except (json.JSONDecodeError, ValueError):
                return None
        return None

    def _log_call(self, messages, resp: LLMResponse, duration_ms: int,
                  case_id, request_id, agent) -> None:
        try:
            from backend.app.repositories import store
            from backend.app.database.engine import SessionLocal
            with SessionLocal() as db:
                store.add_llm_call(
                    db,
                    case_id=case_id or "",
                    request_id=request_id or uuid.uuid4().hex,
                    agent=agent or "llm",
                    provider=self.provider.provider_name,
                    model=self.model_name,
                    prompt_preview=json.dumps(messages)[:2000],
                    response_preview=(resp.text or "")[:2000],
                    prompt_tokens=resp.prompt_tokens or None,
                    completion_tokens=resp.completion_tokens or None,
                    duration_ms=duration_ms,
                    error=resp.error,
                )
                db.commit()
        except Exception:
            pass  # observability must never break the pipeline
        try:
            from backend.app.observability.logger import emit

            emit(
                "llm_call",
                level=20,
                request_id=request_id or "",
                case_id=case_id or "",
                agent=agent or "llm",
                provider=self.provider.provider_name,
                model=self.model_name,
                duration_ms=duration_ms,
                status="error" if resp.error else "ok",
                **({"error": resp.error} if resp.error else {}),
            )
        except Exception:
            pass

    def __repr__(self) -> str:
        return f"<LLM provider={self.provider.provider_name} available={self.available}>"


_default_llm: Optional[LLM] = None


def get_llm() -> LLM:
    global _default_llm
    if _default_llm is None:
        _default_llm = LLM()
    return _default_llm


def reset_llm() -> None:
    global _default_llm
    _default_llm = None