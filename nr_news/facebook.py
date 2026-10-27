"""Official Meta Graph API v26.0 client and Page identity verification.

The numeric value in the configured Facebook profile URL is used ONLY as a
lookup hint against /me/accounts. Publishing always uses the Page ID confirmed
by the API and stored in the durable settings table.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import requests

from .config import CONFIG
from .db import Database
from .util import iso, redact


class GraphAPIError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, code: str | None = None,
                 transient: bool = False):
        super().__init__(message)
        self.status = status
        self.code = code
        self.transient = transient


class FacebookGraphClient:
    REQUIRED_PERMISSIONS = (
        "pages_manage_posts", "pages_read_engagement", "pages_show_list",
        "pages_manage_engagement", "pages_read_user_engagement",
    )
    REQUIRED_TASKS = {"CREATE_CONTENT", "MANAGE", "MODERATE"}

    def __init__(self, db: Database | None = None, config=CONFIG):
        self.db = db or Database(config.db_path)
        self.config = config

    @property
    def base(self) -> str:
        return f"{self.config.graph_api_base}/{self.config.graph_api_version}"

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _call(self, method: str, path: str, *, token: str, params: dict[str, Any] | None = None,
              data: dict[str, Any] | None = None, files: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base}/{path.lstrip('/')}"
        if params:
            url += "?" + urlencode(params)
        try:
            r = requests.request(method, url, headers=self._headers(token), data=data, files=files, timeout=90)
        except requests.RequestException as exc:
            raise GraphAPIError(redact(f"Network error: {type(exc).__name__}", self.config.secret_values),
                                transient=True) from exc
        try:
            payload = r.json()
        except ValueError:
            payload = {"raw": redact(r.text[:500], self.config.secret_values)}
        if r.status_code >= 400:
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            message = redact(str(error.get("message") or payload), self.config.secret_values)
            code = str(error.get("code") or error.get("type") or r.status_code) if isinstance(error, dict) else str(r.status_code)
            transient = r.status_code >= 500 or r.status_code == 429
            raise GraphAPIError(message[:1000], status=r.status_code, code=code, transient=transient)
        return payload if isinstance(payload, dict) else {"data": payload}

    def verify_identity(self) -> dict[str, Any]:
        """Resolve the configured URL to a managed Page and verify permissions."""
        if not self.config.expected_page_name:
            return {"ok": False, "reason": "NR_EXPECTED_PAGE_NAME is not set; ask the operator for the exact Page name."}
        if not self.config.facebook_user_token:
            return {"ok": False, "reason": "Authorized user access token is not available."}
        if not self.config.facebook_page_token:
            return {"ok": False, "reason": "Page access token is not available."}
        try:
            accounts = self._call("GET", "/me/accounts", token=self.config.facebook_user_token,
                                  params={"fields": "id,name,link,tasks", "limit": 100})
            permissions = self._call("GET", "/me/permissions", token=self.config.facebook_user_token)
        except GraphAPIError as exc:
            self.db.insert_api_event("facebook", "verify_identity", "error", str(exc),
                                     http_status=exc.status, error_code=exc.code)
            return {"ok": False, "reason": str(exc)}

        pages = accounts.get("data", [])
        hint = self.config.facebook_page_url_hint_id
        matches = [p for p in pages if str(p.get("id")) == hint or hint in str(p.get("link", ""))]
        if len(matches) != 1:
            reason = f"The configured profile URL resolved to {len(matches)} managed Pages; identity not confirmed."
            self.db.insert_api_event("facebook", "verify_identity", "held", reason,
                                     details={"configured_url": self.config.facebook_page_url})
            return {"ok": False, "reason": reason}
        page = matches[0]
        name = str(page.get("name", ""))
        if name.strip().casefold() != self.config.expected_page_name.strip().casefold():
            reason = "API-confirmed Page name does not match NR_EXPECTED_PAGE_NAME; publishing remains paused."
            self.db.insert_api_event("facebook", "verify_identity", "held", reason,
                                     details={"configured_url": self.config.facebook_page_url})
            return {"ok": False, "reason": reason}
        tasks = {str(x).upper() for x in page.get("tasks", [])}
        missing_tasks = sorted(self.REQUIRED_TASKS - tasks)
        if missing_tasks:
            reason = "Page role does not include required tasks: " + ", ".join(missing_tasks)
            self.db.insert_api_event("facebook", "verify_identity", "held", reason)
            return {"ok": False, "reason": reason}

        granted = {
            str(p.get("permission")): str(p.get("status"))
            for p in permissions.get("data", []) if isinstance(p, dict)
        }
        missing_permissions = [p for p in self.REQUIRED_PERMISSIONS if granted.get(p) != "granted"]
        if missing_permissions:
            reason = "Required permissions not granted: " + ", ".join(missing_permissions)
            self.db.insert_api_event("facebook", "verify_identity", "held", reason)
            return {"ok": False, "reason": reason}

        page_id = str(page["id"])
        try:
            confirmed = self._call("GET", f"/{page_id}", token=self.config.facebook_page_token,
                                   params={"fields": "id,name,link,category"})
        except GraphAPIError as exc:
            self.db.insert_api_event("facebook", "verify_page_token", "error", str(exc),
                                     http_status=exc.status, error_code=exc.code)
            return {"ok": False, "reason": str(exc)}
        if str(confirmed.get("id")) != page_id or str(confirmed.get("name", "")).strip().casefold() != name.strip().casefold():
            reason = "Page access token does not match the API-confirmed Page identity."
            self.db.insert_api_event("facebook", "verify_identity", "held", reason)
            return {"ok": False, "reason": reason}

        self.db.set_setting("facebook_page_id", page_id)
        self.db.set_setting("facebook_page_name", name)
        self.db.set_setting("facebook_page_url", self.config.facebook_page_url)
        self.db.set_setting("facebook_identity_verified_at", iso())
        self.db.insert_api_event("facebook", "verify_identity", "ok",
                                 "Page identity, role and permissions confirmed by Graph API",
                                 details={"page_id": page_id, "page_name": name,
                                          "permissions": sorted(granted), "tasks": sorted(tasks)})
        return {"ok": True, "page_id": page_id, "page_name": name, "permissions": sorted(granted), "tasks": sorted(tasks)}

    def confirmed_page_id(self) -> str | None:
        if self.db.get_setting("facebook_identity_verified_at"):
            return self.db.get_setting("facebook_page_id")
        return None

    def publish_photo(self, page_id: str, image_path: str, caption: str) -> dict[str, Any]:
        path = Path(image_path)
        if not path.exists():
            raise GraphAPIError("Card image is unavailable", status=None, code="LOCAL_FILE")
        if path.stat().st_size > 10 * 1024 * 1024:
            raise GraphAPIError("Card image exceeds the 10 MB photo upload limit")
        with path.open("rb") as fh:
            payload = self._call(
                "POST", f"/{page_id}/photos", token=self.config.facebook_page_token or "",
                data={"caption": caption, "published": "true"},
                files={"source": (path.name, fh, "image/png")},
            )
        if not payload.get("id") and not payload.get("post_id"):
            raise GraphAPIError("Graph API response did not confirm a photo/post ID", code="NO_POST_ID")
        return payload

    def find_recent_caption(self, page_id: str, caption_hash_marker: str) -> dict[str, Any] | None:
        payload = self._call(
            "GET", f"/{page_id}/feed", token=self.config.facebook_page_token or "",
            params={"fields": "id,message,created_time", "limit": 30},
        )
        for post in payload.get("data", []):
            if caption_hash_marker in str(post.get("message", "")):
                return post
        return None
