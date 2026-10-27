"""Facebook publishing workflow with dry-run, idempotency and safe retries."""
from __future__ import annotations

import json
import time
from typing import Any

from ..config import CONFIG
from ..db import Database
from ..facebook import FacebookGraphClient, GraphAPIError
from ..util import iso, json_dumps, redact, sha256_text


class PublishingAgent:
    def __init__(self, db: Database | None = None, graph: FacebookGraphClient | None = None, config=CONFIG):
        self.db = db or Database(config.db_path)
        self.graph = graph or FacebookGraphClient(self.db, config)
        self.config = config

    @staticmethod
    def _json(value: str | None, default: Any) -> Any:
        if not value:
            return default
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return default

    def build_caption(self, editorial: dict[str, Any], marker: str, test: bool = False) -> str:
        urdu_bullets = self._json(editorial.get("urdu_bullets_json"), [])
        roman_bullets = self._json(editorial.get("roman_bullets_json"), [])
        source_urls = self._json(editorial.get("source_urls_json"), [])
        media = self._json(editorial.get("media_json"), None)
        lines: list[str] = []
        if test:
            lines.append("TEST POST — OPERATOR APPROVAL REQUIRED")
        lines.append(editorial["urdu_headline"])
        lines.extend(f"• {x}" for x in urdu_bullets[:5])
        lines.append("")
        lines.append("Roman Urdu:")
        lines.append(editorial["roman_headline"])
        lines.extend(f"- {x}" for x in roman_bullets[:5])
        if source_urls:
            lines.append("")
            lines.append("Sources:")
            lines.extend(f"- {u}" for u in source_urls[:5])
        if media and media.get("credit"):
            lines.append(f"Photo: {media['credit']} ({media.get('license_ref', 'licensed')})")
        lines.append(f"AZAD STUDIO ref: {marker}")
        return redact("\n".join(lines), self.config.secret_values)

    def publish(self, editorial_id: str, card_row: dict[str, Any], *, dry_run: bool | None = None,
                test: bool = False) -> dict[str, Any]:
        dry_run = self.config.dry_run if dry_run is None else dry_run
        editorial = self.db.get_editorial(editorial_id)
        if not editorial:
            return {"status": "held", "reason": "editorial_not_found"}
        if editorial.get("approved_by") == "dry_run_fixture" and not dry_run:
            self.db.insert_api_event("facebook", "publish", "held",
                                     "Dry-run test fixture is not eligible for live publication.",
                                     details={"editorial_id": editorial_id})
            return {"status": "held", "reason": "dry_run_fixture"}
        if editorial["status"] != "approved":
            self.db.insert_api_event("facebook", "publish", "held",
                                     "Editorial is not approved for public posting.",
                                     details={"editorial_id": editorial_id, "status": editorial["status"]})
            return {"status": "held", "reason": "editorial_not_approved"}
        if card_row.get("visual_status") != "passed":
            return {"status": "held", "reason": "card_visual_inspection_failed"}

        page_id = self.graph.confirmed_page_id() if not dry_run else None
        caption_seed = json_dumps({
            "editorial": editorial["content_hash"], "card": card_row["sha256"],
            "page": page_id or "dry_run", "test": test,
        })
        caption_hash = sha256_text(caption_seed)
        marker = caption_hash[:12]
        caption = self.build_caption(editorial, marker, test)
        idempotency_key = sha256_text(caption_seed + "|" + sha256_text(caption))[:48]

        if dry_run:
            existing = self.db.publish_attempt(idempotency_key)
            if not existing:
                pid = self.db.create_publish_attempt(
                    idempotency_key=idempotency_key, editorial_id=editorial_id,
                    card_id=card_row["id"], target_page_id=None, caption_hash=caption_hash,
                    status="dry_run",
                )
            else:
                pid = existing["id"]
            self.db.insert_api_event("facebook", "publish", "dry_run",
                                     "No Graph API call made; dry-run caption and card recorded.",
                                     details={"editorial_id": editorial_id, "card_id": card_row["id"],
                                              "idempotency_key": idempotency_key, "caption": caption})
            return {"status": "dry_run", "attempt_id": pid, "caption": caption, "post_id": None}

        if not self.config.publish_safety_ok:
            return self._hold(idempotency_key, editorial_id, card_row, caption_hash,
                              "Live publishing is not enabled or operator approval is missing.")
        if not page_id:
            return self._hold(idempotency_key, editorial_id, card_row, caption_hash,
                              "Facebook Page identity has not been confirmed by the Graph API.")
        if not test and self.db.get_setting("facebook_test_post_passed") != "true":
            return self._hold(idempotency_key, editorial_id, card_row, caption_hash,
                              "An operator-approved test publication has not succeeded.")

        existing = self.db.publish_attempt(idempotency_key)
        if existing and existing["status"] == "success":
            return {"status": "already_published", "post_id": existing.get("post_id"), "attempt_id": existing["id"]}
        if existing and existing["status"] in {"unknown", "running"}:
            return self._reconcile(existing, page_id, marker, caption_hash)
        if existing and existing["status"] == "failed" and int(existing["attempts"]) >= 3:
            return self._hold(idempotency_key, editorial_id, card_row, caption_hash,
                              "Retry limit reached; operator review is required.")
        if not existing:
            pid = self.db.create_publish_attempt(
                idempotency_key=idempotency_key, editorial_id=editorial_id,
                card_id=card_row["id"], target_page_id=page_id, caption_hash=caption_hash,
                status="planned",
            )
        else:
            pid = existing["id"]

        last_error: GraphAPIError | None = None
        for attempt_no in range(1, 4):
            self.db.update_publish_attempt(pid, status="running", attempts=attempt_no,
                                           request_started_at=iso(), last_error=None)
            if attempt_no > 1:
                # Reconcile first: a timed-out request may already have posted.
                current = self.db.publish_attempt(idempotency_key)
                reconciled = self._reconcile(current, page_id, marker, caption_hash)
                if reconciled["status"] == "success":
                    return reconciled
            try:
                response = self.graph.publish_photo(page_id, card_row["file_path"], caption)
                post_id = str(response.get("post_id") or response.get("id"))
                self.db.update_publish_attempt(
                    pid, status="success", response_at=iso(), post_id=post_id,
                    photo_id=str(response.get("id") or ""), last_error=None,
                )
                self.db.set_editorial_status(editorial_id, "published", "graph_api")
                cluster_id = editorial.get("cluster_id")
                if cluster_id:
                    self.db.set_cluster_status(cluster_id, "published", post_id)
                if test:
                    self.db.set_setting("facebook_test_post_passed", "true")
                    self.db.set_setting("facebook_test_post_id", post_id)
                self.db.insert_api_event("facebook", "publish_photo", "success",
                                         "Graph API confirmed photo publication.",
                                         http_status=200,
                                         details={"page_id": page_id, "post_id": post_id,
                                                  "attempt": attempt_no, "test": test})
                return {"status": "success", "post_id": post_id, "attempt_id": pid, "attempts": attempt_no}
            except GraphAPIError as exc:
                last_error = exc
                self.db.insert_api_event("facebook", "publish_photo", "error", str(exc),
                                         http_status=exc.status, error_code=exc.code,
                                         details={"page_id": page_id, "attempt": attempt_no})
                if not exc.transient:
                    self.db.update_publish_attempt(pid, status="failed", response_at=iso(),
                                                   last_error=str(exc)[:1000])
                    return {"status": "failed", "reason": str(exc), "attempt_id": pid}
                if attempt_no < 3:
                    self.db.update_publish_attempt(pid, status="unknown", response_at=iso(),
                                                   last_error=str(exc)[:1000])
                    time.sleep(2 ** (attempt_no - 1))
                    continue
                self.db.update_publish_attempt(pid, status="unknown", response_at=iso(),
                                               last_error=str(exc)[:1000])
                return {"status": "unknown", "reason": "Network/API result ambiguous; manual reconciliation required.",
                        "attempt_id": pid}
        return {"status": "unknown", "reason": str(last_error) if last_error else "retry loop ended", "attempt_id": pid}

    def _hold(self, idempotency_key: str, editorial_id: str, card_row: dict[str, Any],
              caption_hash: str, reason: str) -> dict[str, Any]:
        existing = self.db.publish_attempt(idempotency_key)
        if not existing:
            self.db.create_publish_attempt(
                idempotency_key=idempotency_key, editorial_id=editorial_id,
                card_id=card_row["id"], target_page_id=None, caption_hash=caption_hash, status="held",
            )
        self.db.add_review(None, None, "publishing_blocked", {"reason": reason, "editorial_id": editorial_id})
        self.db.insert_api_event("facebook", "publish", "held", reason,
                                 details={"editorial_id": editorial_id, "card_id": card_row["id"]})
        return {"status": "held", "reason": reason}

    def _reconcile(self, attempt: dict[str, Any], page_id: str, marker: str,
                   caption_hash: str) -> dict[str, Any]:
        try:
            post = self.graph.find_recent_caption(page_id, marker)
        except GraphAPIError as exc:
            self.db.insert_api_event("facebook", "reconcile", "error", str(exc),
                                     http_status=exc.status, error_code=exc.code)
            return {"status": "unknown", "reason": "Could not query Page feed for duplicate reconciliation.",
                    "attempt_id": attempt["id"]}
        if post and post.get("id"):
            post_id = str(post["id"])
            self.db.update_publish_attempt(attempt["id"], status="success", response_at=iso(),
                                           post_id=post_id, last_error=None)
            self.db.insert_api_event("facebook", "reconcile", "success",
                                     "Existing post found; duplicate publication prevented.",
                                     details={"post_id": post_id, "marker": marker})
            return {"status": "success", "post_id": post_id, "attempt_id": attempt["id"], "reconciled": True}
        self.db.update_publish_attempt(attempt["id"], status="unknown", last_error="No matching post found in recent feed.")
        return {"status": "unknown", "reason": "No matching post found; manual reconciliation required.",
                "attempt_id": attempt["id"]}
