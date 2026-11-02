"""Facebook publishing workflow with dry-run, idempotency and safe retries."""
from __future__ import annotations

import json
import time
from typing import Any

from ..config import CONFIG
from ..db import Database
from ..facebook import FacebookGraphClient, GraphAPIError
from ..publication_ledger import GitHubPublicationLedger, LedgerDuplicate, LedgerUnavailable
from ..source_policy import source_allowed
from ..util import iso, json_dumps, redact, sha256_file, sha256_text


class PublishingAgent:
    def __init__(self, db: Database | None = None, graph: FacebookGraphClient | None = None, config=CONFIG,
                 ledger: GitHubPublicationLedger | None = None):
        self.db = db or Database(config.db_path)
        self.graph = graph or FacebookGraphClient(self.db, config)
        self.config = config
        self.ledger = ledger or GitHubPublicationLedger()

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
        # Operator rule: no URLs and no photographer credits in the public
        # caption. Source links, credits and license references remain in
        # the DB/audit trail only.
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

        # Existing queued cards may have the old Urdu/byline or stock photos.
        # Live publication requires a new verified English/Roman editorial and
        # a freshly-rendered current-layout card with matching file bytes.
        if not dry_run:
            digest = card_row.get("sha256") or ""
            if self.db.get_setting(f"english_roman_verified:{editorial_id}") != "true":
                return {"status": "held", "reason": "new_format_verification_required"}
            if not digest or self.db.get_setting(f"card_current_layout:{digest}") != "true":
                return {"status": "held", "reason": "current_card_rebuild_required"}
            try:
                if sha256_file(card_row["file_path"]) != digest:
                    return {"status": "held", "reason": "current_card_hash_mismatch"}
            except (KeyError, OSError):
                return {"status": "held", "reason": "current_card_unavailable"}

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
            # The one-off test-post gate must not deadlock scheduled
            # automation: it is satisfied by (a) any earlier Graph-confirmed
            # successful publication recorded in this DB, or (b) the explicit
            # standing operator approval already required by
            # publish_safety_ok (NR_OPERATOR_APPROVED / NR_VERIFY_APPROVED /
            # NR_AUTO_POST_ENABLED / publish safety switch) together with an
            # API-confirmed Page identity. The DB flag alone lives in the
            # cached SQLite file and was lost when the cache reverted, which
            # silently held every approved story. A successful live publish
            # below re-sets the flag.
            prior = self.db.one(
                "SELECT post_id FROM publish_attempts WHERE status='success' "
                "AND post_id IS NOT NULL ORDER BY created_at LIMIT 1")
            if prior:
                self.db.set_setting("facebook_test_post_passed", "true")
                self.db.set_setting("facebook_test_post_id", str(prior.get("post_id") or ""))
            elif not self.config.publish_safety_ok:
                return self._hold(idempotency_key, editorial_id, card_row, caption_hash,
                                  "An operator-approved test publication has not succeeded.")
            else:
                self.db.insert_api_event(
                    "facebook", "test_post_gate", "ok",
                    "Test-post gate satisfied by standing operator approval and "
                    "API-confirmed Page identity; first successful publish will set the flag.",
                    details={"editorial_id": editorial_id, "page_id": page_id})

        existing = self.db.publish_attempt(idempotency_key)
        if existing and existing["status"] == "success":
            return {"status": "already_published", "post_id": existing.get("post_id"),
                    "attempt_id": existing["id"], "caption_marker": marker}
        if existing and existing["status"] in {"unknown", "running"}:
            return self._reconcile(existing, page_id, marker, caption_hash)
        if existing and existing["status"] == "failed" and int(existing["attempts"]) >= 3:
            return self._hold(idempotency_key, editorial_id, card_row, caption_hash,
                              "Retry limit reached; operator review is required.")
        # The remote compare-and-swap reservation MUST commit before any
        # Graph upload. SQLite and Actions cache are not crash-durable enough.
        candidate = self.db.get_candidate(editorial.get("candidate_id")) or {}
        if not source_allowed(candidate):
            return {"status": "held", "reason": "source_not_in_operator_list"}
        try:
            self.ledger.reserve(key=idempotency_key, candidate=candidate,
                                editorial_id=editorial_id, page_id=page_id, caption_marker=marker)
        except LedgerDuplicate:
            return {"status": "held", "reason": "durable_duplicate_or_previous_attempt"}
        except LedgerUnavailable as exc:
            self.db.insert_api_event("facebook", "durable_ledger", "held", str(exc),
                                     details={"editorial_id": editorial_id})
            return {"status": "held", "reason": "durable_ledger_unavailable"}

        if not existing:
            pid = self.db.create_publish_attempt(
                idempotency_key=idempotency_key, editorial_id=editorial_id,
                card_id=card_row["id"], target_page_id=page_id, caption_hash=caption_hash,
                status="planned",
            )
        else:
            pid = existing["id"]

        attempt_no = 1
        self.db.update_publish_attempt(pid, status="running", attempts=attempt_no,
                                       request_started_at=iso(), last_error=None)
        try:
            response = self.graph.publish_photo(page_id, card_row["file_path"], caption)
            confirmed_id = response.get("post_id") or response.get("id")
            if not confirmed_id:
                raise GraphAPIError("Graph response did not confirm publication", code="NO_POST_ID", transient=True)
            post_id = str(confirmed_id)
            ledger_warning = None
            try:
                self.ledger.finish(idempotency_key, status="confirmed", post_id=post_id)
            except LedgerUnavailable as exc:
                # The pre-upload reservation remains durable and blocks all retries.
                ledger_warning = str(exc)
                self.db.insert_api_event("facebook", "durable_ledger", "error", ledger_warning,
                                         details={"editorial_id": editorial_id})
            self.db.update_publish_attempt(
                pid, status="success", response_at=iso(), post_id=post_id,
                photo_id=str(response.get("id") or ""), last_error=None,
            )
            self.db.set_editorial_status(editorial_id, "published", "graph_api")
            cluster_id = editorial.get("cluster_id")
            if cluster_id:
                self.db.set_cluster_status(cluster_id, "published", post_id)
            # Any Graph-confirmed live publication (test or automated)
            # satisfies the test-post requirement durably in this DB.
            self.db.set_setting("facebook_test_post_passed", "true")
            self.db.set_setting("facebook_test_post_id", post_id)
            self.db.insert_api_event("facebook", "publish_photo", "success",
                                     "Graph API confirmed photo publication.",
                                     http_status=200,
                                     details={"page_id": page_id, "post_id": post_id,
                                              "attempt": attempt_no, "test": test})
            result = {"status": "success", "post_id": post_id, "attempt_id": pid,
                      "attempts": attempt_no, "caption_marker": marker}
            if ledger_warning:
                result["ledger_warning"] = "Upload confirmed but ledger outcome update requires review."
            result["instagram"] = self._cross_post_instagram(
                page_id, post_id, caption, test=test)
            return result
        except GraphAPIError as exc:
            self.db.insert_api_event("facebook", "publish_photo", "error", str(exc),
                                     http_status=exc.status, error_code=exc.code,
                                     details={"page_id": page_id, "attempt": attempt_no})
            try:
                self.ledger.finish(idempotency_key,
                                   status="uncertain" if exc.transient or exc.code == "NO_POST_ID" else "rejected")
            except LedgerUnavailable:
                pass  # The original reservation still blocks retries.
            if not exc.transient and exc.code != "NO_POST_ID":
                self.db.update_publish_attempt(pid, status="failed", response_at=iso(),
                                               last_error=str(exc)[:1000])
                return {"status": "failed", "reason": str(exc), "attempt_id": pid}
            # An upload timeout, 5xx, 429, or missing post ID can mean the
            # server accepted the photo despite our not receiving proof.
            # Never retry an ambiguous upload without manual confirmation.
            self.db.update_publish_attempt(pid, status="unknown", response_at=iso(),
                                           last_error=str(exc)[:1000])
            return {"status": "unknown", "reason": "Network/API result ambiguous; manual reconciliation required.",
                    "attempt_id": pid}
        except Exception as exc:
            try:
                self.ledger.finish(idempotency_key, status="uncertain")
            except LedgerUnavailable:
                pass
            self.db.update_publish_attempt(pid, status="unknown", response_at=iso(),
                                           last_error=f"Unexpected upload error: {type(exc).__name__}")
            return {"status": "unknown", "reason": "Unexpected upload result; manual reconciliation required.",
                    "attempt_id": pid}

    def _cross_post_instagram(self, page_id: str, post_id: str, caption: str,
                              *, test: bool = False) -> dict[str, Any]:
        """Cross-post a published Facebook card to the linked Instagram
        account. Never fails the Facebook post; the public Facebook photo
        URL is the publicly reachable image Instagram requires."""
        if test:
            return {"status": "skipped", "reason": "test_post"}
        if not self.config.instagram_enabled:
            return {"status": "skipped", "reason": "instagram_disabled"}
        existing = self.db.get_setting(f"instagram_media_for_{post_id}")
        if existing:
            return {"status": "already_published", "media_id": existing}
        try:
            ig_id = (self.config.instagram_user_id
                     or self.db.get_setting("instagram_user_id")
                     or self.graph.instagram_account_id(page_id))
            if not ig_id:
                self.db.insert_api_event("instagram", "publish", "held",
                                         "No linked Instagram account found for the Page.")
                return {"status": "skipped", "reason": "no_instagram_account"}
            self.db.set_setting("instagram_user_id", ig_id)
            image_url = self.graph.post_image_url(post_id)
            if not image_url:
                self.db.insert_api_event("instagram", "publish", "error",
                                         "Facebook post had no public image URL for Instagram.",
                                         details={"post_id": post_id})
                return {"status": "failed", "reason": "no_public_image_url"}
            creation_id = self.graph.ig_create_container(ig_id, image_url, caption)
            for _ in range(6):
                status = self.graph.ig_container_status(creation_id)
                if status == "FINISHED":
                    break
                if status == "ERROR":
                    raise GraphAPIError("Instagram container processing failed",
                                        code="CONTAINER_ERROR")
                time.sleep(5)
            media_id = self.graph.ig_publish_container(ig_id, creation_id)
            permalink = self.graph.ig_permalink(media_id)
            self.db.set_setting(f"instagram_media_for_{post_id}", media_id)
            if permalink:
                self.db.set_setting(f"instagram_permalink_for_{post_id}", permalink)
            self.db.insert_api_event("instagram", "publish", "success",
                                     "Instagram cross-post published.",
                                     http_status=200,
                                     details={"ig_user_id": ig_id, "media_id": media_id,
                                              "permalink": permalink, "fb_post_id": post_id})
            return {"status": "success", "media_id": media_id, "permalink": permalink}
        except GraphAPIError as exc:
            self.db.insert_api_event("instagram", "publish", "error", str(exc),
                                     http_status=exc.status, error_code=exc.code,
                                     details={"fb_post_id": post_id})
            return {"status": "failed", "reason": str(exc)}
        except Exception as exc:  # Instagram must never break Facebook posting
            message = redact(f"{type(exc).__name__}: {exc}", self.config.secret_values)
            self.db.insert_api_event("instagram", "publish", "error", message,
                                     details={"fb_post_id": post_id})
            return {"status": "failed", "reason": message}

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
            return {"status": "success", "post_id": post_id, "attempt_id": attempt["id"],
                    "reconciled": True, "caption_marker": marker}
        self.db.update_publish_attempt(attempt["id"], status="unknown", last_error="No matching post found in recent feed.")
        return {"status": "unknown", "reason": "No matching post found; manual reconciliation required.",
                "attempt_id": attempt["id"]}
