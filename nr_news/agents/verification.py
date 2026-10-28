"""Agent B — verification, editorial control, clustering and duplicate prevention."""
from __future__ import annotations

import io
import json
import re
import sys
from typing import Any

from bs4 import BeautifulSoup

from ..config import CONFIG, ROOT
from ..db import Database
from ..http_client import PoliteHTTPClient
from ..models import VerificationResult
from ..util import (clean_text, domain, is_official_url, iso, json_dumps,
                    numeric_atoms, parse_iso, similarity, tokens, utcnow)

VENDOR = ROOT / "vendor"
if VENDOR.exists() and str(VENDOR) not in sys.path:
    sys.path.insert(0, str(VENDOR))

EXCLUSION_PATTERNS = re.compile(
    r"murder|قتل|rape|جنسی زیادتی|زیادتی|robbery|ڈکیتی|theft|چوری|dacoity|loot|لُوٹ|firing|فائرنگ|"
    r"shooting|گولی|gangster|غنڈہ|kidnap|اغوا|abduct|assault|حملہ|stab|چاقو|graphic|dead body|لاش|"
    r"crime-news|\bcrime\b|criminal|جرائم|مجرم|suicide|خودکشی|accident|حادثہ|bomb|blast|دھماکہ|terror|دہشت|"
    r"police case|police custody|police arrest|\barrest(?:ed|s)?\b|گرفتار|accused|ملزم|bail|ضمانت|"
    r"\bfir\b|ایف آئی آر|chargesheet|چالان|convict(?:ed|ion)?|سزا|sentenced|jail|جیل|prison|قید|"
    r"fraud|دھوکہ|cheating|scam|گھوٹالہ|dowry|جہیز|harassment|ہراساں|molest|چھیڑ چھاڑ|"
    r"domestic violence|گھریلو تشدد|smuggl|اسمگل|drug|نشیلا|narcotic|نشہ|lynch|"
    r"gang|encounter|مقابلہ|shootout|burglar|robber|ڈاکو|pickpocket|جیب کتر|culprit|suspect|مشتبہ|"
    r"victim|متاثرہ|violent riot|پرتشدد فساد|rumou?r|افواہ|fake|جعلی|unverified|غیر مصدقہ",
    re.I,
)


def editorial_exclusion_text(*parts: str) -> bool:
    """Return True when any supplied text matches the no-crime editorial filter."""
    return bool(EXCLUSION_PATTERNS.search(" ".join(str(part or "") for part in parts)))

SENSITIVE_PATTERNS = re.compile(
    r"alleg|الزام|accus|دعوی|corruption|scam|probe|investigation|جانچ|"
    r"election|minister|party|court|protest|riot|clash|تصادم|arrest|گرفتار",
    re.I,
)


class EvidenceFetcher:
    def __init__(self, client: PoliteHTTPClient | None = None, config=CONFIG):
        self.client = client or PoliteHTTPClient(config)
        self.config = config

    def fetch_text(self, url: str) -> tuple[str, str, int | None]:
        r = self.client.get(url)
        if not r.ok:
            return "", r.message, r.status_code
        content_type = r.headers.get("content-type", "").lower()
        if "pdf" in content_type or r.content[:4] == b"%PDF":
            try:
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(r.content))
                text = "\n".join((page.extract_text() or "") for page in reader.pages[:5])
                return clean_text(text, 12000), "official PDF read", r.status_code
            except Exception as exc:
                return "", f"PDF parse failed: {type(exc).__name__}", r.status_code
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        return clean_text(soup.get_text(" ", strip=True), 12000), "official page read", r.status_code


