"""OpenAI-compatible JSON model adapter used by the separate editorial agents.

The adapter supports any compatible gateway. No key is stored in the workspace;
missing credentials cause stories to be held for review rather than invented.
"""
from __future__ import annotations

import json
import re
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
        payload = {
            "model": model,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user, ensure_ascii=False)},
            ],
        }
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=90)
        except requests.RequestException as exc:
            raise AgentUnavailable(redact(f"LLM request failed: {type(exc).__name__}", self.config.secret_values)) from exc
        if response.status_code >= 400:
            body = redact(response.text[:1000], self.config.secret_values)
            raise AgentUnavailable(f"LLM HTTP {response.status_code}: {body}")
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
            value = json.loads(text[start:end + 1])
        if not isinstance(value, dict):
            raise AgentUnavailable("LLM JSON root is not an object")
        return value
