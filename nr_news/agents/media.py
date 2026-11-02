"""Agent E — rights-aware news photographs, up to two per card.

Images require individually recorded relevance and rights review, explicit
credit, licence and a local file. Stock search results are not auto-selected.
If no reviewed relevant photograph is available, the renderer uses a clean
text-led card rather than inventing or hot-linking an image.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from ..config import CONFIG
from ..db import Database
from ..pixabay import PixabayClient
from ..util import iso, sha256_file


class MediaAgent:
    def __init__(self, db: Database | None = None, client: Any | None = None, config=CONFIG,
                 pixabay_client: PixabayClient | None = None):
        self.db = db or Database(config.db_path)
        self.client = client
        self.config = config
        self.pixabay = pixabay_client or PixabayClient(self.db, config)

    @staticmethod
    def _as_list(media: Any) -> list[dict[str, Any]]:
        if not media:
            return []
        if isinstance(media, dict):
            return [media]
        if isinstance(media, (list, tuple)):
            return [m for m in media if isinstance(m, dict)]
        return []

    def _approve_one(self, candidate: dict[str, Any], media: dict[str, Any]) -> dict[str, Any] | None:
        # Licences and search terms alone do not establish editorial relevance.
        # Accept only assets individually reviewed for this specific story.
        required = ("url", "credit", "license_ref", "local_path",
                    "relevance_reviewed_by", "relevance_note", "rights_reviewed_by")
        missing = [k for k in required if not media.get(k)]
        if missing:
            self.db.insert_api_event("agent_e", "media_select", "held",
                                     "Media rights metadata incomplete; image skipped.",
                                     details={"missing": missing, "candidate_id": candidate.get("id")})
            return None
        if media.get("ai_generated") is True:
            self.db.insert_api_event("agent_e", "media_select", "rejected",
                                     "AI-generated imagery is not a news photograph.",
                                     details={"candidate_id": candidate.get("id")})
            return None
        path = Path(media["local_path"])
        if not path.exists():
            self.db.insert_api_event("agent_e", "media_select", "held",
                                     "Licensed local image file is unavailable.",
                                     details={"candidate_id": candidate.get("id")})
            return None
        try:
            with Image.open(path) as im:
                width, height = im.size
                if width < 600 or height < 400:
                    raise ValueError("image too small")
        except Exception as exc:
            self.db.insert_api_event("agent_e", "media_select", "held",
                                     f"Image inspection failed: {type(exc).__name__}",
                                     details={"candidate_id": candidate.get("id")})
            return None
        approved = {
            "url": media["url"],
            "credit": media["credit"],
            "license_ref": media["license_ref"],
            "permission_ref": media.get("permission_ref"),
            "local_path": str(path),
            "sha256": media.get("sha256") or sha256_file(path),
            "width": width,
            "height": height,
            "provider": media.get("provider") or "source",
            "source_page": media.get("source_page") or media["url"],
            "source_image_url": media.get("source_image_url") or media["url"],
            "query": media.get("query"),
            "tags": media.get("tags"),
            "pixabay_id": media.get("pixabay_id"),
            "relevance_reviewed_by": media["relevance_reviewed_by"],
            "relevance_note": media["relevance_note"],
            "rights_reviewed_by": media["rights_reviewed_by"],
        }
        self.db.execute(
            "INSERT INTO media_assets(id,url,local_path,credit,license_ref,permission_ref,sha256,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (uuid4().hex, approved["url"], approved["local_path"], approved["credit"],
             approved["license_ref"], approved.get("permission_ref"), approved["sha256"], "approved", iso()),
        )
        self.db.insert_api_event("agent_e", "media_select", "approved",
                                 "Rights-cleared editorial photograph approved.",
                                 details={"candidate_id": candidate.get("id"),
                                          "license_ref": approved["license_ref"],
                                          "provider": approved["provider"]})
        return approved

    def select(self, candidate: dict[str, Any], media: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Backwards-compatible single-image selector."""
        approved = self.select_many(candidate, media, max_photos=1)
        return approved[0] if approved else None

    def select_many(self, candidate: dict[str, Any], media: Any = None, *,
                    query_context: dict[str, Any] | None = None,
                    max_photos: int | None = None) -> list[dict[str, Any]]:
        """Return up to `max_photos` approved media dicts (default two)."""
        limit = max(1, int(max_photos or self.config.max_photos_per_card))
        supplied = self._as_list(media)
        approved: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(item: dict[str, Any] | None) -> None:
            if not item or len(approved) >= limit:
                return
            key = str(item.get("sha256") or item.get("url") or item.get("local_path") or "")
            if key and key in seen:
                return
            if key:
                seen.add(key)
            approved.append(item)

        for item in supplied:
            add(self._approve_one(candidate, item))
            if len(approved) >= limit:
                break

        # Do not auto-fill missing slots from stock search: matching search
        # terms does not prove that a photo represents this particular story.
        # Reviewed, licensed stock images may still be supplied explicitly.
        if not approved:
            reason = "No individually reviewed, rights-cleared relevant photo supplied; text-led card used."
            self.db.insert_api_event("agent_e", "media_select_many", "skipped", reason,
                                     details={"candidate_id": candidate.get("id"),
                                              "supplied": len(supplied), "approved": 0})
            return []
        self.db.insert_api_event("agent_e", "media_select_many", "approved",
                                 f"{len(approved)} rights-cleared photograph(s) approved for card.",
                                 details={"candidate_id": candidate.get("id"),
                                          "supplied": len(supplied), "approved": len(approved),
                                          "providers": [m.get("provider") for m in approved]})
        return approved
