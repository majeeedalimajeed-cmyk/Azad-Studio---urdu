"""Small, dependency-light helpers shared by the AZAD STUDIO news agents."""
from __future__ import annotations

import hashlib
import html
import json
import re
import unicodedata
from datetime import datetime, timezone
from difflib import SequenceMatcher
from email.utils import parsedate_to_datetime
from typing import Any, Iterable
from urllib.parse import urlsplit, urlunsplit

ARABIC_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹", "0123456789")
PERSIAN_DIGITS = str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789")

STOPWORDS = {
    "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with", "at",
    "by", "from", "as", "is", "are", "was", "were", "be", "been", "this", "that",
    "these", "those", "will", "would", "could", "after", "before", "over", "into",
    "says", "said", "news", "latest", "live", "update", "updates", "india", "indian",
    "کا", "کی", "کے", "میں", "سے", "پر", "اور", "ہے", "ہیں", "کیا", "کو", "نے",
    "یہ", "وہ", "ایک", "کہ", "لیے", "گیا", "گئی", "گئے", "ہو", "ہوا", "ہوئے",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None = None) -> str:
    dt = dt or utcnow()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def parse_rss_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError, IndexError):
        return parse_iso(value)


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower() or "https"
    netloc = parts.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+$", "", parts.path) or "/"
    # Remove known tracking parameters but keep article identifiers.
    tracking = {"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "fbclid", "ref", "rand", "amp"}
    query = "&".join(
        f"{k}={v}" for k, v in sorted(
            (p.split("=", 1) + [""])[:2] for p in parts.query.split("&") if p
        ) if k not in tracking
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def clean_text(value: str, limit: int = 6000) -> str:
    value = html.unescape(value or "")
    value = re.sub(r"<script.*?</script>|<style.*?</style>", " ", value, flags=re.I | re.S)
    value = re.sub(r"<[^>]+>", " ", value)
    value = unicodedata.normalize("NFKC", value)
    value = value.translate(ARABIC_DIGITS).translate(PERSIAN_DIGITS)
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def normalize_headline(value: str) -> str:
    value = clean_text(value).lower()
    value = value.translate(ARABIC_DIGITS).translate(PERSIAN_DIGITS)
    value = re.sub(r"[^\w\u0600-\u06ff\s]", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def tokens(value: str) -> set[str]:
    words = {
        w for w in re.findall(r"[\w\u0600-\u06ff]+", normalize_headline(value))
        if len(w) > 2 and w not in STOPWORDS
    }
    return words


def numbers(value: str) -> set[str]:
    value = clean_text(value).translate(ARABIC_DIGITS).translate(PERSIAN_DIGITS)
    return set(re.findall(r"\d+(?:[.,:/-]\d+)*", value))


def numeric_atoms(value: str) -> set[str]:
    """Return numbers plus normalized components of dates/ranges (06/11 -> 6, 11)."""
    atoms: set[str] = set()
    for token in numbers(value):
        atoms.add(token)
        for part in re.split(r"[.,:/-]", token):
            part = part.lstrip("0") or "0"
            atoms.add(part)
    return atoms


def similarity(a: str, b: str) -> float:
    na, nb = normalize_headline(a), normalize_headline(b)
    if na == nb:
        return 1.0
    seq = SequenceMatcher(None, na, nb).ratio()
    ta, tb = tokens(a), tokens(b)
    jac = len(ta & tb) / max(1, len(ta | tb))
    num_a, num_b = numbers(a), numbers(b)
    number_bonus = 0.08 if num_a and num_a == num_b else 0.0
    return min(1.0, max(seq, jac) + number_bonus)


def story_fingerprint(headline: str, body: str = "") -> str:
    key_tokens = sorted(t for t in tokens(headline + " " + body) if len(t) > 3)[:14]
    key_numbers = sorted(numbers(headline + " " + body))[:10]
    return sha256_text(normalize_headline(headline) + "|" + "|".join(key_tokens + key_numbers))[:32]


def redact(text: str, secrets: Iterable[str]) -> str:
    result = str(text)
    for secret in secrets:
        if secret and len(secret) >= 4:
            result = result.replace(secret, "[REDACTED]")
    result = re.sub(r"(?i)(access_token|api[_-]?key|bearer)\s*[=:]\s*[^\s&]+", r"\1=[REDACTED]", result)
    return result


def domain(url: str) -> str:
    return (urlsplit(url).netloc or "").lower().removeprefix("www.")


def is_official_url(url: str, suffixes: Iterable[str]) -> bool:
    host = domain(url)
    return any(host == s or host.endswith("." + s) for s in suffixes)