class VerificationAgent:
    def __init__(self, db: Database | None = None, evidence_fetcher: EvidenceFetcher | None = None,
                 config=CONFIG):
        self.db = db or Database(config.db_path)
        self.evidence = evidence_fetcher or EvidenceFetcher(config=config)
        self.config = config

    def verify(self, candidate_id: str, extra_evidence_urls: list[str] | None = None,
               provided_evidence_text: str | None = None) -> VerificationResult:
        row = self.db.get_candidate(candidate_id)
        if not row:
            result = VerificationResult(False, ["candidate_not_found"], [], "none")
            self._record(candidate_id, result)
            return result
        reasons: list[str] = []
        evidence: list[str] = []
        confidence = "low"
        priority = 5

        raw = _json(row.get("raw_json"), {})
        if row.get("status") == "excluded":
            return VerificationResult(False, ["already_excluded"], [], "none")

        if raw.get("snapshot_only"):
            reasons.append("Direct source fetch unavailable; snapshot is non-publishable until original article is re-read.")
        if editorial_exclusion_text(
            row.get("original_headline", ""), row.get("url", ""),
            row.get("category", ""), row.get("body_excerpt", ""),
        ):
            reasons.append("Editorial exclusion: crime, graphic violence, suicide/accident, rumor or unsupported allegation.")
            result = VerificationResult(False, reasons, evidence, "none")
            self.db.update_candidate(candidate_id, status="excluded", review_reason="editorial_exclusion")
            self._record(candidate_id, result)
            return result

        pub = parse_iso(row.get("published_at"))
        if not pub:
            reasons.append("No trustworthy publication timestamp.")
        else:
            age_hours = (utcnow() - pub).total_seconds() / 3600
            if age_hours < -2:
                reasons.append("Publication timestamp is implausibly in the future.")
            if age_hours > self.config.max_story_age_hours:
                reasons.append(f"Story is older than {self.config.max_story_age_hours} hours.")
            evidence.append(f"Publication timestamp: {row['published_at']}")
        if len(clean_text(row.get("body_excerpt", ""))) < 80:
            reasons.append("Original report excerpt is too short to establish reported facts.")

        # Cluster similar reports from the four monitored sources.
        cluster_id, related = self._cluster(row)
        self.db.update_candidate(candidate_id, cluster_id=cluster_id)
        if related:
            evidence.append(f"Related reports considered: {len(related)}; syndicated duplicates are not independent corroboration.")

        # Calibrated single-source policy: a substantial, fresh report from a
        # monitored publisher may verify at medium confidence when it carries
        # no allegation/uncertainty language. Allegations, short excerpts and
        # snapshot-only items still require official or independent evidence.
        text = " ".join([row.get("original_headline", ""), row.get("body_excerpt", "")])
        excerpt_len = len(clean_text(row.get("body_excerpt", "")))
        claim_language = bool(re.search(
            r"alleg|claimed|accused|unverified|rumou?r|دعوی|الزام|مبینہ", text, re.I))
        single_source_candidate = (
            excerpt_len >= 280 and not claim_language and not raw.get("snapshot_only")
        )

        # Explicit, source-linked evidence. Primary article links in the raw record
        # are retained in the audit trail and can be official documents.
        links = _json(row.get("external_links_json"), [])
        if isinstance(links, list):
            evidence_urls = [str(x) for x in links]
        else:
            evidence_urls = []
        for value in raw.get("evidence_urls", []) or []:
            evidence_urls.append(str(value))
        for value in extra_evidence_urls or []:
            evidence_urls.append(str(value))
        official_urls = [u for u in dict.fromkeys(evidence_urls) if is_official_url(u, self.config.official_host_suffixes)]
        evidence_urls = list(dict.fromkeys(evidence_urls))

        official_text = ""
        if provided_evidence_text:
            official_text = clean_text(provided_evidence_text, 20000)
            evidence.append("Operator-supplied snapshot of official document used because direct access is blocked/restricted.")
            self.db.insert_api_event("agent_b", "official_evidence", "ok",
                                     "Operator-provided official document snapshot checked; no access restriction bypassed.",
                                     details={"official_urls": official_urls})
        elif official_urls:
            for url in official_urls[:3]:
                text, message, status = self.evidence.fetch_text(url)
                if text:
                    official_text += " " + text
                    evidence.append(f"Official document checked: {url} ({message})")
                    self.db.insert_api_event("agent_b", "official_evidence", "ok", message,
                                             http_status=status, details={"url": url})
                else:
                    reasons.append(f"Official evidence could not be read: {message}")
        if not official_urls and not single_source_candidate:
            reasons.append("No official document or independently verified evidence URL attached.")

        if official_text:
            source_numbers = numeric_atoms(row.get("original_headline", "") + " " + row.get("body_excerpt", ""))
            evidence_numbers = numeric_atoms(official_text)
            shared = source_numbers & evidence_numbers
            if source_numbers and len(shared) < max(1, int(len(source_numbers) * 0.35)):
                reasons.append("Material dates/figures in the report are not sufficiently supported by the official document.")
            else:
                evidence.append(f"Numeric cross-check: {len(shared)} shared date/figure tokens.")
            source_tokens = tokens(row.get("original_headline", "") + " " + row.get("body_excerpt", ""))
            evidence_tokens = tokens(official_text)
            overlap = len(source_tokens & evidence_tokens) / max(1, len(source_tokens))
            if overlap < 0.08:
                reasons.append("Official evidence text does not sufficiently overlap the reported event.")
            else:
                evidence.append(f"Entity/term overlap with official evidence: {overlap:.2f}")

        # Independent reports from different configured sources. Near-identical
        # excerpts are treated as syndication, not independent confirmation.
        independent = 0
        for other in related:
            if other["source"] == row["source"]:
                continue
            body_similarity = similarity(row.get("body_excerpt", ""), other.get("body_excerpt", ""))
            if body_similarity < 0.82 and similarity(row["original_headline"], other["original_headline"]) > 0.55:
                independent += 1
                evidence.append(f"Independent event report considered: {other['source']} ({other['url']})")
        if not official_urls and independent < 1 and not single_source_candidate:
            reasons.append("No official evidence and no demonstrably independent corroboration.")
        if not official_urls and independent < 1 and single_source_candidate:
            evidence.append(
                "Single-source report from a monitored publisher accepted at medium "
                "confidence (substantial excerpt, no allegation language)."
            )

        sensitive = bool(SENSITIVE_PATTERNS.search(text))
        if sensitive and not official_urls and not single_source_candidate:
            reasons.append("Politically/allegation-sensitive story requires official corroboration or human review.")
        if sensitive:
            confidence = "medium"
        else:
            confidence = "high" if official_urls and not reasons else "medium"

        if re.search(r"alleg|claimed|accused|unverified|رumou?r|دعوی|الزام|مبینہ", text, re.I):
            reasons.append("Claim is attributed/uncertain; Urdu and Roman Urdu must preserve that wording.")

        cluster = self.db.get_cluster(cluster_id) if cluster_id else None
        if cluster and cluster.get("status") == "published":
            cluster_update = parse_iso(cluster.get("last_material_update_at"))
            material_update = bool(pub and cluster_update and pub > cluster_update and cluster.get("post_id"))
            if not material_update:
                reasons.append("Event already published; no material update detected.")

        approved = not reasons
        status = "verified" if approved else "held"
        if row.get("status") == "excluded":
            status = "excluded"
        result = VerificationResult(approved, reasons, evidence, confidence if approved else "low", cluster_id, priority)
        self.db.update_candidate(candidate_id, status=status, review_reason=None if approved else "; ".join(reasons[:4]))
        if not approved and status != "excluded":
            self.db.close_open_reviews(candidate_id, "verification_hold")
            self.db.add_review(candidate_id, cluster_id, "verification_hold", {
                "reasons": reasons, "evidence": evidence, "confidence": result.confidence,
                "evidence_urls": evidence_urls, "official_urls": official_urls,
            })
        elif approved:
            self.db.set_cluster_status(cluster_id, "verified")
        self._record(candidate_id, result, evidence_urls)
        return result

    def _cluster(self, row: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        candidates = self.db.recent_candidates(hours=72, limit=400)
        related: list[dict[str, Any]] = []
        best_score = 0.0
        cluster_id = None
        for other in candidates:
            if other["id"] == row["id"]:
                continue
            score = similarity(row["original_headline"], other["original_headline"])
            if score >= 0.74:
                related.append(other)
                if score > best_score and other.get("cluster_id"):
                    best_score, cluster_id = score, other["cluster_id"]
        if not cluster_id:
            cluster_id = self.db.upsert_cluster(row["fingerprint"], row["original_headline"])
        return cluster_id, related

    def _record(self, candidate_id: str, result: VerificationResult, evidence_urls: list[str] | None = None) -> None:
        self.db.insert_api_event(
            "agent_b", "verify_story", "approved" if result.approved else "held",
            "; ".join(result.reasons[:3]) if result.reasons else "verification checks passed",
            details={
                "candidate_id": candidate_id,
                "confidence": result.confidence,
                "evidence": result.evidence,
                "evidence_urls": evidence_urls or [],
            },
        )


def _json(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default
