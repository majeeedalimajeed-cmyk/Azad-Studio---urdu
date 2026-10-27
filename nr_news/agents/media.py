"""Agent E — rights-aware news photographs. Default is a text-led card."""
from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image

from ..config import CONFIG
from ..db import Database
from ..http_client import PoliteHTTPClient
from ..util import iso, sha256_file


class MediaAgent:
    def __init__(self, db: Database | None = None, client: PoliteHTTPClient | None = None, config=CONFIG):
        self.db = db or Database(config.db_path)
        self.client = client or PoliteHTTPClient(config)
        self.config = config

    def select(self, candidate: dict[str, Any], media: dict[str, Any] | None = None) -> dict[str, Any] | None:
        """Return approved media metadata or None.

        A source image URL alone is never sufficient. The operator must provide a
        license reference, credit and (where needed) a permission reference.
        AI-generated images are never accepted as news photographs.
        """
        if not media:
            self.db.insert_api_event("agent_e", "media_select", "skipped",
                                     "No rights-cleared photograph supplied; text-led card used.",
                                     details={"candidate_id": candidate.get("id")})
            return None
        required = ("url", "credit", "license_ref", "local_path")
        missing = [k for k in required if not media.get(k)]
        if missing:
            self.db.insert_api_event("agent_e", "media_select", "held",
                                     "Media rights metadata incomplete; text-led card used.",
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
            "sha256": sha256_file(path),
            "width": width,
            "height": height,
        }
        self.db.execute(
            "INSERT INTO media_assets(id,url,local_path,credit,license_ref,permission_ref,sha256,status,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (uuid4().hex, approved["url"], approved["local_path"], approved["credit"],
             approved["license_ref"], approved.get("permission_ref"), approved["sha256"], "approved", iso()),
        )
        self.db.insert_api_event("agent_e", "media_select", "approved",
                                 "Rights-cleared editorial photograph approved.",
                                 details={"candidate_id": candidate.get("id"), "license_ref": approved["license_ref"]})
        return approved
