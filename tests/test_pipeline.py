"""Offline launch tests. These never contact Facebook or publish publicly."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from nr_news.agents.publisher import PublishingAgent
from nr_news.agents.translator import TranslationValidator
from nr_news.agents.verification import VerificationAgent
from nr_news.config import CONFIG, ROOT
from nr_news.db import Database
from nr_news.facebook import GraphAPIError
from nr_news.models import Candidate, SourceResult
from nr_news.pipeline import NewsPipeline
from nr_news.renderer import verify_logo
from nr_news.service import PersistentScheduler
from nr_news.util import iso, utcnow

FIXTURE = ROOT / "tests/fixtures/verified_tgpsc_story.json"


def temp_config(tmp_path: Path, **overrides):
    base = tmp_path / "data"
    values = {
        "db_path": base / "nr_news.sqlite3",
        "data_dir": base,
        "card_dir": base / "cards",
        "audit_dir": base / "audit",
        "media_dir": base / "media",
        "snapshot_dir": base / "snapshots",
        "dry_run": True,
        "publish_enabled": False,
        "operator_approved": False,
        "verify_approved": False,
        "llm_api_key": None,
        "llm_model": None,
        "translator_model": None,
        "facebook_page_token": None,
        "facebook_user_token": None,
        "expected_page_name": None,
    }
    values.update(overrides)
    cfg = replace(CONFIG, **values)
    cfg.ensure_dirs()
    return cfg


def sample_candidate(**kwargs) -> Candidate:
    values = {
        "source": "test_source",
        "publisher": "Test Publisher",
        "url": "https://example.org/policy-story-1",
        "original_headline": "Telangana government announces public education policy",
        "published_at": iso(),
        "updated_at": None,
        "language": "en",
        "body_excerpt": "The Telangana government announced a public education policy in Hyderabad on 27 October 2026, according to an official statement.",
        "category": "government",
        "external_links": [],
        "raw": {},
    }
    values.update(kwargs)
    return Candidate(**values)


def test_exact_logo_and_fonts():
    assert verify_logo() == CONFIG.logo_sha256
    assert CONFIG.urdu_font_path.exists()
    assert CONFIG.latin_font_path.exists()


def test_fixture_renders_valid_bilingual_card(tmp_path):
    cfg = temp_config(tmp_path)
    pipeline = NewsPipeline(cfg)
    result = pipeline.run_fixture(FIXTURE, dry_run=True)
    assert result["status"] == "rendered"
    assert result["translation_validation"]["ok"] is True
    assert result["publish"][0]["status"] == "dry_run"
    assert result["publish"][0]["post_id"] is None
    card = Path(result["cards"][0])
    assert card.exists()
    with Image.open(card) as im:
        assert im.size == (1080, 1350)
    audits = []
    for path in cfg.audit_dir.glob("*.json"):
        value = json.loads(path.read_text(encoding="utf-8"))
        if "visual_inspection" in value:
            audits.append(value)
    assert audits
    data = audits[0]
    assert data["logo_sha256"] == CONFIG.logo_sha256
    assert data["visual_inspection"]["passed"] is True
    assert data["heading"] == "LATEST NEWS"


def test_duplicate_fixture_does_not_create_second_post(tmp_path):
    cfg = temp_config(tmp_path)
    pipeline = NewsPipeline(cfg)
    first = pipeline.run_fixture(FIXTURE, dry_run=True)
    second = pipeline.run_fixture(FIXTURE, dry_run=True)
    assert first["status"] == "rendered"
    assert second["status"] in {"duplicate_fixture", "duplicate_editorial"}
    assert len(pipeline.db.publish_attempts()) == 1
    assert len(pipeline.db.cards()) == 1


def test_crime_story_is_excluded(tmp_path):
    cfg = temp_config(tmp_path)
    db = Database(cfg.db_path)
    pipeline = NewsPipeline(cfg, db)
    candidate = sample_candidate(
        url="https://example.org/murder-story",
        original_headline="Police investigate murder in Hyderabad",
        body_excerpt="A murder report and graphic violence details.",
    )
    row = pipeline.research._to_row(candidate, "test")
    cid, created = db.insert_candidate(row)
    assert created
    result = VerificationAgent(db, config=cfg).verify(cid)
    assert result.approved is False
    assert any("exclusion" in x for x in result.reasons)
    assert db.get_candidate(cid)["status"] == "excluded"


def test_translation_validator_catches_changed_number_and_name(tmp_path):
    cfg = temp_config(tmp_path)
    validator = TranslationValidator(config=cfg)
    urdu = {
        "urdu_headline": "7 نومبر کا اعلان",
        "urdu_bullets": ["ٹی جی پی ایس سی نے 6 نومبر کی تاریخ دی۔", "30 اکتوبر تک درخواست۔", "5 بجے آخری وقت۔"],
    }
    roman = {
        "roman_headline": "7 November ka elan",
        "roman_bullets": ["TGPSC ne 6 November ki date di.", "31 October tak darkhwast.", "5 baje aakhri waqt."],
        "entity_map": [{"urdu": "ٹی جی پی ایس سی", "roman": "TGPSC"}],
        "uncertainty_map": [],
    }
    result = validator.validate(urdu, roman, evidence_text="6 and 7 November; 30 October; 5 PM")
    assert result["ok"] is False
    assert any("Dates/figures differ" in x for x in result["errors"])


def test_uncertainty_wording_must_be_preserved(tmp_path):
    cfg = temp_config(tmp_path)
    validator = TranslationValidator(config=cfg)
    urdu = {
        "urdu_headline": "تجویز زیر غور",
        "urdu_bullets": ["یہ تجویز جہاں ممکن ہو لاگو ہوگی۔", "دعویٰ کی جانچ ہوگی۔", "فی الحال کوئی حتمی فیصلہ نہیں۔"],
    }
    roman = {
        "roman_headline": "Tajveez zair-e-ghaur",
        "roman_bullets": ["Yeh proposal har jagah laagu hoga.", "Claim ki jaanch hogi.", "Abhi koi final decision nahi."],
        "entity_map": [],
        "uncertainty_map": [],
    }
    result = validator.validate(urdu, roman)
    assert result["ok"] is False
    assert any("Uncertainty wording" in x for x in result["errors"])


def test_all_four_sources_monitored_and_empty_scan_creates_no_jobs(tmp_path):
    cfg = temp_config(tmp_path)
    pipeline = NewsPipeline(cfg)
    results = [
        SourceResult("munsif_daily", True, 0, "test empty"),
        SourceResult("etemaad_daily", True, 0, "test empty"),
        SourceResult("indian_express", True, 0, "test empty"),
        SourceResult("the_wire", True, 0, "test empty"),
    ]
    result = pipeline.scan("hourly", source_results=results)
    assert len(result["sources"]) == 4
    assert result["new_candidates"] == []
    assert pipeline.drain_jobs(10) == []
    assert len(pipeline.db.publish_attempts()) == 0
    assert len(pipeline.db.cards()) == 0


def test_source_outage_is_recorded_and_other_sources_continue(tmp_path):
    cfg = temp_config(tmp_path)
    pipeline = NewsPipeline(cfg)
    results = [
        SourceResult("munsif_daily", True, 2, "RSS fetched"),
        SourceResult("etemaad_daily", False, 0, "HTTP 503", 503),
        SourceResult("indian_express", True, 1, "RSS fetched"),
        SourceResult("the_wire", True, 3, "API fetched"),
    ]
    pipeline.scan("hourly", source_results=results)
    statuses = {s["source"]: s for s in pipeline.db.source_statuses()}
    assert statuses["etemaad_daily"]["status"] == "error"
    assert statuses["munsif_daily"]["status"] == "ok"
    assert statuses["the_wire"]["status"] == "ok"


def test_breaking_news_verification_starts_immediately(tmp_path):
    cfg = temp_config(tmp_path)
    pipeline = NewsPipeline(cfg)
    candidate = sample_candidate(
        url="https://example.org/breaking-election-decision",
        original_headline="Election Commission announces Telangana government decision",
        published_at=iso(),
    )
    result = pipeline.scan("breaking", source_results=[SourceResult("test_source", True, 1, "test", candidates=[candidate])])
    assert len(result["new_candidates"]) == 1
    job = pipeline.db.one("SELECT * FROM jobs WHERE type='verify_story'")
    payload = json.loads(job["payload_json"])
    assert payload["breaking"] is True
    assert job["status"] == "queued"


def test_durable_scheduler_queues_morning_comprehensive_scan(tmp_path):
    cfg = temp_config(tmp_path, morning_hour_ist=0)
    db = Database(cfg.db_path)
    scheduler = PersistentScheduler(db, cfg)
    scheduler.tick()
    jobs = db.query("SELECT * FROM jobs WHERE type='scan'")
    assert any(json.loads(j["payload_json"]).get("mode") == "morning" for j in jobs)


def test_facebook_publishing_blocked_without_api_confirmed_page(tmp_path):
    cfg = temp_config(tmp_path, dry_run=False, publish_enabled=True, operator_approved=True,
                      verify_approved=True, facebook_page_token="fake-token", expected_page_name="AZAD STUDIO")
    db = Database(cfg.db_path)
    pipeline = NewsPipeline(cfg, db)
    candidate = sample_candidate()
    cid, _ = db.insert_candidate(pipeline.research._to_row(candidate, "test"))
    content = {
        "urdu_headline": "سرخی", "urdu_bullets": ["ایک", "دو", "تین"],
        "roman_headline": "Headline", "roman_bullets": ["one", "two", "three"],
        "verification_notes": [], "source_urls": [], "publication_time": iso(), "media": None,
    }
    eid, _ = db.insert_editorial(cid, None, content, status="approved")
    card_id = db.insert_card(eid, str(tmp_path / "card.png"), "abc", 1080, 1350, 1, 1, "passed")
    card = {"id": card_id, "file_path": str(tmp_path / "card.png"), "sha256": "abc", "visual_status": "passed"}
    result = pipeline.publisher.publish(eid, card, dry_run=False)
    assert result["status"] == "held"
    assert any(a["status"] == "held" for a in db.publish_attempts())


def test_graph_api_error_is_recorded_without_false_success(tmp_path):
    cfg = temp_config(tmp_path, dry_run=False, publish_enabled=True, operator_approved=True,
                      verify_approved=True, facebook_page_token="fake-token", expected_page_name="AZAD STUDIO")
    db = Database(cfg.db_path)
    pipeline = NewsPipeline(cfg, db)
    db.set_setting("facebook_identity_verified_at", iso())
    db.set_setting("facebook_page_id", "123456789")
    db.set_setting("facebook_test_post_passed", "true")
    candidate = sample_candidate(url="https://example.org/error-story")
    cid, _ = db.insert_candidate(pipeline.research._to_row(candidate, "test"))
    content = {
        "urdu_headline": "سرخی", "urdu_bullets": ["ایک", "دو", "تین"],
        "roman_headline": "Headline", "roman_bullets": ["one", "two", "three"],
        "verification_notes": [], "source_urls": [], "publication_time": iso(), "media": None,
    }
    eid, _ = db.insert_editorial(cid, None, content, status="approved")
    card_id = db.insert_card(eid, str(tmp_path / "card.png"), "abc", 1080, 1350, 1, 1, "passed")
    card = {"id": card_id, "file_path": str(tmp_path / "card.png"), "sha256": "abc", "visual_status": "passed"}

    class FakeGraph:
        def confirmed_page_id(self):
            return "123456789"

        def publish_photo(self, *args, **kwargs):
            raise GraphAPIError("Permission error", status=403, code="200", transient=False)

        def find_recent_caption(self, *args, **kwargs):
            return None

    pipeline.publisher.graph = FakeGraph()
    result = pipeline.publisher.publish(eid, card, dry_run=False)
    assert result["status"] == "failed"
    attempts = db.publish_attempts()
    assert attempts[0]["status"] == "failed"
    assert attempts[0]["post_id"] is None
    assert any(e["subsystem"] == "facebook" and e["status"] == "error" for e in db.api_events())


def test_publisher_name_not_allowed_on_card(tmp_path):
    cfg = temp_config(tmp_path)
    validator = TranslationValidator(config=cfg)
    urdu = {"urdu_headline": "خبر", "urdu_bullets": ["ایک", "دو", "تین"]}
    roman = {"roman_headline": "The Wire report", "roman_bullets": ["one", "two", "three"],
             "entity_map": [], "uncertainty_map": []}
    result = validator.validate(urdu, roman)
    assert result["ok"] is False
    assert any("Publisher name" in x for x in result["errors"])
