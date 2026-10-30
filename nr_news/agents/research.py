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
from .verification import editorial_exclusion_text

PRIORITY_PATTERNS = {
    # Regional first: Hyderabad/Telangana and district-level public-interest news.
    "regional": re.compile(
        r"hyderabad|حیدرآباد|telangana|تلنگانہ|secunderabad|سکندرآباد|ghmc|hmwssb|hydraa|hmda|tsrtc|"
        r"ranga reddy|rangareddy|medchal|sangareddy|nalgonda|warangal|karimnagar|nizamabad|khammam|"
        r"mahbubnagar|adilabad|siddipet|sircilla|kothagudem|bhadradri|jagtial|peddapalli|mancherial|"
        r"nirmal|komaram bheem|asifabad|gadwal|narayanpet|vikarabad|medak|yadadri|bhuvanagiri|"
        r"jangaon|mahabubabad|mulugu|jogulamba|wanaparthy|nagarkurnool|rajanna", re.I),
    # More political news: government, elections, courts, policy and parties.
    "political": re.compile(
        r"election|parliament|assembly|cabinet|government|policy|court|supreme court|high court|"
        r"minister|chief minister|prime minister|president|governor|bjp|congress|brs|aimim|"
        r"politician|political|party|candidate|vote|manifesto|law|bill|ordinance|scheme|"
        r"election commission|lok sabha|rajya sabha|vidhan sabha|panchayat|municipal|local body|"
        r"الیکشن|حکومت|وزیر|عدالت|اسمبلی|پارلیمنٹ|صدر|گورنر|سیاست|سیاسی|پارٹی|امیدوار|ووٹ|"
        r"منشور|قانون|بل|پالیسی|اسکیم|سرکاری|حکومتی", re.I),
    # War and conflict news (operator: always important for Facebook).
    "war": re.compile(
        r"war|conflict|ceasefire|missile|airstrike|air strike|army|military|defence|defense|"
        r"border clash|troops|soldier|invasion|bombard|shelling|جنگ|حملہ|فوج|دفاع|سرحد|میزائل|"
        r"جھڑپ|گولہ باری", re.I),
    # Government decisions: approvals, schemes, policy moves, price calls.
    "govt_decision": re.compile(
        r"cabinet approv|approv\w* (a |the )?(scheme|policy|bill|project|package)|"
        r"government (approv|announc|launch|decid|clear)|announces scheme|new policy|"
        r"ordinance|price (cut|hike|revision)|scheme (launch|approv)|"
        r"کابینہ|اسکیم منظور|حکومت نے منظور|فیصلہ", re.I),
    # International affairs after regional and national politics.
    "international": re.compile(
        r"international|world|global|foreign|diplomacy|diplomatic|bilateral|summit|"
        r"united nations|g20|brics|saarc|china|pakistan|russia|ukraine|israel|gaza|iran|"
        r"saudi|uae|japan|nepal|sri lanka|bangladesh|america|britain|france|germany|"
        r"europe|africa|asia|بین الاقوامی|دنیا|عالمی|سفارت|سفارتی|غیر ملکی|چین|پاکستان|روس|"
        r"یوکرین|اسرائیل|غزہ|ایران|سعودی|متحدہ عرب امارات|جاپان|نیپال|سری لنکا|بنگلہ دیش", re.I),
    # Public-interest development and economy news after politics.
    "development": re.compile(
        r"education|employment|jobs|health|agriculture|transport|infrastructure|business|"
        r"technology|science|climate|weather|cyclone|rain|monsoon|budget|gdp|inflation|market|"
        r"railway|metro|airport|highway|road|bridge|power|energy|water|irrigation|hospital|"
        r"vaccine|disease|crop|farmer|tgpsc|exam|recruitment|تعلیم|ملازمت|صحت|زراعت|"
        r"ٹرانسپورٹ|انفراسٹرکچر|معیشت|ٹیکنالوجی|سائنس|موسم|بارش|بجٹ|منڈی|ریلوے|"
        r"میٹرو|ایئرپورٹ|سڑک|پل|بجلی|پانی|آبپاشی|ہسپتال|فصل|کسان|امتحان|بھرتی", re.I),
}

