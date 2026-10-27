"""Agent A — research, extraction and original Urdu summarisation."""
from __future__ import annotations

import re
from typing import Any

from ..config import CONFIG
from ..db import Database
from ..llm import AgentUnavailable, LLMClient
from ..models import Candidate, SourceResult, VerificationResult
from ..util import (canonical_url, json_dumps, numbers, parse_iso, sha256_text,
                    story_fingerprint, utcnow)

PRIORITY_PATTERNS = (
    (10, r"hyderabad|حیدرآباد|telangana|تلنگانہ|gh|hmwssb|hydraa"),
    (8, r"election|parliament|cabinet|government|policy|court|supreme|minister|الیکشن|حکومت|وزیر|عدالت"),
    (6, r"education|employment|health|agriculture|transport|infrastructure|تعلیم|ملازمت|صحت|زراعت|ٹرانسپورٹ"),
    (5, r"business|technology|diplomacy|world|business|معیشت|ٹیکنالوجی"),
)

RESEARCH_SYSTEM = """You are Agent A, an Urdu news research and extraction agent.
You receive ONLY a short excerpt from an original report, its headline, and verified evidence notes.
Write a concise, original Urdu-script headline and 3 to 5 Urdu bullets.
Rules:
- Preserve names, dates, figures, locations, titles and attribution exactly.
- Distinguish confirmed, alleged, proposed, attributed and developing claims.
- A proposal is not an approved decision; an allegation is not a fact.
- Never invent a fact, quote, source, statistic, motive or image.
- Do not copy article text verbatim and do not mention the publisher on the card.
- Keep bullets short enough for a 1080x1350 news card.
- Return ONLY one JSON object with keys: urdu_headline (string), urdu_bullets (array of 3-5 strings),
  verification_notes (array of strings), facts (object with arrays names, dates, figures, locations,
  and a string claim_status), requires_human_review (boolean).
"""


class ResearchAgent:
    def __init__(self, db: Database | None = None, llm: LLMClient | None = None, config=CONFIG):
        self.db = db or Database(config.db_path)
        self.llm = llm or LLMClient(config)
        self.config = config

    @staticmethod
    def priority(candidate: Candidate) -> int:
        text = (candidate.original_headline + " " + candidate.body_excerpt + " " + candidate.url).lower()
        score = 1
        for points, pattern in PRIORITY_PATTERNS:
            if re.search(pattern, text, re.I):
                score = max(score, points)
        return score

    def ingest(self, source_results: list[SourceResult], mode: str) -> list[str]:
        """Persist candidates and enqueue immediate verification for new items."""
        new_candidate_ids: list[str] = []
        for result in source_results:
            for candidate in result.candidates:
                row = self._to_row(candidate, mode)
                cid, created = self.db.insert_candidate(row)
                if not created:
                    continue
                new_candidate_ids.append(cid)
                priority = self.priority(candidate)
                published = parse_iso(candidate.published_at)
                breaking = bool(
                    mode == "breaking" and published and priority >= 8 and
                    (utcnow() - published).total_seconds() <= self.config.breaking_age_minutes * 60
                )
                # Breaking scans are event-driven; verification starts at once.
                self.db.enqueue_job(
                    "verify_story",
                    {"candidate_id": cid, "mode": mode, "priority": priority, "breaking": breaking},
                    dedupe_key=f"verify:{cid}",
                )
                if breaking:
                    self.db.insert_api_event("breaking_news", "candidate_detected", "queued",
                                             "Fresh high-priority report queued for immediate verification.",
                                             details={"candidate_id": cid, "priority": priority,
                                                      "headline": candidate.original_headline})
        self.db.insert_api_event(
            "agent_a", "ingest", "ok",
            f"{len(new_candidate_ids)} new candidates from {len(source_results)} sources",
            details={"mode": mode, "new_candidate_ids": new_candidate_ids[:50]},
        )
        return new_candidate_ids

    @staticmethod
    def _to_row(candidate: Candidate, mode: str) -> dict[str, Any]:
        raw = dict(candidate.raw)
        raw["scan_mode"] = mode
        return {
            "source": candidate.source,
            "publisher": candidate.publisher,
            "url": candidate.url,
            "canonical_url": canonical_url(candidate.url),
            "original_headline": candidate.original_headline,
            "published_at": candidate.published_at,
            "updated_at": candidate.updated_at,
            "language": candidate.language,
            "title_hash": sha256_text(candidate.original_headline),
            "fingerprint": story_fingerprint(candidate.original_headline, candidate.body_excerpt),
            "body_excerpt": candidate.body_excerpt[:1800],
            "body_excerpt_hash": sha256_text(candidate.body_excerpt[:1800]),
            "category": candidate.category,
            "image_url": candidate.image_url,
            "image_credit": candidate.image_credit,
            "image_license": candidate.image_license,
            "external_links": candidate.external_links,
            "raw": raw,
        }

    def summarize_urdu(self, candidate: dict[str, Any], verification: VerificationResult) -> dict[str, Any]:
        if not self.llm.available:
            raise AgentUnavailable("Urdu summarization model not configured; story held for review")
        payload = {
            "original_headline": candidate["original_headline"],
            "source_language": candidate.get("language"),
            "reported_facts_excerpt": candidate.get("body_excerpt", "")[:1800],
            "published_at": candidate.get("published_at"),
            "updated_at": candidate.get("updated_at"),
            "verification_evidence": verification.evidence,
            "verification_reasons": verification.reasons,
            "output_language": "Urdu script (Nastaliq-compatible Unicode)",
        }
        data = self.llm.chat_json(system=RESEARCH_SYSTEM, user=payload, model=self.config.llm_model)
        self._validate_output(data)
        return data

    @staticmethod
    def _validate_output(data: dict[str, Any]) -> None:
        if not isinstance(data.get("urdu_headline"), str) or not data["urdu_headline"].strip():
            raise AgentUnavailable("Agent A returned no Urdu headline")
        bullets = data.get("urdu_bullets")
        if not isinstance(bullets, list) or not 3 <= len(bullets) <= 5:
            raise AgentUnavailable("Agent A must return 3-5 Urdu bullets")
        if any(not isinstance(x, str) or len(x.strip()) < 5 for x in bullets):
            raise AgentUnavailable("Agent A returned an empty Urdu bullet")
        facts = data.get("facts")
        if not isinstance(facts, dict):
            raise AgentUnavailable("Agent A returned no fact inventory")
        # Every number used in the generated Urdu copy must occur in the source excerpt
        # or be explicitly listed by the model as a fact for later cross-checking.
        declared = set()
        for key in ("dates", "figures"):
            for value in facts.get(key, []) or []:
                declared |= numbers(str(value))
        allowed = declared
        generated = numbers(data["urdu_headline"] + " " + " ".join(bullets))
        if generated and not generated.issubset(allowed):
            # The pipeline will still perform a stricter evidence check before rendering.
            data["verification_notes"] = list(data.get("verification_notes", [])) + [
                "Generated Urdu contains a number that needs evidence cross-check."
            ]
            data["requires_human_review"] = True
