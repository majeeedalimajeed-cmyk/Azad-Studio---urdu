"""Durable orchestration for the AZAD STUDIO newsroom.

Source scan -> durable job -> verification -> Urdu summary -> separate Roman
Urdu translation -> validation -> card -> visual inspection -> publishing.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .agents import (CardAgent, MediaAgent, PublishingAgent, ResearchAgent,
                     RomanUrduTranslatorAgent, TranslationValidator, VerificationAgent,
                     VideoWorkflow)
from .agents.verification import EvidenceFetcher, editorial_exclusion_text
from .config import CONFIG
from .db import Database
from .llm import AgentUnavailable
from .models import Candidate, EditorialContent, SourceResult, VerificationResult
from .sources import SourceMonitor
from .util import (canonical_url, clean_text, iso, is_official_url, json_dumps,
                   numeric_atoms, redact, sha256_text)


class NewsPipeline:
    def __init__(self, config=CONFIG, db: Database | None = None):
        self.config = config
        self.db = db or Database(config.db_path)
        self.source_monitor = SourceMonitor(self.db, config=config)
        self.research = ResearchAgent(self.db, config=config)
        self.verification = VerificationAgent(self.db, config=config)
        self.evidence = EvidenceFetcher(config=config)
        self.translator = RomanUrduTranslatorAgent(config=config)
        self.validator = TranslationValidator(config=config)
        self.media = MediaAgent(self.db, config=config)
        self.cards = CardAgent(self.db, config=config)
        self.publisher = PublishingAgent(self.db, config=config)
        self.video = VideoWorkflow(self.db, config=config)

    def scan(self, mode: str = "hourly", source_results: list[SourceResult] | None = None) -> dict[str, Any]:
        results = source_results if source_results is not None else self.source_monitor.monitor_all(mode)
        for result in results:
            self.db.record_source_status(result.source, result.ok, result.item_count,
                                         redact(result.message, self.config.secret_values),
                                         result.http_status)
        new_ids = self.research.ingest(results, mode)
        self.db.insert_api_event("scheduler", "scan", "ok",
                                 f"{mode} scan examined {len(results)} configured sources",
                                 details={"new_candidates": len(new_ids),
                                          "sources": [{"name": r.source, "ok": r.ok,
                                                       "items": r.item_count} for r in results]})
        return {
            "mode": mode,
            "sources": [{"source": r.source, "ok": r.ok, "item_count": r.item_count,
                         "message": redact(r.message, self.config.secret_values),
                         "http_status": r.http_status} for r in results],
            "new_candidates": new_ids,
            "jobs_queued": len(new_ids),
        }

    def drain_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        outputs: list[dict[str, Any]] = []
        for _ in range(limit):
            job = self.db.claim_job()
            if not job:
                break
            try:
                payload = json.loads(job["payload_json"])
                result = self.process_job(job["type"], payload)
                self.db.finish_job(job["id"])
                outputs.append({"job_id": job["id"], "type": job["type"], **result})
            except Exception as exc:
                message = redact(f"{type(exc).__name__}: {exc}", self.config.secret_values)
                self.db.finish_job(job["id"], error=message)
                self.db.insert_api_event("scheduler", "job", "failed", message,
                                         details={"job_id": job["id"], "type": job["type"]})
                outputs.append({"job_id": job["id"], "type": job["type"], "status": "job_error", "reason": message})
        return outputs

    def process_job(self, job_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        if job_type == "scan":
            return self.scan(payload.get("mode", "hourly"))
        if job_type != "verify_story":
            return {"status": "ignored", "reason": "unknown_job_type"}
        candidate_id = payload["candidate_id"]
        extra_urls = payload.get("evidence_urls", [])
        verification = self.verification.verify(
            candidate_id, extra_evidence_urls=extra_urls,
            provided_evidence_text=payload.get("evidence_text"),
        )
        if not verification.approved:
            return {"status": "held", "candidate_id": candidate_id,
                    "reasons": verification.reasons, "evidence": verification.evidence}
        return self.generate_from_verified(candidate_id, verification, extra_urls)

    def _official_urls(self, candidate: dict[str, Any], extra_urls: list[str] | None = None) -> list[str]:
        raw = _json(candidate.get("raw_json"), {})
        links = _json(candidate.get("external_links_json"), [])
        urls = [str(x) for x in (links if isinstance(links, list) else [])]
        urls += [str(x) for x in (raw.get("evidence_urls", []) or [])]
        urls += [str(x) for x in (extra_urls or [])]
        return [u for u in dict.fromkeys(urls) if is_official_url(u, self.config.official_host_suffixes)]

    def _official_evidence_text(self, urls: list[str]) -> str:
        chunks: list[str] = []
        for url in urls[:3]:
            text, _, _ = self.evidence.fetch_text(url)
            chunks.append(text)
        return " ".join(chunks)

    def generate_from_verified(self, candidate_id: str, verification: VerificationResult,
                               extra_urls: list[str] | None = None) -> dict[str, Any]:
        candidate = self.db.get_candidate(candidate_id)
        if not candidate:
            return {"status": "held", "reason": "candidate_not_found"}
        official_urls = self._official_urls(candidate, extra_urls)
        evidence_text = self._official_evidence_text(official_urls)
        try:
            urdu = self.research.summarize_urdu(candidate, verification)
        except AgentUnavailable as exc:
            self.db.add_review(candidate_id, verification.cluster_id, "urdu_generation_unavailable",
                               {"reason": str(exc), "verification": verification.to_dict()})
            self.db.insert_api_event("agent_a", "summarize", "held", str(exc),
                                     details={"candidate_id": candidate_id})
            return {"status": "held", "candidate_id": candidate_id, "reason": str(exc)}
        if urdu.get("requires_human_review"):
            self.db.add_review(candidate_id, verification.cluster_id, "agent_a_requested_review",
                               {"notes": urdu.get("verification_notes", [])})
            return {"status": "held", "candidate_id": candidate_id, "reason": "Agent A requested human review"}

        facts = urdu.get("facts", {})
        notes = list(urdu.get("verification_notes", [])) + list(verification.evidence)
        try:
            roman = self.translator.translate(urdu, facts=facts, verification_notes=notes)
        except AgentUnavailable as exc:
            self.db.add_review(candidate_id, verification.cluster_id, "roman_urdu_translation_unavailable",
                               {"reason": str(exc), "urdu": urdu})
            self.db.insert_api_event("agent_c", "translate", "held", str(exc),
                                     details={"candidate_id": candidate_id})
            return {"status": "held", "candidate_id": candidate_id, "reason": str(exc)}

        validation = self.validator.validate(
            urdu, roman, facts=facts, verification_notes=notes, evidence_text=evidence_text,
        )
        if not validation["ok"]:
            self.db.add_review(candidate_id, verification.cluster_id, "translation_validation_failed",
                               {"errors": validation["errors"], "warnings": validation["warnings"],
                                "urdu": urdu, "roman": roman})
            self.db.insert_api_event("translation_validator", "validate", "held",
                                     "; ".join(validation["errors"][:3]),
                                     details={"candidate_id": candidate_id, "errors": validation["errors"]})
            return {"status": "held", "candidate_id": candidate_id,
                    "reason": "Translation validation failed", "errors": validation["errors"]}

        raw = _json(candidate.get("raw_json"), {})
        source_urls = [candidate["url"]] + official_urls
        source_urls = list(dict.fromkeys(source_urls))
        media_list = self.media.select_many(
            candidate, raw.get("media"),
            query_context={"urdu": urdu, "facts": facts, "pixabay_query": raw.get("pixabay_query")},
        )
        content = EditorialContent(
            urdu_headline=clean_text(urdu["urdu_headline"], 240),
            urdu_bullets=[clean_text(x, 320) for x in urdu["urdu_bullets"]],
            roman_headline=clean_text(roman["roman_headline"], 240),
            roman_bullets=[clean_text(x, 320) for x in roman["roman_bullets"]],
            verification_notes=notes,
            source_urls=source_urls,
            publication_time=iso(),
            media=media_list or None,
        )
        if editorial_exclusion_text(
            content.urdu_headline, " ".join(content.urdu_bullets),
            content.roman_headline, " ".join(content.roman_bullets),
            candidate.get("original_headline", ""), candidate.get("body_excerpt", ""),
        ):
            self.db.add_review(candidate_id, verification.cluster_id, "no_crime_policy_hold", {
                "reason": "Generated editorial matched the no-crime exclusion filter.",
            })
            return {"status": "held", "candidate_id": candidate_id,
                    "reason": "No-crime editorial policy hold"}
        status = "approved" if (
            self.config.auto_approve_verified and self.config.operator_approved
            and self.config.auto_post_enabled
        ) else "pending_review"
        approved_by = "auto_policy" if status == "approved" else None
        editorial_id, created = self.db.insert_editorial(candidate_id, verification.cluster_id,
                                                         content.to_dict(), status=status)
        if approved_by:
            self.db.set_editorial_status(editorial_id, status, approved_by)
        self._write_audit(editorial_id, candidate, verification, urdu, roman, validation, source_urls, evidence_text)
        if not created:
            return {"status": "duplicate_editorial", "candidate_id": candidate_id,
                    "editorial_id": editorial_id}

        rendered = self.cards.render(editorial_id, content, media=media_list or None)
        publish_results: list[dict[str, Any]] = []
        for card in rendered:
            card_row = self.db.one("SELECT * FROM cards WHERE file_path=? ORDER BY created_at DESC LIMIT 1", (card.path,))
            if card_row:
                publish_results.append(self.publisher.publish(editorial_id, card_row,
                                                              dry_run=self.config.dry_run))
        if self.config.video_enabled:
            try:
                self.video.prepare(editorial_id, content)
            except Exception as exc:
                # Optional workflow must never block card publishing.
                self.db.insert_api_event("video", "prepare", "error", redact(str(exc), self.config.secret_values),
                                         details={"editorial_id": editorial_id})
        return {"status": "rendered", "candidate_id": candidate_id, "editorial_id": editorial_id,
                "cards": [c.path for c in rendered], "publish": publish_results}

    def _write_audit(self, editorial_id: str, candidate: dict[str, Any], verification: VerificationResult,
                     urdu: dict[str, Any], roman: dict[str, Any], validation: dict[str, Any],
                     source_urls: list[str], evidence_text: str) -> None:
        payload = {
            "editorial_id": editorial_id,
            "created_at": iso(),
            "candidate": {
                "id": candidate["id"], "source": candidate["source"], "publisher": candidate["publisher"],
                "url": candidate["url"], "canonical_url": candidate["canonical_url"],
                "original_headline": candidate["original_headline"],
                "published_at": candidate["published_at"], "updated_at": candidate["updated_at"],
                "title_hash": candidate["title_hash"], "fingerprint": candidate["fingerprint"],
                "body_excerpt_hash": candidate["body_excerpt_hash"],
            },
            "verification": verification.to_dict(),
            "approved_urdu": urdu,
            "roman_urdu": roman,
            "translation_validation": validation,
            "source_urls": source_urls,
            "official_evidence_excerpt": evidence_text[:2500],
            "logo_sha256": self.config.logo_sha256,
        }
        path = self.config.audit_dir / f"editorial-{editorial_id[:12]}.json"
        path.write_text(json_dumps(payload), encoding="utf-8")

    def run_fixture(self, path: Path, *, dry_run: bool = True,
                    use_photos: bool = False) -> dict[str, Any]:
        """Render an operator-provided, already-verified bilingual fixture.

        This is used for offline launch validation. It never invents news:
        all facts and source URLs must be present in the fixture.
        """
        fixture = json.loads(Path(path).read_text(encoding="utf-8"))
        candidate_data = fixture["candidate"]
        candidate = Candidate(**candidate_data)
        row = ResearchAgent._to_row(candidate, "fixture")
        raw = row["raw"]
        raw["evidence_urls"] = fixture.get("evidence_urls", [])
        raw["fixture"] = True
        row["raw"] = raw
        cid, created = self.db.insert_candidate(row)
        if not created:
            # A source scan may already have stored a shorter excerpt for this
            # URL. Refresh it with the operator-supplied verified fixture data
            # before Agent B and the duplicate checks run.
            self.db.update_candidate(
                cid,
                body_excerpt=row["body_excerpt"],
                body_excerpt_hash=row["body_excerpt_hash"],
                raw_json=json_dumps(raw),
                external_links_json=json_dumps(row["external_links"]),
                published_at=row.get("published_at"),
                original_headline=row["original_headline"],
                category=row.get("category"),
                title_hash=row["title_hash"],
                fingerprint=row["fingerprint"],
                image_url=row.get("image_url"),
                image_credit=row.get("image_credit"),
                image_license=row.get("image_license"),
                language=row.get("language"),
                status="new",
                review_reason=None,
            )
            # Re-running the same fixture is a duplicate event, not a new post.
            existing = self.db.one(
                "SELECT id FROM editorials WHERE content_hash=?",
                (sha256_text(json_dumps({
                    "urdu_headline": fixture["editorial"]["urdu_headline"],
                    "urdu_bullets": fixture["editorial"]["urdu_bullets"],
                    "roman_headline": fixture["editorial"]["roman_headline"],
                    "roman_bullets": fixture["editorial"]["roman_bullets"],
                })),),
            )
            if existing:
                return {"status": "duplicate_fixture", "candidate_id": cid,
                        "editorial_id": existing["id"], "cards": []}
        candidate_row = self.db.get_candidate(cid)
        result = self.verification.verify(
            cid, extra_evidence_urls=fixture.get("evidence_urls", []),
            provided_evidence_text=fixture.get("evidence_text", ""),
        )
        if not result.approved:
            return {"status": "fixture_verification_failed", "reasons": result.reasons,
                    "evidence": result.evidence}

        editorial = fixture["editorial"]
        urdu = {"urdu_headline": editorial["urdu_headline"], "urdu_bullets": editorial["urdu_bullets"]}
        roman = {
            "roman_headline": editorial["roman_headline"], "roman_bullets": editorial["roman_bullets"],
            "entity_map": editorial.get("entity_map", []),
            "uncertainty_map": editorial.get("uncertainty_map", []),
            "no_new_facts": True,
        }
        evidence_text = fixture.get("evidence_text", "")
        if not evidence_text:
            evidence_text = self._official_evidence_text(self._official_urls(candidate_row, fixture.get("evidence_urls", [])))
        validation = self.validator.validate(
            urdu, roman, facts=editorial.get("facts", {}),
            verification_notes=editorial.get("verification_notes", []),
            evidence_text=evidence_text,
        )
        if not validation["ok"]:
            return {"status": "fixture_translation_failed", "errors": validation["errors"],
                    "warnings": validation["warnings"]}

        official_urls = self._official_urls(candidate_row, fixture.get("evidence_urls", []))
        source_urls = list(dict.fromkeys([candidate_row["url"]] + official_urls + editorial.get("source_urls", [])))
        media_list: list[dict[str, Any]] = []
        if use_photos:
            media_list = self.media.select_many(
                candidate_row, fixture.get("media"),
                query_context={
                    "editorial": editorial,
                    "candidate": candidate_row,
                    "pixabay_query": fixture.get("pixabay_query"),
                },
            )
        content = EditorialContent(
            urdu_headline=editorial["urdu_headline"],
            urdu_bullets=editorial["urdu_bullets"],
            roman_headline=editorial["roman_headline"],
            roman_bullets=editorial["roman_bullets"],
            verification_notes=editorial.get("verification_notes", []),
            source_urls=source_urls,
            publication_time=editorial.get("publication_time") or iso(),
            media=media_list or None,
        )
        status = "approved" if dry_run else "pending_review"
        eid, created_editorial = self.db.insert_editorial(cid, result.cluster_id, content.to_dict(), status=status)
        if dry_run:
            self.db.set_editorial_status(eid, "approved", "dry_run_fixture")
        if not created_editorial:
            return {"status": "duplicate_editorial", "candidate_id": cid, "editorial_id": eid, "cards": []}
        cards = self.cards.render(eid, content, media=media_list or None)
        publish_results = []
        for card in cards:
            card_row = self.db.one("SELECT * FROM cards WHERE file_path=? ORDER BY created_at DESC LIMIT 1", (card.path,))
            if card_row:
                publish_results.append(self.publisher.publish(eid, card_row, dry_run=dry_run))
        audit = {
            "fixture_path": str(path), "candidate": candidate_row,
            "verification": result.to_dict(), "translation_validation": validation,
            "source_urls": source_urls, "publish_results": publish_results,
            "media": media_list, "media_count": len(media_list),
        }
        (self.config.audit_dir / f"fixture-{eid[:12]}.json").write_text(json_dumps(audit), encoding="utf-8")
        if self.config.video_enabled and dry_run:
            self.video.prepare(eid, content)
        return {"status": "rendered", "candidate_id": cid, "editorial_id": eid,
                "cards": [c.path for c in cards], "publish": publish_results,
                "translation_validation": validation}


def _json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