CONTROVERSIAL_PROTEST = re.compile(
    r"impeachment|resign\w+ (demand|call)|demand\w* .*resign|vote chori|vote theft|"
    r"مواخذ|استعفیٰ|ووٹ چوری", re.I)

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

    # Sources the desk wants first for Hyderabad/Telangana, India and world.
    PREFERRED_PUBLISHERS = ("The Hindu", "The Indian Express", "India Today")

    @staticmethod
    def priority(candidate: Candidate) -> int:
        """Editorial order (operator, 29 Oct 2026: choose more political
        news, Facebook included): regional politics first, then national
        politics, then regional news, then India/world, then other
        public-interest news. Stories from the preferred national
        dailies get a small tie-break boost."""
        text = " ".join([
            candidate.original_headline, candidate.body_excerpt, candidate.url,
            candidate.category or "", candidate.publisher or "",
        ])
        regional = bool(PRIORITY_PATTERNS["regional"].search(text))
        # Politics is judged from the story itself plus an explicit desk
        # section (politics/elections) — a generic category word such as
        # "government" must not promote an education notice over world news.
        section = (candidate.category or "").split(",")[0].strip().lower()
        political = bool(PRIORITY_PATTERNS["political"].search(
            candidate.original_headline + " " + candidate.body_excerpt + " " + candidate.url
        )) or section in {"politics", "political pulse", "political-pulse", "elections"}
        international = bool(PRIORITY_PATTERNS["international"].search(text))
        development = bool(PRIORITY_PATTERNS["development"].search(text))
        war = bool(PRIORITY_PATTERNS["war"].search(text))
        if regional and (political or war):
            score = 16
        elif war:
            score = 14
        elif political and not international:
            score = 14
        elif regional:
            score = 13
        elif political and international:
            score = 11
        elif international:
            score = 8
        elif development:
            score = 6
        else:
            score = 1
        if candidate.publisher in ResearchAgent.PREFERRED_PUBLISHERS:
            score += 1
        return score

    @staticmethod
    def facebook_bucket(candidate: Candidate) -> str:
        """What Facebook may auto-publish (operator, 29 Oct 2026):
        politics, government decisions, war, or the day's important
        regional news. Everything else returns 'other' and is held
        back from the Facebook queue — no filler stories. Controversial
        protest material (resignation/impeachment demands, vote-theft
        allegations) is never published, per operator rule."""
        text = " ".join([
            candidate.original_headline, candidate.body_excerpt, candidate.url,
            candidate.category or "",
        ])
        if CONTROVERSIAL_PROTEST.search(text):
            return "other"
        section = (candidate.category or "").split(",")[0].strip().lower()
        if PRIORITY_PATTERNS["war"].search(text):
            return "war"
        if PRIORITY_PATTERNS["govt_decision"].search(text):
            return "govt"
        if PRIORITY_PATTERNS["political"].search(
                candidate.original_headline + " " + candidate.body_excerpt
                + " " + candidate.url) or section in {
                    "politics", "political pulse", "political-pulse", "elections"}:
            return "politics"
        if PRIORITY_PATTERNS["regional"].search(text):
            return "day"
        return "other"

    def ingest(self, source_results: list[SourceResult], mode: str) -> list[str]:
        """Persist candidates and enqueue immediate verification for new items."""
        new_candidate_ids: list[str] = []
        for result in source_results:
            for candidate in result.candidates:
                row = self._to_row(candidate, mode)
                cid, created = self.db.insert_candidate(row)
                if not created:
                    continue
                if editorial_exclusion_text(
                    candidate.original_headline, candidate.url,
                    candidate.category or "", candidate.body_excerpt,
                ):
                    self.db.update_candidate(cid, status="excluded", review_reason="editorial_exclusion")
                    self.db.insert_api_event(
                        "agent_a", "editorial_filter", "excluded",
                        "No-crime editorial policy excluded this candidate before verification.",
                        details={"candidate_id": cid, "headline": candidate.original_headline},
                    )
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
                    dedupe_key=f"verify:{cid}", priority=priority,
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
