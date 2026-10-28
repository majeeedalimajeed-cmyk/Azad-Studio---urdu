"""Offline launch tests. These never contact Facebook or publish publicly."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image

from nr_news.agents.media import MediaAgent
from nr_news.agents.publisher import PublishingAgent
from nr_news.agents.research import ResearchAgent
from nr_news.agents.translator import TranslationValidator
from nr_news.agents.verification import VerificationAgent, editorial_exclusion_text
from nr_news.config import CONFIG, ROOT
from nr_news.db import Database
from nr_news.facebook import FacebookGraphClient, GraphAPIError
from nr_news.models import Candidate, EditorialContent, SourceResult
from nr_news.pipeline import NewsPipeline
from nr_news.pixabay import PixabayClient
from nr_news.renderer import BYLINE, CardRenderer, verify_logo
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
        "publish_safety_switch": False,
        "operator_approved": False,
        "verify_approved": False,
        "auto_post_enabled": False,
        "llm_api_key": None,
        "llm_model": None,
        "translator_model": None,
        "facebook_page_token": None,
        "facebook_user_token": None,
        "expected_page_name": None,
        "pixabay_api_key": None,
        "pixabay_enabled": False,
        # The verified fixture is a fixed historical snapshot; its tests must
        # not expire when wall-clock time passes the production 36h window.
        "max_story_age_hours": 24 * 365 * 10,
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


def test_fixture_refreshes_short_source_scan_candidate(tmp_path):
    cfg = temp_config(tmp_path)
    db = Database(cfg.db_path)
    pipeline = NewsPipeline(cfg, db)
    short = sample_candidate(
        url="https://www.etemaaddaily.com/education-and-jobs-news/tgpsc-reschedules-aee-civil-exam-to-november-7-for-ssc-je-candidates:344",
        original_headline="TGPSC AEE exam date change",
        body_excerpt="Short source-scan excerpt.",
        external_links=[],
    )
    cid, created = db.insert_candidate(pipeline.research._to_row(short, "hourly"))
    assert created
    result = pipeline.run_fixture(FIXTURE, dry_run=True)
    assert result["status"] == "rendered"
    refreshed = db.get_candidate(cid)
    assert refreshed and len(refreshed["body_excerpt"]) > 80
    assert "TGPSC" in refreshed["body_excerpt"]


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


def test_expanded_crime_filter_and_priority_ranking(tmp_path):
    assert editorial_exclusion_text("Police arrest accused in fraud case")
    assert editorial_exclusion_text("Hyderabad court rejects bail in cheating case")
    assert not editorial_exclusion_text("Telangana cabinet approves education policy")

    regional = sample_candidate(
        url="https://example.org/hyderabad-election-policy",
        original_headline="Hyderabad election cabinet approves government policy",
        body_excerpt="Telangana government announced an election policy decision in Hyderabad.",
    )
    national = sample_candidate(
        url="https://example.org/parliament-election-policy",
        original_headline="Parliament election committee approves government policy",
        body_excerpt="The central government announced an election policy decision.",
    )
    world = sample_candidate(
        url="https://example.org/global-summit-election-policy",
        original_headline="Global summit election committee approves government policy",
        body_excerpt="An international election policy decision was announced at a world summit.",
    )
    development = sample_candidate(
        url="https://example.org/education-exam-recruitment",
        original_headline="Education department announces exam recruitment",
        body_excerpt="A public education exam and recruitment schedule was released.",
    )
    assert ResearchAgent.priority(regional) > ResearchAgent.priority(national)
    assert ResearchAgent.priority(national) > ResearchAgent.priority(world)
    assert ResearchAgent.priority(world) > ResearchAgent.priority(development)


def test_crime_candidate_is_excluded_before_verification_job(tmp_path):
    cfg = temp_config(tmp_path)
    db = Database(cfg.db_path)
    pipeline = NewsPipeline(cfg, db)
    candidate = sample_candidate(
        url="https://example.org/police-arrest-fraud",
        original_headline="Police arrest accused in fraud case",
        body_excerpt="Police arrested an accused person in a fraud case after a complaint.",
    )
    result = pipeline.scan("hourly", source_results=[
        SourceResult("test_source", True, 1, "test", candidates=[candidate])
    ])
    assert result["jobs_queued"] == 0
    rows = db.query("SELECT status, review_reason FROM candidates")
    assert rows and rows[0]["status"] == "excluded"
    assert rows[0]["review_reason"] == "editorial_exclusion"


def test_high_priority_job_is_claimed_first(tmp_path):
    cfg = temp_config(tmp_path)
    db = Database(cfg.db_path)
    db.enqueue_job("verify_story", {"candidate_id": "low"}, dedupe_key="low", priority=1)
    high = db.enqueue_job("verify_story", {"candidate_id": "high"}, dedupe_key="high", priority=12)
    claimed = db.claim_job()
    assert claimed and claimed["id"] == high


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
    cfg = temp_config(tmp_path, dry_run=False, publish_enabled=True, publish_safety_switch=True,
                      operator_approved=True, verify_approved=True, auto_post_enabled=True,
                      facebook_page_token="fake-token", expected_page_name="AZAD STUDIO")
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


def test_page_token_fallback_after_user_token_expiry(tmp_path):
    cfg = temp_config(tmp_path, expected_page_name="Azad Studio", facebook_page_id="123456789",
                      facebook_user_token="expired-user", facebook_page_token="valid-page")
    db = Database(cfg.db_path)
    db.set_setting("facebook_identity_verified_at", iso())
    db.set_setting("facebook_page_id", "123456789")
    db.set_setting("facebook_page_name", "Azad Studio ")

    class FakeGraph(FacebookGraphClient):
        def _call(self, method, path, *, token, params=None, data=None, files=None):
            if path in {"/me/accounts", "/me/permissions"} and token == "expired-user":
                raise GraphAPIError("Session has expired", status=401, code="190")
            if path == "/me/permissions" and token == "valid-page":
                return {"data": [{"permission": p, "status": "granted"} for p in self.REQUIRED_PERMISSIONS]}
            if path == "/123456789":
                return {"id": "123456789", "name": "Azad Studio ",
                        "link": "https://www.facebook.com/123456789",
                        "tasks": ["CREATE_CONTENT", "MANAGE", "MODERATE"]}
            return {}

    result = FakeGraph(db, cfg).verify_identity()
    assert result["ok"] is True
    assert result["identity_method"] == "page_token_fallback"
    assert db.get_setting("facebook_identity_method") == "page_token_fallback"


def test_page_token_fallback_works_on_fresh_db_with_tasks(tmp_path):
    cfg = temp_config(tmp_path, expected_page_name="Azad Studio", facebook_page_id="123456789",
                      facebook_user_token="expired-user", facebook_page_token="valid-page")
    db = Database(cfg.db_path)

    class FakeGraph(FacebookGraphClient):
        def _call(self, method, path, *, token, params=None, data=None, files=None):
            if path in {"/me/accounts", "/me/permissions"}:
                raise GraphAPIError("Session has expired", status=401, code="190")
            if path == "/123456789":
                return {"id": "123456789", "name": "Azad Studio ",
                        "link": "https://www.facebook.com/123456789",
                        "tasks": ["CREATE_CONTENT", "MANAGE", "MODERATE"]}
            return {}

    result = FakeGraph(db, cfg).verify_identity()
    assert result["ok"] is True
    assert result["identity_method"] == "page_token_fallback"


def test_page_token_fallback_requires_tasks_or_permissions(tmp_path):
    cfg = temp_config(tmp_path, expected_page_name="Azad Studio", facebook_page_id="123456789",
                      facebook_user_token="expired-user", facebook_page_token="valid-page")
    db = Database(cfg.db_path)
    db.set_setting("facebook_identity_verified_at", iso())

    class FakeGraph(FacebookGraphClient):
        def _call(self, method, path, *, token, params=None, data=None, files=None):
            if path in {"/me/accounts", "/me/permissions"}:
                raise GraphAPIError("Session has expired", status=401, code="190")
            if path == "/123456789":
                return {"id": "123456789", "name": "Azad Studio ",
                        "link": "https://www.facebook.com/123456789"}
            return {}

    result = FakeGraph(db, cfg).verify_identity()
    assert result["ok"] is False
    assert "tasks or permissions" in result["reason"]


def test_graph_api_error_is_recorded_without_false_success(tmp_path):
    cfg = temp_config(tmp_path, dry_run=False, publish_enabled=True, publish_safety_switch=True,
                      operator_approved=True, verify_approved=True, auto_post_enabled=True,
                      facebook_page_token="fake-token", expected_page_name="AZAD STUDIO")
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


def _demo_media(tmp_path: Path, n: int) -> dict:
    import hashlib
    path = tmp_path / f"photo-{n}.jpg"
    Image.new("RGB", (900, 600), (30 + n * 40, 60, 90)).save(path, format="JPEG", quality=85)
    return {
        "url": f"https://example.invalid/demo-photo-{n}",
        "credit": f"DEMO / synthetic photo {n}",
        "license_ref": "Layout preview only — not for publication",
        "local_path": str(path),
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "width": 900,
        "height": 600,
        "provider": "demo",
    }


def test_two_photo_card_layout_passes_visual_inspection(tmp_path):
    cfg = temp_config(tmp_path)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ed = fixture["editorial"]
    media = [_demo_media(tmp_path, 1), _demo_media(tmp_path, 2)]
    content = EditorialContent(
        urdu_headline=ed["urdu_headline"],
        urdu_bullets=ed["urdu_bullets"],
        roman_headline=ed["roman_headline"],
        roman_bullets=ed["roman_bullets"],
        verification_notes=ed.get("verification_notes", []),
        source_urls=ed.get("source_urls", []),
        publication_time=iso(),
        media=media,
    )
    cards = CardRenderer(cfg).render_cards(content, "two-photo-test", media=content.media)
    assert cards
    card = cards[0]
    assert card.visual_status == "passed"
    assert card.audit["media_count"] == 2
    assert card.audit["visual_inspection"]["checks"]["photo_1_pixels"] is True
    assert card.audit["visual_inspection"]["checks"]["photo_2_pixels"] is True
    assert card.audit["byline"] == BYLINE
    assert card.audit["visual_inspection"]["checks"]["byline_pixels"] is True
    assert card.audit["visual_inspection"]["checks"]["narrow_latest_news_ribbon"] is True
    assert card.audit["heading_box"][2] - card.audit["heading_box"][0] < 720


def test_pixabay_relax_query_shortens_specific_phrases():
    relaxed = PixabayClient.relax_query("Telangana government education exam results")
    assert relaxed[0] == "Telangana government education exam results"
    assert "Telangana government education" in relaxed
    assert relaxed[-1] == "Telangana government"
    assert PixabayClient.relax_query("parliament session") == ["parliament session"]


def test_pixabay_search_keeps_large_images_with_small_webformat(tmp_path):
    cfg = temp_config(tmp_path, pixabay_enabled=True, pixabay_api_key="test-key")

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"hits": [{
                "id": 42, "type": "photo", "tags": "education, exam",
                "webformatURL": "https://cdn.example/42_640.jpg",
                "webformatWidth": 640, "webformatHeight": 360,
                "largeImageURL": "https://cdn.example/42_1280.jpg",
                "imageWidth": 1920, "imageHeight": 1080,
                "user": "exam_demo", "user_id": 7,
            }]}

    class FakeSession:
        headers: dict = {}

        def get(self, url, params=None, timeout=None):
            return FakeResponse()

    client = PixabayClient(config=cfg, session=FakeSession())
    hits = client.search("education exam students")
    assert len(hits) == 1
    assert hits[0]["id"] == 42


def test_long_headlines_fit_gold_bands(tmp_path):
    cfg = temp_config(tmp_path)
    content = EditorialContent(
        urdu_headline="STANFEST 2026: بھارت کا سب سے بڑا تخلیق کار اور گیمنگ میلہ بنگلور میں منعقد ہوگا",
        urdu_bullets=["پہلا نکتہ یہاں درج ہے", "دوسرا نکتہ یہاں درج ہے", "تیسرا نکتہ یہاں درج ہے"],
        roman_headline="STANFEST 2026: India ka sab se bara takhleeq kar aur gaming mela Bengaluru mein",
        roman_bullets=["Pehla point yahan hai", "Doosra point yahan hai", "Teesra point yahan hai"],
        verification_notes=[], source_urls=["https://example.org/story"],
        publication_time=iso(), media=None,
    )
    cards = CardRenderer(cfg).render_cards(content, "long-headline-test")
    assert cards and cards[0].visual_status == "passed"


def test_caption_handles_two_photo_media_list(tmp_path):
    cfg = temp_config(tmp_path)
    db = Database(cfg.db_path)
    agent = PublishingAgent(db, config=cfg)
    editorial = {
        "urdu_headline": "اردو سرخی",
        "roman_headline": "Roman headline",
        "urdu_bullets_json": json.dumps(["ایک نکتہ"]),
        "roman_bullets_json": json.dumps(["Ek point"]),
        "source_urls_json": json.dumps(["https://example.org/story"]),
        "media_json": json.dumps([
            {"credit": "Pixabay / one", "license_ref": "https://pixabay.com/service/terms/"},
            {"credit": "Pixabay / two", "license_ref": "https://pixabay.com/service/terms/"},
        ]),
    }
    caption = agent.build_caption(editorial, "marker123")
    assert "Photo: Pixabay / one" in caption
    assert "Photo: Pixabay / two" in caption
    # Operator rule: no URLs anywhere in the public caption.
    assert "http" not in caption
    assert "Sources:" not in caption
    assert "example.org" not in caption


def test_publish_approved_pending_retries_unpublished_editorial(tmp_path):
    cfg = temp_config(tmp_path)  # dry_run=True in temp config
    db = Database(cfg.db_path)
    pipeline = NewsPipeline(cfg, db)
    candidate = sample_candidate()
    cid, _ = db.insert_candidate(pipeline.research._to_row(candidate, "test"))
    content = {
        "urdu_headline": "اردو سرخی", "urdu_bullets": ["ایک", "دو", "تین"],
        "roman_headline": "Roman headline", "roman_bullets": ["Ek", "Do", "Teen"],
        "verification_notes": [], "source_urls": ["https://example.org/story"],
        "publication_time": iso(),
        "media": [{"credit": "Pixabay / one", "license_ref": "terms"}],
    }
    eid, _ = db.insert_editorial(cid, None, content, status="approved")
    db.execute(
        "INSERT INTO cards(id,editorial_id,file_path,sha256,width,height,page_number,page_count,visual_status,created_at) "
        "VALUES(?,?,?,?,?,? ,1,1,'passed',?)".replace("? ,", "?,"),
        ("card1", eid, "/tmp/card.png", "a" * 64, 1080, 1350, iso()),
    )
    results = pipeline.publish_approved_pending()
    assert results and results[0]["status"] == "dry_run"
    # Second call is idempotent: a dry-run attempt already exists.
    assert pipeline.publish_approved_pending()[0]["status"] == "dry_run"


def test_single_source_substantial_report_verifies_but_claims_hold(tmp_path):
    cfg = temp_config(tmp_path)
    db = Database(cfg.db_path)
    pipeline = NewsPipeline(cfg, db)
    body = (
        "The Telangana government on Tuesday announced a new education policy for "
        "government schools in Hyderabad, setting out teacher recruitment timelines, "
        "infrastructure upgrades and a district-wise implementation schedule. Officials "
        "said the first phase covers 120 schools and begins next month, with budgets "
        "allocated for classrooms, laboratories and digital learning equipment across "
        "the state, according to the official statement issued after the cabinet meeting."
    )
    candidate = sample_candidate(body_excerpt=body)
    cid, _ = db.insert_candidate(pipeline.research._to_row(candidate, "test"))
    result = VerificationAgent(db, config=cfg).verify(cid)
    assert result.approved is True
    assert any("Single-source" in e for e in result.evidence)

    claim = sample_candidate(
        url="https://example.org/policy-story-2",
        original_headline="Opposition alleges irregularities in education policy",
        body_excerpt=body + " The opposition alleged that the process was opaque.",
    )
    cid2, _ = db.insert_candidate(pipeline.research._to_row(claim, "test"))
    result2 = VerificationAgent(db, config=cfg).verify(cid2)
    assert result2.approved is False

    short = sample_candidate(
        url="https://example.org/policy-story-3",
        body_excerpt="A short education policy update from Hyderabad.",
    )
    cid3, _ = db.insert_candidate(pipeline.research._to_row(short, "test"))
    result3 = VerificationAgent(db, config=cfg).verify(cid3)
    assert result3.approved is False


def test_scan_requeues_llm_held_stories_only_when_gateway_available(tmp_path):
    cfg = temp_config(tmp_path, llm_api_key="test-key", llm_model="openai/gpt-oss-120b",
                      llm_api_base="https://api.groq.com/openai/v1")
    pipeline = NewsPipeline(cfg)
    candidate = sample_candidate()
    cid, _ = pipeline.db.insert_candidate(pipeline.research._to_row(candidate, "test"))
    pipeline.db.add_review(cid, None, "urdu_generation_unavailable", {"reason": "gateway down"})
    pipeline.db.add_review(cid, None, "verification_hold", {"reason": "needs human"})

    result = pipeline.scan("hourly", source_results=[])
    assert result["requeued_held"] == 1
    jobs = pipeline.db.query("SELECT * FROM jobs WHERE type='verify_story'")
    assert len(jobs) == 1
    open_rows = pipeline.db.open_reviews(["urdu_generation_unavailable",
                                          "roman_urdu_translation_unavailable"])
    assert open_rows == []
    # Editorial holds are never auto-retried.
    assert len(pipeline.db.open_reviews(["verification_hold"])) == 1

    cfg2 = temp_config(tmp_path / "second")
    pipeline2 = NewsPipeline(cfg2)
    cid2, _ = pipeline2.db.insert_candidate(pipeline2.research._to_row(candidate, "test"))
    pipeline2.db.add_review(cid2, None, "urdu_generation_unavailable", {"reason": "gateway down"})
    assert pipeline2.requeue_llm_held() == 0


def test_llm_retries_without_json_mode_when_gateway_rejects_it(tmp_path, monkeypatch):
    from nr_news import llm as llm_mod

    cfg = temp_config(tmp_path, llm_api_key="test-key", llm_model="openai/gpt-oss-120b",
                      llm_api_base="https://api.groq.com/openai/v1")
    calls = []

    class Resp:
        def __init__(self, status, payload):
            self.status_code = status
            self._payload = payload
            self.text = str(payload)

        def json(self):
            return self._payload

    def fake_post(url, headers=None, json=None, timeout=None):
        calls.append(json)
        if "response_format" in (json or {}):
            return Resp(400, {"error": {"message": "response_format is not supported"}})
        return Resp(200, {"choices": [{"message": {"content": '{"ok": true}'}}]})

    monkeypatch.setattr(llm_mod.requests, "post", fake_post)
    out = llm_mod.LLMClient(cfg).chat_json(system="s", user={"x": 1})
    assert out == {"ok": True}
    assert len(calls) == 2 and "response_format" not in calls[1]


def test_pixabay_query_uses_relevant_story_terms():
    candidate = sample_candidate(
        original_headline="Hyderabad government election policy decision",
        body_excerpt="Telangana cabinet announced a new education policy after the election commission review.",
        category="government",
    )
    query = PixabayClient.build_query(candidate, facts={"locations": ["Hyderabad"], "names": ["TGPSC"]})
    assert "Hyderabad" in query
    assert "government" in query.lower()
    assert "news" not in query.lower().split()


def test_media_agent_fills_two_photos_from_pixabay(tmp_path):
    cfg = temp_config(tmp_path, pixabay_enabled=True, pixabay_api_key="fake-key")
    db = Database(cfg.db_path)
    photos = [_demo_media(tmp_path, 1), _demo_media(tmp_path, 2)]

    class FakePixabay:
        available = True

        @staticmethod
        def build_query(candidate, **kwargs):
            return "Hyderabad education exam"

        @staticmethod
        def search(query):
            return [{"id": 1}, {"id": 2}]

        @staticmethod
        def download(hit, query):
            return photos[hit["id"] - 1]

    agent = MediaAgent(db, config=cfg, pixabay_client=FakePixabay())
    approved = agent.select_many({"id": "c1"}, query_context={"pixabay_query": "Hyderabad education exam"})
    assert len(approved) == 2
    assert {m["provider"] for m in approved} == {"demo"}
    assert db.query("SELECT COUNT(*) AS n FROM media_assets")[0]["n"] == 2


def test_text_led_card_has_operator_byline(tmp_path):
    cfg = temp_config(tmp_path)
    fixture = json.loads(FIXTURE.read_text(encoding="utf-8"))
    ed = fixture["editorial"]
    content = EditorialContent(
        urdu_headline=ed["urdu_headline"],
        urdu_bullets=ed["urdu_bullets"],
        roman_headline=ed["roman_headline"],
        roman_bullets=ed["roman_bullets"],
        verification_notes=ed.get("verification_notes", []),
        source_urls=ed.get("source_urls", []),
        publication_time=iso(),
        media=None,
    )
    cards = CardRenderer(cfg).render_cards(content, "text-led-byline-test")
    assert cards[0].audit["byline"] == BYLINE
    assert cards[0].audit["visual_inspection"]["checks"]["byline_pixels"] is True
    assert cards[0].audit["visual_inspection"]["checks"]["narrow_latest_news_ribbon"] is True
    assert cards[0].audit["heading_box"][2] - cards[0].audit["heading_box"][0] < 720
    assert cards[0].audit["media_count"] == 0
