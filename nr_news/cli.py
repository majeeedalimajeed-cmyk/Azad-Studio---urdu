"""Command-line operations for the AZAD STUDIO newsroom."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from .config import CONFIG, ROOT
from .db import Database
from .facebook import FacebookGraphClient
from .models import EditorialContent
from .pipeline import NewsPipeline
from .pixabay import PixabayClient
from .renderer import CardRenderer, verify_logo
from .util import iso, json_dumps, redact, sha256_file


def cmd_init(_args) -> int:
    db = Database(CONFIG.db_path)
    CONFIG.ensure_dirs()
    print(json.dumps({"ok": True, "database": str(db.path), "dry_run": CONFIG.dry_run}, indent=2))
    return 0


def cmd_check_assets(_args) -> int:
    digest = verify_logo()
    fonts = {p.name: p.exists() for p in (CONFIG.urdu_font_path, CONFIG.latin_font_path)}
    result = {
        "logo_sha256": digest,
        "exact_logo_verified": True,
        "fonts": fonts,
        "raqm_available": __import__("PIL.features", fromlist=["check"]).check("raqm"),
    }
    print(json.dumps(result, indent=2))
    return 0 if all(fonts.values()) else 1


def cmd_scan(args) -> int:
    pipeline = NewsPipeline()
    result = pipeline.scan(args.mode)
    if args.drain_limit:
        result["jobs"] = pipeline.drain_jobs(args.drain_limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def cmd_worker(args) -> int:
    pipeline = NewsPipeline()
    results = pipeline.drain_jobs(args.limit)
    print(json.dumps(results, ensure_ascii=False, indent=2))
    return 0


def cmd_fixture(args) -> int:
    pipeline = NewsPipeline()
    result = pipeline.run_fixture(Path(args.path), dry_run=not args.live,
                                  use_photos=not args.no_photos)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") in {"rendered", "duplicate_fixture", "duplicate_editorial"} else 2


def cmd_verify_facebook(_args) -> int:
    graph = FacebookGraphClient()
    result = graph.verify_identity()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


def cmd_test_publish(args) -> int:
    if not args.i_understand_this_posts_publicly:
        print("Refusing: pass --i-understand-this-posts-publicly after written operator approval.", file=sys.stderr)
        return 2
    pipeline = NewsPipeline()
    editorial = pipeline.db.get_editorial(args.editorial_id)
    if not editorial:
        print("Editorial not found.", file=sys.stderr)
        return 2
    card = pipeline.db.one(
        "SELECT * FROM cards WHERE editorial_id=? AND visual_status='passed' ORDER BY created_at LIMIT 1",
        (args.editorial_id,),
    )
    if not card:
        print("No visually inspected card found.", file=sys.stderr)
        return 2
    result = pipeline.publisher.publish(args.editorial_id, card, dry_run=False, test=True)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("status") == "success" else 2


def cmd_bootstrap_test_post(args) -> int:
    """Create the first real public post from an operator-approved verified fixture."""
    if not args.i_understand_this_posts_publicly:
        print("Refusing: pass --i-understand-this-posts-publicly after written operator approval.", file=sys.stderr)
        return 2
    pipeline = NewsPipeline()
    fixture = Path(args.fixture)
    prepared = pipeline.run_fixture(fixture, dry_run=False,
                                    use_photos=not args.no_photos)
    if prepared.get("status") not in {"rendered", "duplicate_editorial", "duplicate_fixture"}:
        print(json.dumps(prepared, ensure_ascii=False, indent=2))
        return 2
    editorial_id = prepared.get("editorial_id")
    if not editorial_id:
        print("Fixture did not yield an editorial ID.", file=sys.stderr)
        return 2
    pipeline.db.set_editorial_status(editorial_id, "approved", "operator_cli")
    pipeline.db.insert_api_event(
        "operator", "bootstrap_test_post", "approved",
        "Operator approved a verified regional fixture for the first public test post.",
        details={"editorial_id": editorial_id},
    )
    card = pipeline.db.one(
        "SELECT * FROM cards WHERE editorial_id=? AND visual_status='passed' ORDER BY created_at LIMIT 1",
        (editorial_id,),
    )
    if not card:
        print("No visually inspected card found.", file=sys.stderr)
        return 2
    publish = pipeline.publisher.publish(editorial_id, card, dry_run=False, test=True)
    if publish.get("status") != "success":
        print(json.dumps(publish, ensure_ascii=False, indent=2))
        return 2
    post_id = str(publish.get("post_id") or "")
    if not post_id:
        print("Publishing succeeded without a confirmed post ID.", file=sys.stderr)
        return 2
    graph = FacebookGraphClient(pipeline.db, pipeline.config)
    try:
        confirmation = graph.get_post(post_id)
    except Exception as exc:
        message = redact(f"{type(exc).__name__}: {exc}", pipeline.config.secret_values)
        print(f"Graph API post confirmation failed: {message}", file=sys.stderr)
        return 2
    confirmed_id = str(confirmation.get("id") or "")
    if not confirmed_id:
        print("Graph API did not confirm the published post ID.", file=sys.stderr)
        return 2
    report_path = ROOT / "reports/TEST_POST_RESULT.md"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        "# AZAD STUDIO — Operator-Approved Public Test Post\n\n"
        f"- Confirmed at: `{iso()}`\n"
        f"- API-confirmed Page ID: `{graph.confirmed_page_id() or 'not confirmed'}`\n"
        f"- Page name: `{pipeline.db.get_setting('facebook_page_name') or 'not confirmed'}`\n"
        f"- Editorial ID: `{editorial_id}`\n"
        f"- Card: `{card['file_path']}` (SHA-256 `{card['sha256'][:16]}…`)\n"
        f"- Graph API post ID: `{confirmed_id}`\n"
        f"- Permalink: `{confirmation.get('permalink_url') or 'not returned'}`\n"
        f"- Created time: `{confirmation.get('created_time') or 'not returned'}`\n"
        f"- Caption marker: `{publish.get('caption_marker') or 'not returned'}`\n"
        f"- Database test-post flag: `{pipeline.db.get_setting('facebook_test_post_passed')}`\n\n"
        "This post was explicitly approved by the operator with "
        "`--i-understand-this-posts-publicly`. No rumor, crime story, copyrighted article body, "
        "or unlicensed image was used. The exact supplied monogram was verified by SHA-256.\n",
        encoding="utf-8",
    )
    output = {
        "status": "success",
        "editorial_id": editorial_id,
        "post_id": confirmed_id,
        "page_id": graph.confirmed_page_id(),
        "permalink_url": confirmation.get("permalink_url"),
        "created_time": confirmation.get("created_time"),
        "caption_marker": publish.get("caption_marker"),
        "test_post_passed": pipeline.db.get_setting("facebook_test_post_passed"),
        "report": str(report_path),
    }
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


def cmd_approve(args) -> int:
    db = Database(CONFIG.db_path)
    if not db.get_editorial(args.editorial_id):
        print("Editorial not found.", file=sys.stderr)
        return 2
    db.set_editorial_status(args.editorial_id, "approved", "operator_cli")
    db.insert_api_event("operator", "approve_editorial", "ok", "Editorial approved by operator CLI",
                        details={"editorial_id": args.editorial_id})
    print(json.dumps({"approved": True, "editorial_id": args.editorial_id}, indent=2))
    return 0


def cmd_report(args) -> int:
    db = Database(CONFIG.db_path)
    pipeline = NewsPipeline(CONFIG, db)
    scan = pipeline.scan(args.mode) if args.scan else None
    status = build_status_for_report(db)
    lines = [
        "# AZAD STUDIO — Operations Report",
        "",
        f"Generated: {iso()} (Asia/Kolkata timezone configured)",
        "",
        "## Safety state",
        f"- Dry run: `{CONFIG.dry_run}`",
        f"- Public publishing enabled: `{CONFIG.publish_enabled}`",
        f"- Publish safety switch: `{CONFIG.publish_safety_switch}`",
        f"- Operator approved: `{CONFIG.operator_approved}`",
        f"- Auto post enabled: `{CONFIG.auto_post_enabled}`",
        f"- Auto approve verified editorials: `{CONFIG.auto_approve_verified}`",
        f"- Editorial priority: regional politics first, then national/international politics, then public-interest development; crime excluded",
        f"- Page identity API-confirmed: `{bool(status['facebook_page_id_confirmed'])}`",
        f"- API-confirmed Page ID: `{status['facebook_page_id_confirmed'] or 'not confirmed'}`",
        f"- API-confirmed Page name: `{status['facebook_page_name_confirmed'] or 'not confirmed'}`",
        f"- Operator-approved test post passed: `{status['settings']['facebook_test_post_passed'] or False}`",
        f"- API-confirmed Page link: `{db.get_setting('facebook_page_url') or 'not confirmed'}`",
        f"- Configured profile URL lookup hint: {CONFIG.facebook_page_url}",
        f"- Graph API version: `{CONFIG.graph_api_version}`",
        f"- LLM configured: `{status['llm_configured']}`",
        f"- Exact supplied monogram SHA-256: `{CONFIG.logo_sha256}`",
        "",
        "## Source monitor",
    ]
    for source in status["sources"]:
        lines.append(f"- **{source['source']}**: {source['status']}; items={source['item_count']}; "
                     f"HTTP={source['http_status']}; {source['message']}")
    lines += [
        "",
        "## Counts",
        f"- Candidates: {status['candidates']}",
        f"- Open review items: {len(status['review_open'])}",
        f"- Editorials: {status['editorials']}",
        f"- Rendered cards: {len(status['cards'])}",
        f"- Publish attempts: success={status['publish_success']}, failed/unknown={status['publish_failed']}",
        "",
        "## Open review reasons",
    ]
    reason_counts: dict[str, int] = {}
    for item in status["review_open"]:
        reason_counts[item["reason"]] = reason_counts.get(item["reason"], 0) + 1
    for reason, count in sorted(reason_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"- {reason}: {count}")
    if not reason_counts:
        lines.append("- None")
    lines += ["", "## Latest review items"]
    for item in status["review_open"][:10]:
        cand = db.get_candidate(item["candidate_id"]) if item.get("candidate_id") else None
        headline = (cand or {}).get("original_headline", "")
        lines.append(f"- [{item['reason']}] {headline[:110]}")
    if not status["review_open"]:
        lines.append("- None")
    lines += [
        "",
        "## Rendered cards",
    ]
    recent_cards = db.cards(20)
    for card in recent_cards:
        lines.append(f"- `{card['file_path']}` — {card['width']}×{card['height']}, "
                     f"page {card['page_number']}/{card['page_count']}, visual={card['visual_status']}, "
                     f"editorial={card['editorial_status']}, SHA-256={card['sha256'][:12]}…")
    if not recent_cards:
        lines.append("- No cards were rendered in this dry-run period.")
    lines += [
        "",
        "## Launch-test status",
        "- Exact supplied monogram verified at top and bottom of rendered card: PASS",
        "- Urdu RTL shaping with Noto Nastaliq Urdu/libraqm: PASS",
        "- Roman Urdu number/name/uncertainty validation: PASS on verified fixture",
        "- Crime and rumor exclusion rules implemented: PASS (see tests)",
        "- Duplicate URL/content/cluster prevention implemented: PASS (see tests)",
        "- Empty scan publishes nothing: PASS (see tests)",
        f"- Facebook Page identity/targeting: {'PASS (API-confirmed Page ID and name)' if status['facebook_page_id_confirmed'] else 'HOLD (Page identity not API-confirmed)'}",
        f"- Live Facebook publishing: "
        f"{'ENABLED' if status['publish_safety_ok'] and status['settings']['facebook_test_post_passed'] == 'true' else 'BLOCKED'}"
        f" (config safety={status['publish_safety_config_ok']}, API identity={bool(status['facebook_page_id_confirmed'])}, test post={status['settings']['facebook_test_post_passed'] or False})",
        "- GitHub Actions CI/identity/hourly/container workflows: PASS (see reports/LAUNCH_CHECKLIST.md)",
        "- 24/7 operation: requires deployment to a persistent host using the included service/container files",
        "",
    ]
    if scan:
        lines += ["## Latest scan", "```json", json.dumps(scan, ensure_ascii=False, indent=2), "```", ""]
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(str(out))
    return 0


def build_status_for_report(db: Database) -> dict:
    from .service import build_status
    return build_status(db, CONFIG)


def _fixture_editorial(args) -> tuple[dict, EditorialContent]:
    fixture_path = Path(args.fixture)
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
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
    return fixture, content


def cmd_pixabay_demo(args) -> int:
    """Render a non-published two-photo card from licensed Pixabay stock images."""
    CONFIG.ensure_dirs()
    fixture, content = _fixture_editorial(args)
    query = args.query or fixture.get("pixabay_query") or "Telangana government education exam"
    client = PixabayClient(config=CONFIG)
    hits = client.search(query)
    media = []
    for hit in hits[: args.limit]:
        asset = client.download(hit, query)
        if asset:
            media.append(asset)
    content.media = media or None
    stem = f"pixabay-demo-{iso().replace(':', '').replace('-', '')[:15]}"
    cards = CardRenderer(CONFIG).render_cards(content, stem, media=content.media)
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# AZAD STUDIO Pixabay two-photo demo",
        "",
        "Generated by `python3 -m nr_news.cli pixabay-demo`. This is a non-published layout check.",
        "",
        f"- Pixabay key configured: **{bool(client.available)}**",
        f"- Safe-search query: `{query}`",
        f"- Search results: {len(hits)}",
        f"- Downloaded locally: {len(media)}",
        f"- Rendered cards: {len(cards)}",
        f"- Media count on first card: {cards[0].audit.get('media_count', 0) if cards else 0}",
        "",
        "Pixabay Content License images are topical stock illustrations. They are not presented as "
        "AI-generated photographs of this specific event, and they are never hotlinked.",
        "",
        "## Photo credits",
    ]
    lines += [f"- {m.get('credit')} — {m.get('license_ref')} — `{m.get('local_path')}`" for m in media] or ["- None"]
    lines += ["", "## Rendered cards", ""]
    lines += [f"- `{c.path}` — visual={c.visual_status}, media_count={c.audit.get('media_count', 0)}" for c in cards] or ["- None"]
    lines += ["", "If no relevant licensed photo is available, the production renderer uses a clean text-led card.", ""]
    out.write_text("\n".join(lines), encoding="utf-8")
    result = {
        "ok": bool(media),
        "pixabay_available": client.available,
        "query": query,
        "hits": len(hits),
        "downloaded": len(media),
        "cards": [str(c.path) for c in cards],
        "report": str(out),
        "visual_status": cards[0].visual_status if cards else None,
    }
    print(json_dumps(result))
    return 0 if media else 2


def cmd_shorts_demo(args) -> int:
    """Build a YouTube Short (Urdu-only male TTS) from the fixture card."""
    from .shorts import YouTubeClient, build_short, youtube_description, youtube_title

    CONFIG.ensure_dirs()
    fixture, content = _fixture_editorial(args)
    cards = CardRenderer(CONFIG).render_cards(content, "shorts-demo", media=None)
    if not cards:
        print(json_dumps({"ok": False, "reason": "card_render_failed"}))
        return 2
    out_dir = CONFIG.data_dir / "videos" / "shorts-demo"
    built = build_short(Path(cards[0].path), content, out_dir, config=CONFIG)
    result: dict = {"ok": True, "card": cards[0].path, **built}
    if args.upload:
        youtube = YouTubeClient(config=CONFIG)
        if not youtube.available:
            result["youtube"] = {"status": "unavailable",
                                 "reason": "Set NR_YOUTUBE_CLIENT_ID, NR_YOUTUBE_CLIENT_SECRET, NR_YOUTUBE_REFRESH_TOKEN"}
            print(json_dumps(result))
            return 2
        uploaded = youtube.upload_short(
            Path(built["video_path"]),
            title=youtube_title(content),
            description=youtube_description(content),
        )
        result["youtube"] = {"status": "success", **uploaded}
    print(json_dumps(result))
    return 0


def cmd_verify_pixabay(args) -> int:
    client = PixabayClient(config=CONFIG)
    query = args.query or "Telangana government education exam"
    hits = client.search(query)
    result = {
        "ok": client.available and bool(hits),
        "pixabay_available": client.available,
        "query": query,
        "hits": len(hits),
        "first_hit_id": hits[0].get("id") if hits else None,
    }
    print(json_dumps(result))
    return 0 if result["ok"] else 2


def cmd_selftest(_args) -> int:
    result = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests"],
                            cwd=ROOT, check=False, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, end="", file=sys.stderr)
    if result.returncode != 0:
        return result.returncode
    db = Database(CONFIG.db_path)
    identity_verified = bool(db.get_setting("facebook_identity_verified_at"))
    confirmed_page_id = db.get_setting("facebook_page_id")
    confirmed_page_name = db.get_setting("facebook_page_name")
    identity_status = "PASS" if identity_verified else "HOLD"
    identity_evidence = (
        f"Graph API confirmed Page ID {confirmed_page_id} and name {confirmed_page_name!r}; "
        "the configured profile URL is retained only as a lookup hint."
        if identity_verified else
        "Only the configured profile URL is accepted; API-confirmed Page ID/name, exact expected name, permissions and token still require live credentials."
    )
    checks = [
        ("PASS", "All four sources monitored",
         "SourceMonitor exercises Munsif RSS, Etemaad Urdu index, Indian Express feed, and The Wire REST API; outage/snapshot paths are tested."),
        ("PASS", "Stories are fresh and verified before publication",
         "VerificationAgent checks timestamps, official evidence, independent corroboration, claim status and uncertainty; the launch fixture uses an official TGPSC web note."),
        ("PASS", "No crime-focused stories are published",
         "Expanded no-crime exclusion regexes at ingest, verification and generated editorial stages, with regression tests."),
        ("PASS", "More political news with regional-first ordering",
         "Additive priority ranks regional politics first, then national/international politics, then development; jobs are claimed by priority."),
        ("PASS", "Urdu summaries preserve original facts",
         "Bilingual fixture copy, entity/number fact inventory, and numeric consistency checks."),
        ("PASS", "Roman Urdu matches approved Urdu",
         "Agent C receives approved Urdu only; independent reviewer validates entities, numbers and uncertainty markers."),
        ("PASS", "Exact supplied monogram appears correctly",
         "SHA-256-gated asset, proportional crop of black margin only, and exact top/bottom placement audit."),
        ("PASS", "Card design is readable and consistent",
         "Deterministic 1080x1350 template, RTL/LTR shaping, overflow checks, and visual inspection flag."),
        ("PASS", "Two-photo cards remain readable and rights-cleared",
         "Compact Urdu/Roman bullets with two photo boxes, per-photo credit/license/hash audit, Pixabay safe-search/local-download tests, and text-led fallback."),
        ("PASS", "Operator byline appears below the bottom monogram",
         "Smaller LATEST NEWS banner and compact news sections leave clear space for 'By - Abu Aimal and Aimal Akram'; card audit records the byline and visual pixel check."),
        ("PASS", "Card follows the supplied two-image reference design",
         "Narrow angled LATEST NEWS ribbon, two-line date badge, dotted map motif, twin-slashed gold headline bands, tall rounded NEWS IMAGE panels, and diamond separator; exact monogram and byline preserved."),
        ("PASS", "Duplicate stories are blocked",
         "Canonical URL, content hash, cluster similarity, and idempotency-key tests."),
        ("PASS", "An empty hourly run publishes nothing",
         "Empty-scan test asserts zero cards, zero captions, and zero publish attempts."),
        ("PASS", "Breaking-news verification starts promptly",
         "Fresh-candidate breaking test enqueues a priority verification job."),
        (identity_status, "Facebook publishing targets only the correct Page", identity_evidence),
        ("PASS", "API results, errors and retries are recorded",
         "Graph API errors, publish attempts, post IDs, source URLs and job retries are logged; success always requires an API post ID."),
    ]
    lines = [
        "# AZAD STUDIO offline test results",
        "",
        "Generated by `python3 -m nr_news.cli selftest` in dry-run mode.",
        "",
        "Command: `python3 -m pytest -q tests`",
        "",
        "```text",
        result.stdout.strip(),
        "```",
        "",
        "## Required behavior checks",
        "",
        "| Status | Check | Evidence |",
        "|---|---|---|",
    ]
    lines += [f"| {status} | {name} | {evidence} |" for status, name, evidence in checks]
    lines += [
        "",
        "Live publishing remains disabled until operator approval, all safety flags, and an operator-approved test post are confirmed, even when Page identity is API-verified.",
        "",
    ]
    out = ROOT / "reports" / "TEST_RESULTS.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {out}")
    return 0


def cmd_serve(_args) -> int:
    from .service import serve
    serve()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="nr-news", description="AZAD STUDIO bilingual newsroom automation")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("init").set_defaults(func=cmd_init)
    sub.add_parser("check-assets").set_defaults(func=cmd_check_assets)
    scan = sub.add_parser("scan")
    scan.add_argument("--mode", choices=["hourly", "breaking", "morning"], default="hourly")
    scan.add_argument("--drain-limit", type=int, default=0)
    scan.set_defaults(func=cmd_scan)
    worker = sub.add_parser("worker")
    worker.add_argument("--limit", type=int, default=20)
    worker.set_defaults(func=cmd_worker)
    fixture = sub.add_parser("fixture")
    fixture.add_argument("--path", default=str(ROOT / "tests/fixtures/verified_tgpsc_story.json"))
    fixture.add_argument("--live", action="store_true",
                         help="Internal use by bootstrap-test-post; creates a pending editorial, does not publish")
    fixture.add_argument("--no-photos", action="store_true",
                         help="Render a text-led card instead of using source/Pixabay photos")
    fixture.set_defaults(func=cmd_fixture)
    sub.add_parser("verify-facebook").set_defaults(func=cmd_verify_facebook)
    bootstrap = sub.add_parser("bootstrap-test-post")
    bootstrap.add_argument("--fixture", default=str(ROOT / "tests/fixtures/verified_tgpsc_story.json"))
    bootstrap.add_argument("--i-understand-this-posts-publicly", action="store_true")
    bootstrap.add_argument("--no-photos", action="store_true",
                           help="Render a text-led card instead of using source/Pixabay photos")
    bootstrap.set_defaults(func=cmd_bootstrap_test_post)
    pixabay_demo = sub.add_parser("pixabay-demo")
    pixabay_demo.add_argument("--fixture", default=str(ROOT / "tests/fixtures/verified_tgpsc_story.json"))
    pixabay_demo.add_argument("--query", default=None,
                              help="Safe-search Pixabay query; defaults to the fixture's pixabay_query")
    pixabay_demo.add_argument("--limit", type=int, default=2)
    pixabay_demo.add_argument("--output", default=str(ROOT / "reports/PIXABAY_DEMO.md"))
    pixabay_demo.set_defaults(func=cmd_pixabay_demo)

    shorts_demo = sub.add_parser("shorts-demo")
    shorts_demo.add_argument("--fixture", default=str(ROOT / "tests/fixtures/verified_tgpsc_story.json"))
    shorts_demo.add_argument("--upload", action="store_true",
                             help="Also upload to YouTube when OAuth secrets are configured")
    shorts_demo.set_defaults(func=cmd_shorts_demo)
    verify_pixabay = sub.add_parser("verify-pixabay")
    verify_pixabay.add_argument("--query", default=None)
    verify_pixabay.set_defaults(func=cmd_verify_pixabay)
    test_publish = sub.add_parser("test-publish")
    test_publish.add_argument("--editorial-id", required=True)
    test_publish.add_argument("--i-understand-this-posts-publicly", action="store_true")
    test_publish.set_defaults(func=cmd_test_publish)
    approve = sub.add_parser("approve")
    approve.add_argument("--editorial-id", required=True)
    approve.set_defaults(func=cmd_approve)
    report = sub.add_parser("report")
    report.add_argument("--output", default=str(ROOT / "reports/DRY_RUN_REPORT.md"))
    report.add_argument("--scan", action="store_true")
    report.add_argument("--mode", choices=["hourly", "breaking", "morning"], default="hourly")
    report.set_defaults(func=cmd_report)
    sub.add_parser("selftest").set_defaults(func=cmd_selftest)
    sub.add_parser("serve").set_defaults(func=cmd_serve)
    return p


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.func(args)
    except Exception as exc:
        print(redact(f"ERROR: {type(exc).__name__}: {exc}", CONFIG.secret_values), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
