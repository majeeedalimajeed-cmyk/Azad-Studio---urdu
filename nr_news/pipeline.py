"""Durable orchestration for the AZAD STUDIO newsroom.

Source scan -> durable job -> verification -> Urdu summary -> separate Roman
Urdu translation -> validation -> card -> visual inspection -> publishing.
"""
from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlparse
import re
from bs4 import BeautifulSoup
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
from .source_policy import source_allowed
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
        requeued = self.requeue_llm_held() + self.requeue_verification_held()
        publish_retried = self.publish_approved_pending()
        self.db.insert_api_event("scheduler", "scan", "ok",
                                 f"{mode} scan examined {len(results)} configured sources",
                                 details={"new_candidates": len(new_ids),
                                          "requeued_llm_held": requeued,
                                          "sources": [{"name": r.source, "ok": r.ok,
                                                       "items": r.item_count} for r in results]})
        return {
            "mode": mode,
            "sources": [{"source": r.source, "ok": r.ok, "item_count": r.item_count,
                         "message": redact(r.message, self.config.secret_values),
                         "http_status": r.http_status} for r in results],
            "new_candidates": new_ids,
            "jobs_queued": len(new_ids),
            "requeued_held": requeued,
            "publish_retried": publish_retried,
        }

    def requeue_llm_held(self, limit: int = 10) -> int:
        """Retry stories held only because the LLM gateway was unavailable.

        Holds for editorial reasons (verification, validation, policy) are
        never auto-retried. Gateway holds get one fresh verify+generate job
        per scan (bounded), and stale stories exit via re-verification.
        """
        if not self.research.llm.available:
            return 0
        requeued = 0
        seen: set[str] = set()
        rows = self.db.open_reviews(
            ["urdu_generation_unavailable", "roman_urdu_translation_unavailable"],
            limit=limit * 3,
        )
        for row in rows:
            candidate_id = row.get("candidate_id")
            if not candidate_id:
                continue
            self.db.set_review_status(row["id"], "requeued")
            if candidate_id in seen or requeued >= limit:
                continue
            seen.add(candidate_id)
            candidate = self.db.get_candidate(candidate_id)
            if not candidate or candidate.get("status") == "excluded":
                continue
            self.db.enqueue_job(
                "verify_story", {"candidate_id": candidate_id},
                dedupe_key=f"verify:{candidate_id}", priority=5,
            )
            requeued += 1
        if requeued:
            self.db.insert_api_event(
                "scheduler", "requeue", "ok",
                f"Requeued {requeued} LLM-held stories after gateway recovery",
                details={"requeued": requeued},
            )
        return requeued

    def requeue_verification_held(self, limit: int = 10) -> int:
        """Re-verify held stories each scan (bounded, newest first).

        Corroboration can arrive after the first attempt; re-verification is
        cheap (no LLM call until approval) and stale stories stay held.
        """
        requeued = 0
        for candidate in self.db.candidates_by_status("held", limit=limit):
            self.db.close_open_reviews(candidate["id"], "verification_hold",
                                       status="requeued")
            self.db.enqueue_job(
                "verify_story", {"candidate_id": candidate["id"]},
                dedupe_key=f"verify:{candidate['id']}", priority=4,
            )
            requeued += 1
        if requeued:
            self.db.insert_api_event(
                "scheduler", "requeue", "ok",
                f"Requeued {requeued} verification-held stories for re-check",
                details={"requeued": requeued},
            )
        return requeued

    def publish_approved_pending(self, limit: int = 5) -> list[dict[str, Any]]:
        """Publish approved editorials whose earlier publish step never ran
        (e.g. it errored after rendering). Idempotency keys in the publisher
        make this safe against duplicates."""
        results: list[dict[str, Any]] = []
        # Rank by editorial priority (Hyderabad/Telangana, then politics,
        # then India/world, using the approved sources) and freshness — never oldest-backlog-first.
        approved = self.db.query("SELECT * FROM editorials WHERE status='approved'", ())

        def _rank(editorial: dict[str, Any]) -> tuple[int, str]:
            candidate_row = self.db.get_candidate(editorial["candidate_id"]) or {}
            try:
                probe = Candidate(
                    source=candidate_row.get("source", ""),
                    publisher=candidate_row.get("publisher", ""),
                    url=candidate_row.get("url", ""),
                    original_headline=candidate_row.get("original_headline", ""),
                    published_at=candidate_row.get("published_at"),
                    updated_at=candidate_row.get("updated_at"),
                    language=candidate_row.get("language", "en"),
                    body_excerpt=candidate_row.get("body_excerpt", ""),
                    category=candidate_row.get("category", ""),
                )
                score = ResearchAgent.priority(probe)
            except Exception:
                score = 0
            fresh = candidate_row.get("published_at") or editorial.get("created_at") or ""
            return (score, fresh)

        editorials = sorted(approved, key=_rank, reverse=True)[:limit]
        for editorial in editorials:
            # Never revive an old-format or ineligible queued post.
            if not self.config.dry_run and self.db.get_setting(f"english_roman_verified:{editorial['id']}") != "true":
                continue
            source = self.db.get_candidate(editorial["candidate_id"]) or {}
            if not self.config.dry_run and not source_allowed(source):
                continue
            try:
                probe = Candidate(source=source.get("source", ""), publisher=source.get("publisher", ""),
                    url=source.get("url", ""), original_headline=source.get("original_headline", ""),
                    published_at=source.get("published_at"), updated_at=source.get("updated_at"),
                    language=source.get("language", "en"), body_excerpt=source.get("body_excerpt", ""),
                    category=source.get("category", ""))
                if editorial_exclusion_text(probe.original_headline, probe.body_excerpt):
                    continue
            except Exception:
                continue
            success = self.db.one(
                "SELECT id FROM publish_attempts WHERE editorial_id=? AND status='success'",
                (editorial["id"],),
            )
            if success:
                continue
            card_row = self.db.one(
                "SELECT * FROM cards WHERE editorial_id=? AND visual_status='passed' "
                "ORDER BY page_number LIMIT 1",
                (editorial["id"],),
            )
            if not card_row:
                continue
            if not Path(card_row["file_path"]).exists():
                # Card PNGs are not persisted between CI runs; re-render from
                # the stored editorial (fresh licensed photos when available)
                # so an approved story is never stranded by a missing file.
                content = EditorialContent(
                    urdu_headline=editorial["urdu_headline"],
                    urdu_bullets=_json(editorial["urdu_bullets_json"], []),
                    roman_headline=editorial["roman_headline"],
                    roman_bullets=_json(editorial["roman_bullets_json"], []),
                    verification_notes=_json(editorial["verification_notes_json"], []),
                    source_urls=_json(editorial["source_urls_json"], []),
                    publication_time=editorial.get("publication_time") or iso(),
                    media=None,
                )
                candidate = self.db.get_candidate(editorial["candidate_id"]) or {}
                raw = _json(candidate.get("raw_json"), {}) if candidate else {}
                media_list = (
                    self.media.select_many(
                        candidate, raw.get("media"),
                        query_context={"pixabay_query": raw.get("pixabay_query")},
                    ) if candidate else []
                )
                rendered = self.cards.render(editorial["id"], content,
                                             media=media_list or None)
                if not rendered:
                    continue
                card_row = self.db.one(
                    "SELECT * FROM cards WHERE file_path=? ORDER BY created_at DESC LIMIT 1",
                    (rendered[0].path,),
                )
                if not card_row:
                    continue
            try:
                results.append(self.publisher.publish(
                    editorial["id"], card_row, dry_run=self.config.dry_run))
            except Exception as exc:  # never let one editorial block the scan
                message = redact(f"{type(exc).__name__}: {exc}", self.config.secret_values)
                self.db.insert_api_event("facebook", "publish", "error", message,
                                         details={"editorial_id": editorial["id"]})
                results.append({"status": "job_error", "reason": message})
        return results

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
        candidate = self.db.get_candidate(candidate_id) or {}
        if not source_allowed(candidate):
            return {"status": "held", "reason": "source_not_in_operator_list"}
        text = " ".join((candidate.get("original_headline", ""), candidate.get("body_excerpt", ""))).lower()
        # The two one-off posts predate this scheduler's database records.
        if ("aicte" in text and ("modrobs" in text or "four schemes" in text or "four major schemes" in text)) or ("treated" in text and "water" in text and ("revanth" in text or "green tanker" in text)):
            return {"status": "held", "reason": "story_already_posted_manually"}
        probe = Candidate(source=candidate.get("source", ""), publisher=candidate.get("publisher", ""),
                          url=candidate.get("url", ""), original_headline=candidate.get("original_headline", ""),
                          published_at=candidate.get("published_at"), updated_at=candidate.get("updated_at"),
                          language=candidate.get("language", "en"), body_excerpt=candidate.get("body_excerpt", ""),
                          category=candidate.get("category", ""))
        if editorial_exclusion_text(probe.original_headline, probe.body_excerpt):
            return {"status": "held", "reason": "editorial_exclusion"}
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

    def _read_original_article(self, candidate: dict[str, Any]) -> str:
        result = self.evidence.client.get(candidate.get("url", ""))
        if not result.ok:
            return ""
        source_host = (urlparse(candidate.get("url", "")).hostname or "").lower()
        result_host = (urlparse(result.url).hostname or "").lower()
        if source_host.removeprefix("www.") != result_host.removeprefix("www."):
            return ""
        soup = BeautifulSoup(result.text, "html.parser")
        container = soup.find("article") or soup.find("main")
        if not container:
            return ""
        for unwanted in container.select("nav, aside, footer, script, style, form, [class*='related'], [class*='recommend']"):
            unwanted.decompose()
        heading = container.find("h1") or soup.find("h1")
        parts = [heading.get_text(" ", strip=True)] if heading else []
        for paragraph in container.find_all("p"):
            text = clean_text(paragraph.get_text(" ", strip=True), 3000)
            if len(text) >= 45:
                parts.append(text)
        return clean_text(" ".join(parts), 12000)

    def generate_from_verified(self, candidate_id: str, verification: VerificationResult,
                               extra_urls: list[str] | None = None) -> dict[str, Any]:
        candidate = self.db.get_candidate(candidate_id)
        if not candidate:
            return {"status": "held", "reason": "candidate_not_found"}
        # Read the original article, not just the RSS title/excerpt. Fail
        # closed when blocked, paywalled, too short, or not the same story.
        article = self._read_original_article(candidate)
        title_words = set(re.findall(r"[a-z0-9]{4,}", candidate.get("original_headline", "").lower()))
        article_words = set(re.findall(r"[a-z0-9]{4,}", article.lower()))
        if len(article) < 600 or len(title_words & article_words) < max(2, len(title_words)//3):
            return {"status": "held", "candidate_id": candidate_id,
                    "reason": "original_article_unreadable_or_mismatched"}
        if editorial_exclusion_text(candidate.get("original_headline", ""), article):
            return {"status": "held", "candidate_id": candidate_id,
                    "reason": "article_matches_editorial_exclusion"}
        official_urls = self._official_urls(candidate, extra_urls)
        official_evidence = self._official_evidence_text(official_urls)
        evidence_text = article + " " + official_evidence
        # Model reads the real article, while the original DB record stays immutable.
        candidate_for_generation = dict(candidate, body_excerpt=article[:10000])
        try:
            urdu = self.research.summarize_urdu(candidate_for_generation, verification)
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
        if not validation["ok"] or not isinstance(validation.get("model_review"), dict) or validation["model_review"].get("consistent") is not True:
            if validation["ok"]:
                validation["errors"].append("Independent English/Roman translation review unavailable")
            self.db.add_review(candidate_id, verification.cluster_id, "translation_validation_failed",
                               {"errors": validation["errors"], "warnings": validation["warnings"],
                                "urdu": urdu, "roman": roman})
            self.db.insert_api_event("translation_validator", "validate", "held",
                                     "; ".join(validation["errors"][:3]),
                                     details={"candidate_id": candidate_id, "errors": validation["errors"]})
            return {"status": "held", "candidate_id": candidate_id,
                    "reason": "Translation validation failed", "errors": validation["errors"]}

        # A separate source-grounded review must explicitly approve every
        # claim in both languages. No approval on missing/error/uncertain output.
        try:
            fact_review = self.research.llm.chat_json(
                system=("Independently check every English and Roman Urdu claim against the original article. "
                        "Require exact support for names, dates, figures and attribution; proposals remain proposals. "
                        "Reject unsupported claims, uncertainty, mismatches, and prohibited protest coverage. "
                        "Also judge whether the news is important rather than trivial or promotional. "
                        "Return JSON with supported boolean, important boolean and issues array. Never infer missing facts."),
                user={"article": article[:10000], "english": urdu, "roman_urdu": roman},
                model=getattr(self.config, "validator_model", None) or self.config.llm_model,
                max_tokens=900,
            )
        except AgentUnavailable as exc:
            return {"status": "held", "candidate_id": candidate_id, "reason": "fact_review_unavailable", "error": str(exc)}
        if fact_review.get("supported") is not True:
            return {"status": "held", "candidate_id": candidate_id, "reason": "fact_review_not_approved"}
        if fact_review.get("important") is not True:
            return {"status": "held", "candidate_id": candidate_id, "reason": "importance_not_approved"}
        notes.append("Independent article-grounded fact review passed: " + json_dumps(fact_review))
        raw = _json(candidate.get("raw_json"), {})
        source_urls = [candidate["url"]] + official_urls
        source_urls = list(dict.fromkeys(source_urls))
        # Automated runs use a text-only card. Relevant, rights-cleared photos
        # can be added through a separately reviewed manual workflow; search
        # results and old cached media are never posted automatically.
        media_list: list[dict[str, Any]] = []
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
        self.db.set_setting(f"english_roman_verified:{editorial_id}", "true")

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
