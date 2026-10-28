"""Editorial selection order: Hyderabad/Telangana first, then politics,
then India/world; The Hindu / Indian Express / India Today preferred."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from nr_news.agents.research import ResearchAgent
from nr_news.config import CONFIG
from nr_news.db import Database
from nr_news.models import Candidate, SourceResult
from nr_news.pipeline import NewsPipeline
from nr_news.sources import SourceMonitor
from nr_news.util import iso


def _cfg(tmp_path: Path):
    base = tmp_path / "data"
    cfg = replace(CONFIG, db_path=base / "nr.sqlite3", data_dir=base,
                  card_dir=base / "cards", audit_dir=base / "audit",
                  media_dir=base / "media", snapshot_dir=base / "snapshots",
                  dry_run=True, pixabay_enabled=False, llm_api_key=None,
                  llm_model=None, translator_model=None)
    cfg.ensure_dirs()
    return cfg


def _cand(**kw) -> Candidate:
    values = dict(source="test", publisher="Test Publisher",
                  url="https://example.org/story", original_headline="News",
                  published_at=iso(), updated_at=None, language="en",
                  body_excerpt="", category="", external_links=[], raw={})
    values.update(kw)
    return Candidate(**values)


def test_editorial_order_hyderabad_then_politics_then_world():
    regional = _cand(url="https://example.org/hyderabad-water-board",
                     original_headline="Hyderabad water board announces supply schedule",
                     body_excerpt="HMWSSB Hyderabad water supply notice.",
                     category="hyderabad")
    regional_politics = _cand(url="https://example.org/telangana-cabinet",
                              original_headline="Telangana cabinet approves new policy",
                              body_excerpt="Telangana government cabinet decision.",
                              category="telangana")
    politics = _cand(url="https://example.org/parliament-bill",
                     original_headline="Parliament passes new bill after debate",
                     body_excerpt="Lok Sabha government bill voting.",
                     category="politics")
    world = _cand(url="https://example.org/un-summit",
                  original_headline="World leaders meet at global summit",
                  body_excerpt="United Nations international diplomacy summit.",
                  category="world")
    sports = _cand(url="https://example.org/cricket",
                   original_headline="Cricket team wins series final",
                   body_excerpt="A thrilling series decider.", category="sports")
    assert ResearchAgent.priority(regional_politics) >= ResearchAgent.priority(regional)
    assert ResearchAgent.priority(regional) > ResearchAgent.priority(politics)
    assert ResearchAgent.priority(politics) > ResearchAgent.priority(world)
    assert ResearchAgent.priority(world) > ResearchAgent.priority(sports)


def test_preferred_publishers_get_tie_break_boost():
    hindu = _cand(publisher="The Hindu", category="india",
                  original_headline="Parliament committee releases report",
                  body_excerpt="Parliament government committee report.")
    other = _cand(publisher="Some Blog", category="india",
                  original_headline="Parliament committee releases report",
                  body_excerpt="Parliament government committee report.")
    assert ResearchAgent.priority(hindu) > ResearchAgent.priority(other)


class _Resp:
    def __init__(self, text, ok=True, status=200):
        self.text, self.ok, self.status_code = text, ok, status
        self.message = "ok" if ok else "unavailable"


RSS = """<?xml version="1.0"?><rss><channel>
<item><title>Telangana cabinet approves irrigation policy</title>
<link>https://www.thehindu.com/news/national/Telangana/story-1</link>
<description>Telangana government cabinet policy decision.</description></item>
<item><title>Hyderabad metro expansion announced</title>
<link>https://www.thehindu.com/news/cities/Hyderabad/story-2</link>
<description>Hyderabad metro transport infrastructure.</description></item>
</channel></rss>"""


def test_multi_feed_sections_label_candidates(tmp_path):
    cfg = _cfg(tmp_path)
    monitor = SourceMonitor(Database(cfg.db_path), config=cfg)
    monitor.client = type("C", (), {"get": lambda self, url, params=None: _Resp(RSS)})()
    result = monitor._hindu(10)
    # The same two stories in every section feed are deduped by URL.
    assert result.ok and result.item_count == 2
    sections = {c.category.split(",")[0] for c in result.candidates}
    assert sections <= {"telangana", "hyderabad", "india", "politics", "world"}
    assert all(c.publisher == "The Hindu" for c in result.candidates)
    assert all(ResearchAgent.priority(c) >= 13 for c in result.candidates)


def test_publish_queue_prefers_regional_over_old_backlog(tmp_path):
    cfg = _cfg(tmp_path)
    db = Database(cfg.db_path)
    pipeline = NewsPipeline(cfg, db)
    published_order: list[str] = []

    def add(headline: str, category: str, publisher: str, url: str) -> str:
        candidate = _cand(publisher=publisher, url=url, original_headline=headline,
                          category=category,
                          body_excerpt=headline)
        cid, _ = db.insert_candidate(pipeline.research._to_row(candidate, "test"))
        content = {"urdu_headline": "سرخی", "urdu_bullets": ["ایک", "دو", "تین"],
                   "roman_headline": headline, "roman_bullets": ["a", "b", "c"],
                   "verification_notes": [], "source_urls": [], "publication_time": iso(),
                   "media": None}
        eid, _ = db.insert_editorial(cid, None, content, status="approved")
        card_path = tmp_path / f"{eid}.png"
        card_path.write_bytes(b"png")
        db.insert_card(eid, str(card_path), "abc", 1080, 1350, 1, 1, "passed")
        return eid

    # Oldest first in the table: a generic world story, then regional politics.
    add("World leaders meet at global summit", "world", "The Wire",
        "https://example.org/world-first-created")
    regional_id = add("Telangana cabinet approves education policy", "telangana",
                      "The Hindu", "https://example.org/telangana-second-created")

    class _Pub:
        def publish(self, eid, card, dry_run=True):
            published_order.append(eid)
            return {"status": "success", "editorial_id": eid}

    pipeline.publisher = _Pub()
    results = pipeline.publish_approved_pending(limit=5)
    assert len(results) == 2
    assert published_order[0] == regional_id
