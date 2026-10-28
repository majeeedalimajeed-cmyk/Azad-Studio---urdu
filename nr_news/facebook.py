"""Official Meta Graph API v26.0 client and Page identity verification.

The configured Facebook profile URL, an explicit NR_FB_PAGE_ID, or the exact
expected Page name are used ONLY as lookup hints against /me/accounts. When the
user token is unavailable or expired, the long-lived Page token can confirm the
same identity directly. Publishing always uses the Page ID confirmed by the API
and stored in the durable settings table.
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
        if not self.config.facebook_page_token:
            return {"ok": False, "reason": "Page access token is not available."}

        accounts: dict[str, Any] | None = None
        permissions: dict[str, Any] | None = None
        user_error: GraphAPIError | None = None
        if self.config.facebook_user_token:
            try:
                accounts = self._call("GET", "/me/accounts", token=self.config.facebook_user_token,
                                      params={"fields": "id,name,link,tasks", "limit": 100})
                permissions = self._call("GET", "/me/permissions", token=self.config.facebook_user_token)
            except GraphAPIError as exc:
                user_error = exc
                self.db.insert_api_event("facebook", "verify_identity_user_token", "error", str(exc),
                                         http_status=exc.status, error_code=exc.code)
        else:
            user_error = GraphAPIError("Authorized user access token is not available.", code="NO_USER_TOKEN")
        if user_error or accounts is None or permissions is None:
            # A long-lived Page token can keep an already API-confirmed Page
            # publishing even when the short-lived user token has expired.
            return self._verify_with_page_token(user_error)

        pages = accounts.get("data", [])
        hint = self.config.facebook_page_id or self.config.facebook_page_url_hint_id
        expected_name = (self.config.expected_page_name or "").strip().casefold()
        matches_by_id: dict[str, dict[str, Any]] = {}
        for candidate in pages:
            cid = str(candidate.get("id", ""))
            api_name = str(candidate.get("name", "")).strip().casefold()
            if (cid == hint
                    or hint in str(candidate.get("link", ""))
                    or (expected_name and api_name == expected_name)):
                matches_by_id[cid] = candidate
        matches = list(matches_by_id.values())
        if len(matches) != 1:
            reason = f"The configured Page ID/name resolved to {len(matches)} managed Pages; identity not confirmed."
            self.db.insert_api_event("facebook", "verify_identity", "held", reason,
                                     details={"configured_url": self.config.facebook_page_url,
                                              "configured_page_id": self.config.facebook_page_id,
                                              "expected_page_name": self.config.expected_page_name})
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
        self.db.set_setting("facebook_page_url", str(confirmed.get("link") or self.config.facebook_page_url))
        self.db.set_setting("facebook_configured_profile_url", self.config.facebook_page_url)
        self.db.set_setting("facebook_identity_verified_at", iso())
        self.db.insert_api_event("facebook", "verify_identity", "ok",
                                 "Page identity, role and permissions confirmed by Graph API",
                                 details={"page_id": page_id, "page_name": name,
                                          "permissions": sorted(granted), "tasks": sorted(tasks)})
        return {"ok": True, "page_id": page_id, "page_name": name, "permissions": sorted(granted), "tasks": sorted(tasks)}

    def _verify_with_page_token(self, user_error: GraphAPIError | None) -> dict[str, Any]:
        """Fallback for an expired/missing user token using a long-lived Page token.

        The Page token is checked directly against the configured Page ID and
        exact expected name. It must also expose the required Page tasks or the
        required permissions; otherwise identity remains unconfirmed.
        """
        page_id = self.config.facebook_page_id or self.db.get_setting("facebook_page_id")
        stored_id = self.db.get_setting("facebook_page_id")
        if not page_id:
            return {"ok": False, "reason": "User token unavailable and no configured/API-confirmed Page ID is available for Page-token verification."}
        if stored_id and str(stored_id) != str(page_id):
            return {"ok": False, "reason": "Configured Page ID does not match the previously API-confirmed Page ID."}

        expected_name = (self.config.expected_page_name or "").strip().casefold()
        try:
            confirmed = self._call(
                "GET", f"/{page_id}", token=self.config.facebook_page_token or "",
                params={"fields": "id,name,link,category,tasks"},
            )
        except GraphAPIError as exc:
            self.db.insert_api_event("facebook", "verify_page_token_fallback", "error", str(exc),
                                     http_status=exc.status, error_code=exc.code)
            return {"ok": False, "reason": str(exc)}
        name = str(confirmed.get("name", ""))
        if str(confirmed.get("id", "")) != str(page_id) or name.strip().casefold() != expected_name:
            reason = "Page token does not match the configured Page ID and exact expected name."
            self.db.insert_api_event("facebook", "verify_page_token_fallback", "held", reason,
                                     details={"page_id": page_id, "expected_name": self.config.expected_page_name})
            return {"ok": False, "reason": reason}

        tasks = {str(x).upper() for x in confirmed.get("tasks", []) or []}
        granted: dict[str, str] = {}
        try:
            perm_payload = self._call("GET", "/me/permissions", token=self.config.facebook_page_token or "")
            granted = {
                str(p.get("permission")): str(p.get("status"))
                for p in perm_payload.get("data", []) if isinstance(p, dict)
            }
        except GraphAPIError:
            # Page tokens do not always expose /me/permissions; tasks from the
            # direct Page read can confirm the role instead.
            granted = {}

        if tasks:
            missing_tasks = sorted(self.REQUIRED_TASKS - tasks)
            if missing_tasks:
                reason = "Page token reports missing required tasks: " + ", ".join(missing_tasks)
                self.db.insert_api_event("facebook", "verify_page_token_fallback", "held", reason)
                return {"ok": False, "reason": reason}
        if granted:
            missing_permissions = [p for p in self.REQUIRED_PERMISSIONS if granted.get(p) != "granted"]
            if missing_permissions:
                reason = "Page token reports missing required permissions: " + ", ".join(missing_permissions)
                self.db.insert_api_event("facebook", "verify_page_token_fallback", "held", reason)
                return {"ok": False, "reason": reason}
        if not tasks and not granted:
            reason = "Page token did not return the required tasks or permissions."
            self.db.insert_api_event("facebook", "verify_page_token_fallback", "held", reason)
            return {"ok": False, "reason": reason}

        self.db.set_setting("facebook_page_id", str(page_id))
        self.db.set_setting("facebook_page_name", name)
        self.db.set_setting("facebook_page_url", str(confirmed.get("link") or self.config.facebook_page_url))
        self.db.set_setting("facebook_configured_profile_url", self.config.facebook_page_url)
        self.db.set_setting("facebook_identity_verified_at", iso())
        self.db.set_setting("facebook_identity_method", "page_token_fallback")
        self.db.insert_api_event(
            "facebook", "verify_page_token_fallback", "ok",
            "Page identity re-verified directly with the long-lived Page token.",
            details={"page_id": page_id, "page_name": name, "tasks": sorted(tasks),
                     "permissions": sorted(granted),
                     "user_token_error": redact(str(user_error) if user_error else "missing", self.config.secret_values)},
        )
        return {"ok": True, "page_id": str(page_id), "page_name": name,
                "permissions": sorted(granted), "tasks": sorted(tasks),
                "identity_method": "page_token_fallback"}

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
        # Feed reconciliation normally needs pages_read_engagement on the user
        # token. If that token expired, try the Page token; some Page tokens can
        # read the feed, others cannot. Never let a feed-read failure masquerade
        # as a confirmed duplicate.
        tokens = [t for t in (self.config.facebook_user_token,
                              self.config.facebook_page_token) if t]
        last_error: GraphAPIError | None = None
        for token in tokens:
            try:
                payload = self._call(
                    "GET", f"/{page_id}/feed", token=token,
                    params={"fields": "id,message,created_time", "limit": 30},
                )
                for post in payload.get("data", []):
                    if caption_hash_marker in str(post.get("message", "")):
                        return post
                return None
            except GraphAPIError as exc:
                last_error = exc
                continue
        if last_error:
            self.db.insert_api_event(
                "facebook", "feed_reconciliation", "error",
                redact(str(last_error), self.config.secret_values),
                http_status=last_error.status, error_code=last_error.code,
                details={"page_id": page_id},
            )
        return None

    # --- Instagram cross-posting (linked Instagram professional account) ---
    def instagram_account_id(self, page_id: str) -> str | None:
        payload = self._call(
            "GET", f"/{page_id}", token=self.config.facebook_page_token or "",
            params={"fields": "instagram_business_account{id,username}"},
        )
        account = payload.get("instagram_business_account") or {}
        return str(account["id"]) if account.get("id") else None

    def post_image_url(self, post_id: str) -> str | None:
        payload = self._call(
            "GET", f"/{post_id}/attachments",
            token=self.config.facebook_page_token or "",
        )
        for item in payload.get("data", []):
            src = ((item.get("media") or {}).get("image") or {}).get("src")
            if src:
                return str(src)
        return None

    def ig_create_container(self, ig_user_id: str, image_url: str, caption: str) -> str:
        payload = self._call(
            "POST", f"/{ig_user_id}/media",
            token=self.config.facebook_page_token or "",
            data={"image_url": image_url, "caption": caption},
        )
        if not payload.get("id"):
            raise GraphAPIError("Instagram container response had no ID", code="NO_CONTAINER_ID")
        return str(payload["id"])

    def ig_container_status(self, creation_id: str) -> str:
        payload = self._call(
            "GET", f"/{creation_id}", token=self.config.facebook_page_token or "",
            params={"fields": "status_code"},
        )
        return str(payload.get("status_code") or "")

    def ig_publish_container(self, ig_user_id: str, creation_id: str) -> str:
        payload = self._call(
            "POST", f"/{ig_user_id}/media_publish",
            token=self.config.facebook_page_token or "",
            data={"creation_id": creation_id},
        )
        if not payload.get("id"):
            raise GraphAPIError("Instagram publish response had no media ID", code="NO_MEDIA_ID")
        return str(payload["id"])

    def ig_permalink(self, media_id: str) -> str | None:
        payload = self._call(
            "GET", f"/{media_id}", token=self.config.facebook_page_token or "",
            params={"fields": "permalink"},
        )
        return str(payload["permalink"]) if payload.get("permalink") else None

    def get_post(self, post_id: str) -> dict[str, Any]:
        """Independently confirm a published post through the Graph API."""
        tokens = [t for t in (self.config.facebook_page_token,
                              self.config.facebook_user_token) if t]
        last_error: GraphAPIError | None = None
        for token in tokens:
            try:
                return self._call(
                    "GET", f"/{post_id}", token=token,
                    params={"fields": "id,message,created_time,permalink_url"},
                )
            except GraphAPIError as exc:
                last_error = exc
                if exc.transient:
                    raise
        detail = redact(str(last_error) if last_error else "no token", self.config.secret_values)
        raise GraphAPIError(f"Could not confirm post {post_id}: {detail}",
                            status=getattr(last_error, "status", None),
                            code="POST_CONFIRMATION_FAILED")
