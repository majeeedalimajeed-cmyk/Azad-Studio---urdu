"""Pixabay Content License photo provider for AZAD STUDIO cards.

The API key is read from configuration only. Search responses are cached for
24 hours without the key; images are downloaded to the local media directory
instead of being hot-linked. Only safe-search, photo results above the minimum
resolution are accepted. No AI-generated image is ever treated as a news photo.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from PIL import Image

from .config import CONFIG
from .db import Database
from .util import clean_text, iso, json_dumps, redact, sha256_file, sha256_text

LATIN_WORD = re.compile(r"[A-Za-z][A-Za-z0-9-]{2,}")
QUERY_STOPWORDS = {
    "news", "report", "reports", "reported", "update", "updates", "today",
    "said", "says", "according", "after", "before", "over", "into", "with",
    "from", "that", "this", "these", "those", "will", "would", "could",
    "have", "has", "had", "was", "were", "are", "and", "the", "for", "not",
    "but", "all", "new", "latest", "live", "read", "more", "less", "via",
}


class PixabayClient:
    def __init__(self, db: Database | None = None, config=CONFIG,
                 session: requests.Session | None = None):
        self.db = db or Database(config.db_path)
        self.config = config
        self.session = session or requests.Session()
        self.session.headers.update({
            "User-Agent": config.user_agent,
            "Accept": "application/json, image/jpeg, image/png, */*",
        })
        self.cache_dir = config.media_dir / "pixabay_cache"
        self.image_dir = config.media_dir / "pixabay"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self._last_request = 0.0

    @property
    def available(self) -> bool:
        return bool(self.config.pixabay_enabled and self.config.pixabay_api_key)

    @staticmethod
    def build_query(candidate: Any, *, urdu: dict[str, Any] | None = None,
                    facts: dict[str, Any] | None = None) -> str:
        """Build a short English search phrase from verified story context."""
        if hasattr(candidate, "to_dict"):
            candidate = candidate.to_dict()
        elif not isinstance(candidate, dict):
            candidate = {}
        parts: list[str] = []
        facts = facts or {}
        for key in ("locations", "names"):
            for value in facts.get(key, []) or []:
                parts.append(clean_text(str(value), 50))
        if candidate.get("category"):
            parts.append(clean_text(str(candidate["category"]), 60))
        if urdu and urdu.get("urdu_headline"):
            # Urdu is kept out of the English query; Roman/English names in the
            # source excerpt and fact inventory are more useful to Pixabay.
            pass
        text = " ".join([
            str(candidate.get("original_headline", "")),
            str(candidate.get("body_excerpt", "")),
        ])
        for word in LATIN_WORD.findall(text):
            if word.casefold() in QUERY_STOPWORDS:
                continue
            parts.append(word)
        seen: set[str] = set()
        ordered: list[str] = []
        for part in parts:
            part = clean_text(part, 60)
            if not part or len(part) < 3:
                continue
            key = part.casefold()
            if key in seen:
                continue
            seen.add(key)
            ordered.append(part)
        query = " ".join(ordered)[:100].strip()
        return query

    def _rate_limit(self) -> None:
        # Pixabay allows 100 requests/60s per key; a one-second floor is ample
        # for two photos per story and protects the shared API.
        wait = 1.0 - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)
        self._last_request = time.monotonic()

    def _cache_path(self, params: dict[str, Any]) -> Path:
        safe = {k: v for k, v in params.items() if k != "key"}
        return self.cache_dir / (sha256_text(json_dumps(safe))[:24] + ".json")

    def _read_cache(self, path: Path) -> list[dict[str, Any]] | None:
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
                return None
            if time.time() - float(data.get("cached_at", 0)) > self.config.pixabay_cache_ttl:
                return None
            return [h for h in data["hits"] if isinstance(h, dict)]
        except (OSError, ValueError, TypeError):
            return None

    @staticmethod
    def _sanitize_hit(hit: dict[str, Any]) -> dict[str, Any]:
        keys = (
            "id", "pageURL", "type", "tags", "webformatURL", "webformatWidth",
            "webformatHeight", "largeImageURL", "imageWidth", "imageHeight",
            "user", "user_id",
        )
        return {k: hit.get(k) for k in keys if hit.get(k) is not None}

    def _log(self, status: str, action: str, message: str, **details: Any) -> None:
        self.db.insert_api_event(
            "pixabay", action, status,
            redact(message, self.config.secret_values),
            details=details,
        )

    @staticmethod
    def relax_query(query: str) -> list[str]:
        """Candidate queries from most to least specific.

        Pixabay matches every query term, so a long story-derived phrase often
        returns zero hits. Retrying with the leading 3, then 2, terms keeps the
        photo topical while avoiding an unrelated generic image.
        """
        words = query.split()
        candidates = [query]
        if len(words) > 3:
            candidates.append(" ".join(words[:3]))
        if len(words) > 2:
            candidates.append(" ".join(words[:2]))
        seen: set[str] = set()
        out: list[str] = []
        for cand in candidates:
            key = cand.casefold()
            if cand and key not in seen:
                seen.add(key)
                out.append(cand)
        return out

    def search(self, query: str, *, per_page: int | None = None) -> list[dict[str, Any]]:
        if not self.available:
            self._log("held", "search", "Pixabay API key is not configured; text-led card used.",
                      query=query)
            return []
        query = clean_text(query, 100)
        if len(query) < 3:
            self._log("held", "search", "Photo query is too short; no generic stock image used.",
                      query=query)
            return []
        for attempt, relaxed in enumerate(self.relax_query(query)):
            hits = self._search_once(relaxed, per_page=per_page)
            if hits:
                if attempt:
                    self._log("ok", "search_relaxed",
                              f"Pixabay query relaxed from {query!r} to {relaxed!r} for topical hits.",
                              query=query, relaxed=relaxed, hits=len(hits))
                return hits
        return []

    def _search_once(self, query: str, *, per_page: int | None = None) -> list[dict[str, Any]]:
        per_page = max(3, min(int(per_page or self.config.pixabay_per_page), 200))
        params: dict[str, Any] = {
            "key": self.config.pixabay_api_key,
            "q": query,
            "image_type": "photo",
            "orientation": "horizontal",
            "safesearch": "true",
            "min_width": self.config.pixabay_min_width,
            "min_height": self.config.pixabay_min_height,
            "per_page": per_page,
            "page": 1,
        }
        cache_path = self._cache_path(params)
        cached = self._read_cache(cache_path)
        if cached is not None:
            self._log("ok", "search_cache", "Cached Pixabay search results used (24h policy).",
                      query=query, hits=len(cached))
            return cached[: self.config.max_photos_per_card]
        self._rate_limit()
        try:
            response = self.session.get(self.config.pixabay_api_base, params=params,
                                        timeout=self.config.request_timeout)
        except requests.RequestException as exc:
            self._log("error", "search", f"Pixabay network error: {type(exc).__name__}", query=query)
            return []
        if response.status_code >= 400:
            body = redact(response.text[:500], self.config.secret_values)
            self._log("error", "search", f"Pixabay HTTP {response.status_code}: {body}", query=query)
            return []
        try:
            data = response.json()
        except ValueError:
            self._log("error", "search", "Pixabay returned non-JSON response.", query=query)
            return []
        hits = [self._sanitize_hit(h) for h in data.get("hits", []) if isinstance(h, dict)]
        hits = [
            h for h in hits
            if str(h.get("type", "")).lower() == "photo"
            and (h.get("largeImageURL") or h.get("webformatURL"))
            and int(h.get("webformatWidth") or h.get("imageWidth") or 0) >= self.config.pixabay_min_width
            and int(h.get("webformatHeight") or h.get("imageHeight") or 0) >= self.config.pixabay_min_height
        ][: self.config.max_photos_per_card]
        cache_path.write_text(
            json_dumps({"cached_at": time.time(), "query": query, "hits": hits}),
            encoding="utf-8",
        )
        self._log("ok", "search", f"Pixabay returned {len(hits)} safe photo candidates.",
                  query=query, hits=len(hits), cached_at=iso())
        return hits

    def download(self, hit: dict[str, Any], query: str) -> dict[str, Any] | None:
        url = str(hit.get("largeImageURL") or hit.get("webformatURL") or "")
        if not url:
            return None
        pixabay_id = str(hit.get("id") or sha256_text(url)[:16])
        path = self.image_dir / f"{pixabay_id}.jpg"
        if not path.exists() or path.stat().st_size < 10_000:
            self._rate_limit()
            try:
                response = self.session.get(url, timeout=self.config.request_timeout)
            except requests.RequestException as exc:
                self._log("error", "download", f"Pixabay image download error: {type(exc).__name__}",
                          query=query, pixabay_id=pixabay_id)
                return None
            if response.status_code >= 400 or "image" not in response.headers.get("content-type", "").lower():
                self._log("error", "download", "Pixabay image URL did not return an image.",
                          query=query, pixabay_id=pixabay_id, http_status=response.status_code)
                return None
            if len(response.content) > 10 * 1024 * 1024:
                self._log("held", "download", "Pixabay image exceeds 10 MB limit.",
                          query=query, pixabay_id=pixabay_id)
                return None
            path.write_bytes(response.content)
        try:
            with Image.open(path) as im:
                width, height = im.size
                if width < 600 or height < 400:
                    raise ValueError("image too small")
        except Exception as exc:
            path.unlink(missing_ok=True)
            self._log("held", "download", f"Pixabay image inspection failed: {type(exc).__name__}",
                      query=query, pixabay_id=pixabay_id)
            return None
        user = str(hit.get("user") or "Pixabay contributor").strip()
        page_url = str(hit.get("pageURL") or "https://pixabay.com/")
        return {
            "url": page_url,
            "credit": f"Pixabay / {user}",
            "license_ref": self.config.pixabay_license_ref,
            "permission_ref": page_url,
            "local_path": str(path),
            "sha256": sha256_file(path),
            "width": width,
            "height": height,
            "provider": "pixabay",
            "pixabay_id": pixabay_id,
            "source_page": page_url,
            "source_image_url": url,
            "query": query,
            "tags": str(hit.get("tags") or ""),
            "ai_generated": False,
        }
