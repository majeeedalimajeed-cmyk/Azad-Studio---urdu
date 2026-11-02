"""Agent A (research/extraction) — source-specific public monitors.

Only public feeds/pages are used. Article bodies are truncated to short factual
excerpts; no complete article is stored or republished.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from datetime import datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from .config import CONFIG
from .db import Database
from .http_client import PoliteHTTPClient
from .models import Candidate, SourceResult
from .util import canonical_url, clean_text, iso, parse_rss_date, redact, sha256_text

NS = {
    "content": "http://purl.org/rss/1.0/modules/content/",
    "media": "http://search.yahoo.com/mrss/",
    "dc": "http://purl.org/dc/elements/1.1/",
}


class _LinkParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for k, v in attrs:
                if k == "href" and v:
                    self.links.append(v)


def _external_links(html_text: str) -> list[str]:
    p = _LinkParser()
    try:
        p.feed(html_text or "")
    except Exception:
        return []
    return list(dict.fromkeys(p.links))[:30]


def _rss_items(xml_text: str, limit: int) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    for node in root.findall(".//item")[:limit]:
        def txt(path: str, namespaces: dict[str, str] | None = None) -> str:
            el = node.find(path, namespaces or {})
            return clean_text("".join(el.itertext()) if el is not None else "", 4000)
        link = txt("link").strip()
        guid = txt("guid").strip()
        description = txt("description")
        encoded = txt("content:encoded", NS)
        media = node.find("media:content", NS)
        enclosure = node.find("enclosure")
        image_url = None
        if media is not None and media.attrib.get("url"):
            image_url = media.attrib["url"]
        elif enclosure is not None and enclosure.attrib.get("url"):
            image_url = enclosure.attrib.get("url")
        categories = [clean_text("".join(e.itertext())) for e in node.findall("category")]
        pub_date = parse_rss_date(txt("pubDate"))
        items.append({
            "title": txt("title"),
            "link": link,
            "guid": guid,
            "pub_date": iso(pub_date) if pub_date else None,
            "description": description[:1000],
            "encoded": encoded[:2500],
            "image_url": image_url,
            "categories": categories,
            "links": _external_links(description + " " + encoded),
        })
    return items


def _rss_candidates(items: list[dict[str, Any]], source: str, publisher: str, language: str,
                    section: str = "") -> list[Candidate]:
    result: list[Candidate] = []
    for item in items:
        url = item["link"] or item["guid"]
        if not url or not item["title"]:
            continue
        body = clean_text(item["encoded"] or item["description"], 1800)
        # The desk section is kept as the first category: it is what the
        # editorial priority ranking reads (telangana/hyderabad/politics/…).
        category = ", ".join(([section] if section else []) + item["categories"][:4])
        result.append(Candidate(
            source=source,
            publisher=publisher,
            url=url,
            original_headline=clean_text(item["title"], 500),
            published_at=item["pub_date"],
            updated_at=None,
            language=language,
            body_excerpt=body,
            category=category,
            image_url=item["image_url"],
            image_credit=None,
            image_license=None,
            external_links=item["links"],
            raw={"feed_item": {
                "guid": item["guid"], "categories": item["categories"],
                "description": item["description"][:500],
            }},
        ))
    return result


class SourceMonitor:
    def __init__(self, db: Database | None = None, client: PoliteHTTPClient | None = None, config=CONFIG):
        self.db = db or Database(config.db_path)
        self.client = client or PoliteHTTPClient(config)
        self.config = config

    def monitor_all(self, mode: str = "hourly") -> list[SourceResult]:
        limit = {"breaking": 12, "hourly": 25, "morning": 60}.get(mode, 25)
        results = [
            self._hindu(limit),
            self._indian_express(limit),
            self._the_wire(limit),
        ]
        for result in results:
            self.db.record_source_status(
                result.source, result.ok, result.item_count, result.message, result.http_status
            )
            self.db.insert_api_event(
                "source_monitor", result.source, "ok" if result.ok else "error",
                redact(result.message, self.config.secret_values),
                http_status=result.http_status,
                details={"item_count": result.item_count, "mode": mode},
            )
        return results

    def _munsif(self, limit: int) -> SourceResult:
        r = self.client.get(self.config.munsif_rss)
        if not r.ok:
            return SourceResult("munsif_daily", False, message=r.message, http_status=r.status_code)
        try:
            items = _rss_items(r.text, limit)
            candidates = _rss_candidates(items, "munsif_daily", "Munsif Daily", "en")
            return SourceResult("munsif_daily", True, len(candidates), "RSS fetched", r.status_code, candidates)
        except ET.ParseError as exc:
            return SourceResult("munsif_daily", False, message=f"RSS parse error: {exc}", http_status=r.status_code)

    def _multi_feed(self, source: str, publisher: str,
                    feeds: tuple[tuple[str, str], ...], per_feed_limit: int) -> SourceResult:
        """Aggregate a publisher's section RSS feeds into one source result.

        Every candidate keeps its desk section as its first category so the
        editorial ranking can put Hyderabad/Telangana and politics first.
        A feed that fails (e.g. an intermittent 403) never sinks the source:
        the source is healthy if at least one section answered."""
        candidates: list[Candidate] = []
        seen: set[str] = set()
        ok_feeds = 0
        last_status = None
        failures: list[str] = []
        for section, url in feeds:
            r = self.client.get(url)
            last_status = r.status_code or last_status
            if not r.ok:
                failures.append(section)
                continue
            try:
                items = _rss_items(r.text, per_feed_limit)
            except ET.ParseError:
                failures.append(section)
                continue
            ok_feeds += 1
            for candidate in _rss_candidates(items, source, publisher, "en", section=section):
                key = canonical_url(candidate.url)
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(candidate)
        message = f"{ok_feeds}/{len(feeds)} section feeds fetched"
        if failures:
            message += f"; unavailable: {', '.join(failures)}"
        return SourceResult(source, ok_feeds > 0, len(candidates), message, last_status, candidates)

    def _hindu(self, limit: int) -> SourceResult:
        return self._multi_feed("the_hindu", "The Hindu", self.config.hindu_feeds,
                                max(6, limit // 2))

    def _india_today(self, limit: int) -> SourceResult:
        return self._multi_feed("india_today", "India Today", self.config.india_today_feeds,
                                max(6, limit // 2))

    def _indian_express(self, limit: int) -> SourceResult:
        result = self._multi_feed("indian_express", "The Indian Express",
                                  self.config.indian_express_feeds, max(6, limit // 2))
        if result.candidates:
            return result
        # Fall back to the clearly-marked monitoring snapshot, as before.
        snapshot_path = self.config.snapshot_dir / "indian_express_feed.xml"
        if not snapshot_path.exists():
            return result
        try:
            items = _rss_items(snapshot_path.read_text(encoding="utf-8"), limit)
        except ET.ParseError as exc:
            return SourceResult("indian_express", False, message=f"RSS parse error: {exc}")
        candidates = _rss_candidates(items, "indian_express", "The Indian Express", "en")
        for c in candidates:
            c.raw["snapshot_only"] = True
            c.raw["reason"] = "direct feed inaccessible from host; original article must be re-read before publication"
        # An outage is still reported to operations even if a snapshot is parsed.
        return SourceResult("indian_express", False, len(candidates),
                            f"{result.message}; using clearly marked non-publishable snapshot for monitoring",
                            None, candidates)

    def _the_wire(self, limit: int) -> SourceResult:
        params = {
            "per_page": str(limit),
            "_fields": "id,date,date_gmt,modified,modified_gmt,link,title,excerpt",
        }
        r = self.client.get(self.config.wire_api, params=params)
        if not r.ok:
            return SourceResult("the_wire", False, message=r.message, http_status=r.status_code)
        try:
            import json as _json
            payload = _json.loads(r.text)
            candidates: list[Candidate] = []
            for item in payload:
                title = BeautifulSoup(item.get("title", {}).get("rendered", ""), "html.parser").get_text(" ", strip=True)
                excerpt_html = item.get("excerpt", {}).get("rendered", "")
                excerpt = BeautifulSoup(excerpt_html, "html.parser").get_text(" ", strip=True)
                link = item.get("link") or item.get("guid", {}).get("rendered")
                if not title or not link:
                    continue
                published = item.get("date_gmt") or item.get("date")
                modified = item.get("modified_gmt") or item.get("modified")
                candidates.append(Candidate(
                    source="the_wire",
                    publisher="The Wire",
                    url=link,
                    original_headline=clean_text(title, 500),
                    published_at=(published + "Z") if published and not published.endswith("Z") and "T" in published else published,
                    updated_at=(modified + "Z") if modified and not modified.endswith("Z") and "T" in modified else modified,
                    language="en",
                    body_excerpt=clean_text(excerpt, 1800),
                    category="",
                    external_links=_external_links(excerpt_html),
                    raw={"wp_id": item.get("id")},
                ))
            return SourceResult("the_wire", True, len(candidates), "public WordPress REST API fetched", r.status_code, candidates)
        except (ValueError, TypeError) as exc:
            return SourceResult("the_wire", False, message=f"API parse error: {type(exc).__name__}", http_status=r.status_code)

    def _etemaad_article(self, url: str, headline: str, *, html_text: str | None = None,
                         snapshot: bool = False) -> Candidate:
        if html_text is None:
            r = self.client.get(url)
            if not r.ok:
                raise RuntimeError(f"article fetch failed: {r.message}")
            html_text = r.text
            article_status = r.status_code
        else:
            article_status = 200
        soup = BeautifulSoup(html_text, "html.parser") if html_text else BeautifulSoup("", "html.parser")
        title_el = soup.find("h1")
        title = clean_text(title_el.get_text(" ", strip=True), 500) if title_el else headline
        date_el = soup.select_one("div.date")
        published_at = None
        if date_el:
            raw_date = clean_text(date_el.get_text(" ", strip=True), 100)
            for fmt in ("%a %d %b %Y, %H:%M:%S", "%d %b %Y, %H:%M:%S"):
                try:
                    dt = datetime.strptime(raw_date, fmt).replace(tzinfo=ZoneInfo("Asia/Kolkata"))
                    published_at = iso(dt)
                    break
                except ValueError:
                    continue
        og_desc = soup.find("meta", attrs={"property": "og:description"})
        body = clean_text(og_desc.get("content", ""), 1800) if og_desc else ""
        if not body:
            body_el = soup.find("div", style=re.compile("text-align", re.I))
            body = clean_text(body_el.get_text(" ", strip=True), 1800) if body_el else ""
        og_image = soup.find("meta", attrs={"property": "og:image"})
        image_url = og_image.get("content") if og_image else None
        links = [a.get("href") for a in soup.find_all("a", href=True)]
        return Candidate(
            source="etemaad_daily",
            publisher="Etemaad Daily",
            url=url,
            original_headline=title,
            published_at=published_at,
            updated_at=None,
            language="ur",
            body_excerpt=body,
            category=urlsplit_category(url),
            image_url=image_url,
            image_credit=None,
            image_license=None,
            external_links=list(dict.fromkeys(urljoin(url, x) for x in links))[:40],
            raw={"article_page_status": article_status, "snapshot_only": snapshot},
        )

    def _etemaad_urdu(self, limit: int) -> SourceResult:
        # index.php is the Urdu edition; / redirects to the English edition.
        # Both canonical hosts are tried because the publisher's DNS is
        # occasionally intermittent from some networks.
        home_urls = list(dict.fromkeys([
            self.config.etemaad_urdu_home,
            self.config.etemaad_urdu_home.replace("://www.", "://"),
        ]))
        home_html = None
        home_status = None
        used_home = home_urls[0]
        fallback_used = False
        snapshot_home = False
        snapshot_any = False
        live_error = "No Etemaad host responded"
        for index, home_url in enumerate(home_urls):
            candidate_response = self.client.get(home_url)
            if candidate_response.ok:
                home_html = candidate_response.text
                home_status = candidate_response.status_code
                used_home = home_url
                fallback_used = index > 0
                break
            live_error = candidate_response.message
        if not home_html:
            snapshot_path = self.config.snapshot_dir / "etemaad_urdu_home.html"
            if snapshot_path.exists():
                home_html = snapshot_path.read_text(encoding="utf-8")
                snapshot_home = True
                snapshot_any = True
                live_error = f"{live_error}; marked non-publishable snapshots used for monitoring"
            else:
                return SourceResult("etemaad_daily", False, message=live_error,
                                    http_status=home_status)
        soup = BeautifulSoup(home_html, "html.parser")
        pairs: list[tuple[str, str]] = []
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not re.search(r":\d+$", href):
                continue
            text = clean_text(a.get_text(" ", strip=True), 500)
            if len(text) < 15:
                continue
            url = canonical_url(urljoin(used_home, href))
            pairs.append((url, text))
        pairs = list(dict.fromkeys(pairs))[:limit]
        candidates: list[Candidate] = []
        for url, headline in pairs:
            known = self.db.one("SELECT id FROM candidates WHERE canonical_url=?", (url,))
            if known:
                continue
            article_id = url.rsplit(":", 1)[-1]
            snapshot_path = self.config.snapshot_dir / f"etemaad_{article_id}.html"
            try:
                candidates.append(self._etemaad_article(url, headline))
            except Exception as exc:
                if not snapshot_path.exists():
                    self.db.insert_api_event("source_monitor", "etemaad_article", "error",
                                             f"article extraction failed: {type(exc).__name__}", details={"url": url})
                    continue
                candidate = self._etemaad_article(url, headline,
                                                  html_text=snapshot_path.read_text(encoding="utf-8"),
                                                  snapshot=True)
                candidate.raw["snapshot_only"] = True
                candidates.append(candidate)
                snapshot_any = True
        if snapshot_any:
            for candidate in candidates:
                candidate.raw["snapshot_only"] = True
            message = live_error
            ok = False
        else:
            message = "Urdu home page and new article pages fetched"
            if fallback_used:
                message += " via DNS fallback host"
            ok = True
        return SourceResult("etemaad_daily", ok, len(candidates), message,
                            home_status, candidates)


def urlsplit_category(url: str) -> str:
    match = re.search(r"https?://[^/]+/([^/]+)/", url)
    return match.group(1).replace("-", " ") if match else ""
