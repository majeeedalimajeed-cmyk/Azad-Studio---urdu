"""Data structures passed between agents."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class Candidate:
    source: str
    publisher: str
    url: str
    original_headline: str
    published_at: str | None
    updated_at: str | None
    language: str
    body_excerpt: str = ""
    category: str = ""
    image_url: str | None = None
    image_credit: str | None = None
    image_license: str | None = None
    external_links: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class VerificationResult:
    approved: bool
    reasons: list[str]
    evidence: list[str]
    confidence: str
    cluster_id: str | None = None
    priority: int = 5

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class EditorialContent:
    urdu_headline: str
    urdu_bullets: list[str]
    roman_headline: str
    roman_bullets: list[str]
    verification_notes: list[str] = field(default_factory=list)
    source_urls: list[str] = field(default_factory=list)
    publication_time: str | None = None
    media: list[dict[str, Any]] | dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if isinstance(data.get("media"), dict):
            data["media"] = [data["media"]]
        return data

    @staticmethod
    def _normalize_media(value: Any) -> list[dict[str, Any]] | None:
        if not value:
            return None
        if isinstance(value, dict):
            return [value]
        if isinstance(value, (list, tuple)):
            items = [v for v in value if isinstance(v, dict)]
            return items or None
        return None

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "EditorialContent":
        return cls(
            urdu_headline=value["urdu_headline"],
            urdu_bullets=list(value["urdu_bullets"]),
            roman_headline=value["roman_headline"],
            roman_bullets=list(value["roman_bullets"]),
            verification_notes=list(value.get("verification_notes", [])),
            source_urls=list(value.get("source_urls", [])),
            publication_time=value.get("publication_time"),
            media=cls._normalize_media(value.get("media")),
        )


@dataclass
class SourceResult:
    source: str
    ok: bool
    item_count: int = 0
    message: str = ""
    http_status: int | None = None
    candidates: list[Candidate] = field(default_factory=list)
