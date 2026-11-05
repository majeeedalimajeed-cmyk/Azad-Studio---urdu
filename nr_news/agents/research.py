"""Agent A — research, extraction and original Urdu summarisation."""
from __future__ import annotations

import re
from typing import Any

from ..config import CONFIG
from ..db import Database
from ..llm import AgentUnavailable, LLMClient
from ..models import Candidate, SourceResult, VerificationResult
from ..util import (canonical_url, json_dumps, numbers, numeric_atoms, parse_iso, sha256_text,
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
        r"\bwar\b|conflict|ceasefire|missile|airstrike|air strike|army|military|defence|defense|"
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

UNREST = re.compile(r"\bprotest\w*\b|\b(?:riot|riots|rioting|demonstration|demonstrations|rally|rallies|unrest|impeachment)\b", re.I)

RESEARCH_SYSTEM = """You are an English news editor. Use only the supplied original article and verified evidence.
Only write straight-news coverage of an event that happened recently, not opinion, columns, historical retrospectives or promotional material. Never present a past event as happening now; preserve the exact timing of the event. Write a concise original English headline and exactly three brief English bullets. Each bullet must be at most 105 characters including spaces so both languages fit on one card. The schema has legacy key names: put English in urdu_headline and urdu_bullets, NOT Urdu script.
Preserve names, dates, figures, locations, titles, uncertainties and attribution. Never present a proposal as an approved decision. Never invent facts, sources or quotations. Exclude partisan accusations, political claims, opinions, motivations and speculative analysis; cover only verifiable, neutral event facts. Never transfer an attributed statement from one speaker to another. Do not conflate completed projects opened or inaugurated with new projects whose foundation stones were laid; distinguish them explicitly or omit the new projects. Omit anything whose precise attribution is unclear. Do not copy wording verbatim. Flag any doubt for human review.
Return ONLY one JSON object using exactly this shape: {"urdu_headline": "English headline", "urdu_bullets": ["English bullet 1", "English bullet 2", "English bullet 3"], "verification_notes": [], "facts": {"names": [], "dates": [], "figures": [], "locations": [], "claim_status": "confirmed"}, "requires_human_review": false}. The facts field MUST be an object, never a list.
"""


class ResearchAgent:
    def __init__(self, db: Database | None = None, llm: LLMClient | None = None, config=CONFIG):
        self.db = db or Database(config.db_path)
        self.llm = llm or LLMClient(config)
        self.config = config

    # Sources the desk wants first for Hyderabad/Telangana, India and world.
    PREFERRED_PUBLISHERS = ("The Hindu", "India Today", "The Wire")

    @staticmethod
    def priority(candidate: Candidate) -> int:
        """Politics first across all sections; prefer the named leaders,
        then regional and national politics. Important non-political news
        remains eligible and publishers get a small tie-break boost."""
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
        )) or section in {"politics", "political pulse", "political-pulse", "elections"} or bool(
            re.search(r"revanth reddy|asaduddin owaisi|rahul gandhi|priyanka gandhi|sanjay singh", text, re.I))
        international = bool(PRIORITY_PATTERNS["international"].search(text))
        development = bool(PRIORITY_PATTERNS["development"].search(text))
        war = bool(PRIORITY_PATTERNS["war"].search(text))
        # Politics always precedes every other section. Prioritise the
        # operator's named leaders without suppressing other important stories.
        if political:
            score = 50 + (2 if regional else (0 if international else 1))
            if re.search(r"revanth reddy|asaduddin owaisi", text, re.I):
                score += 10
            elif re.search(r"rahul gandhi|priyanka gandhi|sanjay singh", text, re.I):
                score += 5
        elif war:
            score = 40
        elif UNREST.search(text):
            score = 38
        elif regional:
            score = 35
        elif international:
            score = 25
        elif development:
            score = 20
        else:
            score = 10
        if candidate.publisher in ResearchAgent.PREFERRED_PUBLISHERS:
            score += 1
        return score

    @staticmethod
    def facebook_bucket(candidate: Candidate) -> str:
        """Classify stories; protests and unrest remain eligible for review.

        Verification and source-based factual review still apply; this bucket
        does not approve unverified claims or create publication permission.
        """
        text = " ".join([
            candidate.original_headline, candidate.body_excerpt, candidate.url,
            candidate.category or "",
        ])
        section = (candidate.category or "").split(",")[0].strip().lower()
        if PRIORITY_PATTERNS["war"].search(text):
            return "war"
        if UNREST.search(text):
            return "politics"
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
                        "Graphic or unverified content excluded before verification.",
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
            "reported_facts_excerpt": candidate.get("body_excerpt", "")[:10000],
            "published_at": candidate.get("published_at"),
            "updated_at": candidate.get("updated_at"),
            "verification_evidence": verification.evidence,
            "verification_reasons": verification.reasons,
            "output_language": "English; use legacy JSON keys urdu_headline and urdu_bullets",
        }
        data = self.llm.chat_json(system=RESEARCH_SYSTEM, user=payload, model=self.config.llm_model)
        self._validate_output(data)
        return data

    @staticmethod
    def _validate_output(data: dict[str, Any]) -> None:
        if not isinstance(data.get("urdu_headline"), str) or not data["urdu_headline"].strip():
            raise AgentUnavailable("Agent A returned no Urdu headline")
        bullets = data.get("urdu_bullets")
        if not isinstance(bullets, list) or len(bullets) != 3:
            raise AgentUnavailable("Agent A must return exactly three English bullets")
        if any(isinstance(x, str) and len(x) > 125 for x in bullets):
            raise AgentUnavailable("Agent A bullets exceed the single-card size limit")
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
                declared |= numeric_atoms(str(value))
        allowed = declared
        generated = numeric_atoms(data["urdu_headline"] + " " + " ".join(bullets))
        if generated and not generated.issubset(allowed):
            # The pipeline will still perform a stricter evidence check before rendering.
            data["verification_notes"] = list(data.get("verification_notes", [])) + [
                "Generated Urdu contains a number that needs evidence cross-check."
            ]
            data["requires_human_review"] = True
