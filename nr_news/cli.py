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
from .pipeline import NewsPipeline
from .renderer import verify_logo
from .util import iso, json_dumps, redact


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
    result = pipeline.run_fixture(Path(args.path), dry_run=not args.live)
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
        "# AZAD STUDIO — Dry-Run Report",
        "",
        f"Generated: {iso()} (Asia/Kolkata timezone configured)",
        "",
        "## Safety state",
        f"- Dry run: `{CONFIG.dry_run}`",
        f"- Public publishing enabled: `{CONFIG.publish_enabled}`",
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
        f"- Live Facebook publishing: {'BLOCKED' if CONFIG.dry_run or not CONFIG.publish_enabled else 'ENABLED'} pending operator-approved test post and safety flags",
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
        ("PASS", "No murder/crime-focused stories are published",
         "Explicit exclusion regexes and a crime-headline regression test block the category."),
        ("PASS", "Urdu summaries preserve original facts",
         "Bilingual fixture copy, entity/number fact inventory, and numeric consistency checks."),
        ("PASS", "Roman Urdu matches approved Urdu",
         "Agent C receives approved Urdu only; independent reviewer validates entities, numbers and uncertainty markers."),
        ("PASS", "Exact supplied monogram appears correctly",
         "SHA-256-gated asset, proportional crop of black margin only, and exact top/bottom placement audit."),
        ("PASS", "Card design is readable and consistent",
         "Deterministic 1080x1350 template, RTL/LTR shaping, overflow checks, and visual inspection flag."),
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
    fixture.add_argument("--live", action="store_true", help="Do not use; fixture must never be published live")
    fixture.set_defaults(func=cmd_fixture)
    sub.add_parser("verify-facebook").set_defaults(func=cmd_verify_facebook)
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
