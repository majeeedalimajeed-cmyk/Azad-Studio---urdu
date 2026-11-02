"""OpenAI-compatible JSON model adapter used by the separate editorial agents.

The adapter supports any compatible gateway. No key is stored in the workspace;
missing credentials cause stories to be held for review rather than invented.
"""
from __future__ import annotations

import json
import re
import time
from typing import Any

import requests

from .config import CONFIG
from .util import redact


class AgentUnavailable(RuntimeError):
    pass


class LLMClient:
    def __init__(self, config=CONFIG):
        self.config = config

    @property
    def available(self) -> bool:
        return bool(self.config.llm_api_key and self.config.llm_model)

    def _sleep(self, seconds: float) -> None:
        time.sleep(max(0.0, seconds))

    def _retry_delay(self, response: Any, attempt: int) -> float:
        base = float(getattr(self.config, "llm_retry_base_seconds", 2) or 2)
        cap = float(getattr(self.config, "llm_retry_max_seconds", 45) or 45)
        delay = base * (2 ** attempt)
        hint: float | None = None
        headers = getattr(response, "headers", None) or {}
        try:
            raw = headers.get("Retry-After") if hasattr(headers, "get") else None
        except Exception:
            raw = None
        if raw:
            try:
                hint = float(raw)
            except (TypeError, ValueError):
                hint = None
        if hint is None:
            text = getattr(response, "text", "") or ""
            m = re.search(r"try again in\s+([0-9.]+)\s*(ms|s|sec|seconds)?", text, re.I)
            if m:
                hint = float(m.group(1))
                if (m.group(2) or "").lower() == "ms":
                    hint /= 1000.0
        if hint is not None:
            delay = max(delay, hint)
        return min(cap, delay)

    def chat_json(self, *, system: str, user: dict[str, Any], model: str | None = None,
                  max_tokens: int = 1800) -> dict[str, Any]:
        if not self.available:
            raise AgentUnavailable("LLM model/key not configured; generation held for review")
        model = model or self.config.llm_model
        url = self.config.llm_api_base.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.llm_api_key}",
            "Content-Type": "application/json",
        }
        max_retries = max(0, int(getattr(self.config, "llm_max_retries", 4) or 0))
        use_json_mode = True
        rate_attempt = 0
        response = None
        while True:
            payload: dict[str, Any] = {
                "model": model,
                "temperature": 0.1,
                "max_tokens": max_tokens,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
                ],
            }
            if use_json_mode:
                payload["response_format"] = {"type": "json_object"}
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=90)
            except requests.RequestException as exc:
                if rate_attempt < max_retries:
                    self._sleep(min(float(getattr(self.config, "llm_retry_max_seconds", 45) or 45),
                                    float(getattr(self.config, "llm_retry_base_seconds", 2) or 2) * (2 ** rate_attempt)))
                    rate_attempt += 1
                    continue
                raise AgentUnavailable(redact(f"LLM request failed: {type(exc).__name__}", self.config.secret_values)) from exc
            if response.status_code >= 400:
                body_text = getattr(response, "text", "") or ""
                if use_json_mode and response.status_code == 400 and (
                    "response_format" in body_text or "json_validate_failed" in body_text):
                    # Some OpenAI-compatible gateways (e.g. Groq-hosted models)
                    # reject JSON mode. Retry without it; _parse_json still
                    # enforces a JSON object in the reply. This does not
                    # consume the rate-limit retry budget.
                    use_json_mode = False
                    continue
                if (response.status_code == 429 or response.status_code >= 500) and rate_attempt < max_retries:
                    self._sleep(self._retry_delay(response, rate_attempt))
                    rate_attempt += 1
                    continue
                body = redact(body_text[:1000], self.config.secret_values)
                raise AgentUnavailable(f"LLM HTTP {response.status_code}: {body}")
            break
        try:
            data = response.json()
            text = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise AgentUnavailable("LLM response shape invalid") from exc
        return self._parse_json(text)

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        text = text.strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S)
        try:
            value = json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            if start < 0 or end <= start:
                raise AgentUnavailable("LLM did not return JSON")
            try:
                value = json.loads(text[start:end + 1])
            except json.JSONDecodeError as exc:
                raise AgentUnavailable("LLM returned invalid JSON") from exc
        if not isinstance(value, dict):
            raise AgentUnavailable("LLM JSON root is not an object")
        return value
