"""Long-running service: persistent scheduler, durable worker and operations dashboard."""
from __future__ import annotations

import html
import json
import mimetypes
import os
import threading
import time
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .config import CONFIG
from .db import Database
from .facebook import FacebookGraphClient
from .pipeline import NewsPipeline
from .util import iso, parse_iso, redact


class PersistentScheduler:
    def __init__(self, db: Database, config=CONFIG):
        self.db = db
        self.config = config
        self.stop_event = threading.Event()

    def _due(self, key: str, minutes: int) -> bool:
        last = parse_iso(self.db.get_setting(key))
        if not last:
            return True
        return datetime.now(ZoneInfo("UTC")) - last >= timedelta(minutes=minutes)

    def tick(self) -> None:
        now_ist = datetime.now(self.config.timezone)
        utc_now = datetime.now(ZoneInfo("UTC"))
        schedules = [
            ("hourly", "last_hourly_scan", self.config.hourly_minutes, {"mode": "hourly"}),
            ("breaking", "last_breaking_scan", self.config.breaking_minutes, {"mode": "breaking"}),
        ]
        for name, key, minutes, payload in schedules:
            if self._due(key, minutes):
                self.db.enqueue_job("scan", payload, dedupe_key=f"scan:{name}:{utc_now.strftime('%Y%m%d%H%M')}", priority=20)
                self.db.set_setting(key, iso())
                self.db.insert_api_event("scheduler", name, "queued", f"{name} scan job queued",
                                         details={"timezone": "Asia/Kolkata"})
        morning_key = "last_morning_scan_date"
        if now_ist.hour >= self.config.morning_hour_ist and self.db.get_setting(morning_key) != now_ist.strftime("%Y-%m-%d"):
            self.db.enqueue_job("scan", {"mode": "morning"}, dedupe_key=f"scan:morning:{now_ist.strftime('%Y%m%d')}", priority=20)
            self.db.set_setting(morning_key, now_ist.strftime("%Y-%m-%d"))
            self.db.set_setting("last_morning_scan", iso())

    def run(self) -> None:
        while not self.stop_event.wait(20):
            try:
                self.tick()
            except Exception as exc:
                self.db.insert_api_event("scheduler", "tick", "error",
                                         redact(f"{type(exc).__name__}: {exc}", self.config.secret_values))


class DurableWorker:
    def __init__(self, pipeline: NewsPipeline):
        self.pipeline = pipeline
        self.db = pipeline.db
        self.config = pipeline.config
        self.stop_event = threading.Event()

    def run(self) -> None:
        while not self.stop_event.is_set():
            try:
                results = self.pipeline.drain_jobs(limit=20)
                if not results:
                    self.stop_event.wait(2)
            except Exception as exc:
                self.db.insert_api_event("scheduler", "worker", "error",
                                         redact(f"{type(exc).__name__}: {exc}", self.config.secret_values))
                self.stop_event.wait(5)


def build_status(db: Database, config=CONFIG) -> dict[str, Any]:
    summary = db.status_summary()
    graph = FacebookGraphClient(db, config)
    summary.update({
        "generated_at": iso(),
        "timezone": "Asia/Kolkata",
        "dry_run": config.dry_run,
        "publish_enabled": config.publish_enabled,
        "publish_safety_switch": config.publish_safety_switch,
        "operator_approved": config.operator_approved,
        "auto_post_enabled": config.auto_post_enabled,
        "auto_approve_verified": config.auto_approve_verified,
        "publish_safety_config_ok": config.publish_safety_ok,
        "publish_safety_ok": config.publish_safety_ok and db.get_setting("facebook_identity_verified_at") is not None,
        "facebook_destination": config.facebook_page_url,
        "facebook_page_id_confirmed": graph.confirmed_page_id(),
        "facebook_page_name_confirmed": db.get_setting("facebook_page_name"),
        "graph_api_version": config.graph_api_version,
        "llm_configured": bool(config.llm_api_key and config.llm_model),
        "separate_translator_model": config.translator_model,
        "logo_sha256": config.logo_sha256,
        "cards": [{
            **card,
            "filename": Path(card["file_path"]).name,
            "url": "/cards/" + Path(card["file_path"]).name,
        } for card in db.cards(limit=12)],
        "review_open": db.query("SELECT * FROM review_queue WHERE status='open' ORDER BY created_at DESC LIMIT 20"),
        "publish_attempts": db.publish_attempts(limit=20),
        "api_events": db.api_events(limit=40),
        "settings": {
            "facebook_identity_verified_at": db.get_setting("facebook_identity_verified_at"),
            "facebook_test_post_passed": db.get_setting("facebook_test_post_passed"),
        },
    })
    return summary


def dashboard_html(status: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else "—"), quote=True)

    def badge(ok: bool, label: str) -> str:
        color = "#38b76b" if ok else "#e2b84a"
        return f'<span class="badge" style="border-color:{color};color:{color}">{esc(label)}</span>'

    configured_sources = ["the_hindu", "india_today", "the_wire"]
    source_map = {s["source"]: s for s in status["sources"]}
    source_rows = []
    for name in configured_sources:
        s = source_map.get(name, {})
        ok = s.get("status") == "ok"
        source_rows.append(
            f"<tr><td>{esc(name.replace('_',' ').title())}</td>"
            f"<td>{badge(ok, 'OK' if ok else ('CHECKED / OUTAGE' if s else 'NOT YET CHECKED'))}</td>"
            f"<td>{esc(s.get('item_count', 0))}</td><td>{esc(s.get('last_checked_at'))}</td>"
            f"<td>{esc(s.get('message'))}</td></tr>"
        )
    cards_html = "".join(
        f'<article class="card"><a href="{esc(c["url"])}" target="_blank"><img src="{esc(c["url"])}" alt="Rendered news card"></a>'
        f'<div><strong>{esc(c["urdu_headline"])}</strong><p>{esc(c["editorial_status"])} · {esc(c["created_at"])}</p>'
        f'<a href="{esc(c["url"])}" target="_blank">Open full card</a></div></article>'
        for c in status["cards"]
    ) or '<p class="empty">No cards rendered yet.</p>'
    events_html = "".join(
        f"<tr><td>{esc(e['created_at'])}</td><td>{esc(e['subsystem'])}</td><td>{esc(e['action'])}</td>"
        f"<td>{badge(e['status']=='ok' or e['status']=='success' or e['status']=='approved', esc(e['status']))}</td>"
        f"<td>{esc(e['message'])}</td></tr>"
        for e in status["api_events"][:20]
    )
    attempts_html = "".join(
        f"<tr><td>{esc(a['created_at'])}</td><td>{esc(a['status'])}</td><td>{esc(a['target_page_id'])}</td>"
        f"<td>{esc(a['post_id'])}</td><td>{esc(a['last_error'])}</td></tr>"
        for a in status["publish_attempts"][:20]
    ) or '<tr><td colspan="5">No publish attempts yet.</td></tr>'
    reviews_html = "".join(
        f"<li><strong>{esc(r['reason'])}</strong> · {esc(r['created_at'])}<br><small>{esc(r['details_json'])[:240]}</small></li>"
        for r in status["review_open"][:12]
    ) or "<li>No open review items.</li>"

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AZAD STUDIO — Dry Run Control Center</title>
<style>
:root{{--bg:#080808;--panel:#11100d;--gold:#e2b84a;--gold2:#ffe797;--muted:#b8a878;--white:#f5f3ec;--red:#b51f32;--green:#38b76b;--line:#3c3218}}
*{{box-sizing:border-box}}body{{margin:0;background:radial-gradient(circle at 50% 0,#1b160b 0,#080808 42%);color:var(--white);font:15px/1.5 Inter,Arial,sans-serif}}
header{{padding:28px 5vw 22px;border-bottom:1px solid var(--line);background:rgba(0,0,0,.55)}}
h1{{margin:0;font-size:30px;letter-spacing:.08em;color:var(--gold2)}}h1 span{{color:var(--white)}}
.sub{{color:var(--muted);margin-top:6px}}main{{padding:24px 5vw 60px;max-width:1400px;margin:auto}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;margin-bottom:24px}}
.tile{{background:linear-gradient(145deg,#19170f,#0d0c0a);border:1px solid var(--line);border-radius:14px;padding:16px;min-height:104px}}
.tile .label{{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.1em}}.tile .value{{font-size:24px;font-weight:700;color:var(--gold2);margin-top:8px;overflow-wrap:anywhere}}
.badge{{display:inline-block;border:1px solid;border-radius:999px;padding:2px 9px;font-size:11px;font-weight:700;letter-spacing:.05em;text-transform:uppercase}}
section{{background:rgba(16,15,12,.88);border:1px solid var(--line);border-radius:16px;padding:18px;margin:18px 0;box-shadow:0 14px 40px rgba(0,0,0,.25)}}
h2{{color:var(--gold2);font-size:18px;margin:0 0 14px;letter-spacing:.04em}}table{{width:100%;border-collapse:collapse}}th,td{{text-align:left;padding:9px 8px;border-bottom:1px solid #282315;vertical-align:top}}th{{color:var(--muted);font-size:12px;text-transform:uppercase}}td{{font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fill,minmax(230px,1fr));gap:16px}}.card{{background:#0c0b09;border:1px solid var(--line);border-radius:12px;overflow:hidden}}.card img{{width:100%;display:block;aspect-ratio:4/5;object-fit:cover}}.card div{{padding:12px}}.card p{{color:var(--muted);font-size:12px}}.card a{{color:var(--gold2)}}
.empty{{color:var(--muted)}}ul{{padding-left:18px}}li{{margin:7px 0}}small{{color:var(--muted)}}
.notice{{border-left:4px solid var(--red);padding:12px 16px;background:rgba(181,31,50,.1);color:#ffd8de;border-radius:8px}}
code{{color:var(--gold2);overflow-wrap:anywhere}}footer{{color:var(--muted);text-align:center;padding:20px}}
@media(max-width:700px){{h1{{font-size:23px}}th:nth-child(4),td:nth-child(4){{display:none}}}}
</style></head><body>
<header><h1>AZAD STUDIO <span>NEWSROOM CONTROL CENTER</span></h1>
<div class="sub">Asia/Kolkata · Graph API {esc(status['graph_api_version'])} · {esc(status['generated_at'])}</div></header>
<main>
<div class="notice"><strong>{'DRY RUN' if status['dry_run'] else 'LIVE AUTO POST'}:</strong> {
    'Public Facebook publishing is disabled; safety flags, API-confirmed Page identity and an operator-approved test post are required.'
    if status['dry_run'] else
    'Auto posting is enabled until stopped. Set NR_AUTO_POST_ENABLED or NR_PUBLISH_ENABLED to false to stop.'
}</div>
<div class="grid">
<div class="tile"><div class="label">Mode</div><div class="value">{badge(status['dry_run'],'DRY RUN' if status['dry_run'] else 'LIVE')}</div></div>
<div class="tile"><div class="label">Auto post</div><div class="value">{badge(status['auto_post_enabled'],'RUNNING' if status['auto_post_enabled'] else 'STOPPED')}</div></div>
<div class="tile"><div class="label">Publishing safety</div><div class="value">{badge(status['publish_safety_ok'],'READY' if status['publish_safety_ok'] else 'BLOCKED')}</div></div>
<div class="tile"><div class="label">Facebook Page ID</div><div class="value">{esc(status['facebook_page_id_confirmed'] or 'NOT API-CONFIRMED')}</div></div>
<div class="tile"><div class="label">LLM agents</div><div class="value">{badge(status['llm_configured'],'CONFIGURED' if status['llm_configured'] else 'HOLD FOR REVIEW')}</div></div>
<div class="tile"><div class="label">Cards / candidates</div><div class="value">{esc(status['cards'] and len(status['cards']) or 0)} / {esc(status['candidates'])}</div></div>
<div class="tile"><div class="label">Open review / jobs</div><div class="value">{esc(status['review_open'])} / {esc(status['jobs_queued'] + status['jobs_running'])}</div></div>
</div>
<section><h2>Configured sources</h2><table><thead><tr><th>Source</th><th>Status</th><th>Items</th><th>Last check</th><th>Message</th></tr></thead><tbody>{''.join(source_rows)}</tbody></table></section>
<section><h2>Rendered cards</h2><div class="cards">{cards_html}</div></section>
<section><h2>Duplicate protection & audit</h2><p><strong>Only destination:</strong> <code>{esc(status['facebook_destination'])}</code></p>
<p><strong>Exact supplied monogram SHA-256:</strong> <code>{esc(status['logo_sha256'])}</code></p>
<p><strong>Page identity verified:</strong> {esc(status['settings']['facebook_identity_verified_at'])} · <strong>Approved test post:</strong> {esc(status['settings']['facebook_test_post_passed'])}</p></section>
<section><h2>Publish attempts</h2><table><thead><tr><th>Created</th><th>Status</th><th>Target Page ID</th><th>Post ID</th><th>Error</th></tr></thead><tbody>{attempts_html}</tbody></table></section>
<section><h2>Manual review queue</h2><ul>{reviews_html}</ul></section>
<section><h2>Recent API / agent events</h2><table><thead><tr><th>Time</th><th>Subsystem</th><th>Action</th><th>Status</th><th>Message</th></tr></thead><tbody>{events_html}</tbody></table></section>
</main><footer>AZAD STUDIO news operation · Separate from any other publisher configuration · Accuracy before speed.</footer>
<script>setTimeout(()=>location.reload(),60000)</script>
</body></html>"""


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "AZADStudio/1.0"

    @property
    def db(self) -> Database:
        return self.server.db  # type: ignore[attr-defined]

    @property
    def config(self):
        return self.server.config  # type: ignore[attr-defined]

    def _send(self, status: int, content: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(content)

    def _json(self, value: Any, status: int = 200) -> None:
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _admin_ok(self) -> bool:
        expected = self.config.admin_token
        if not expected:
            return False
        return self.headers.get("X-NR-Admin-Token", "") == expected

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send(200, dashboard_html(build_status(self.db, self.config)).encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/health":
            self._json({"ok": True, "time": iso()})
            return
        if path == "/api/status":
            self._json(build_status(self.db, self.config))
            return
        if path.startswith("/cards/"):
            name = Path(path.removeprefix("/cards/")).name
            file_path = self.config.card_dir / name
            if file_path.exists() and file_path.parent == self.config.card_dir:
                self._send(200, file_path.read_bytes(), "image/png")
                return
        self._json({"error": "not_found"}, 404)

    def do_POST(self) -> None:
        path = self.path.split("?", 1)[0]
        if not self._admin_ok():
            self._json({"error": "admin_token_required"}, 401)
            return
        length = int(self.headers.get("Content-Length", "0") or "0")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8")) if length else {}
        except json.JSONDecodeError:
            self._json({"error": "invalid_json"}, 400)
            return
        if path == "/api/scan":
            mode = payload.get("mode", "hourly")
            if mode not in {"hourly", "breaking", "morning"}:
                self._json({"error": "invalid_mode"}, 400)
                return
            jid = self.db.enqueue_job("scan", {"mode": mode}, dedupe_key=f"manual:{mode}:{iso()[:16]}", priority=20)
            self._json({"queued": bool(jid), "job_id": jid, "mode": mode})
            return
        if path == "/api/approve":
            eid = payload.get("editorial_id")
            if not eid or not self.db.get_editorial(eid):
                self._json({"error": "editorial_not_found"}, 404)
                return
            self.db.set_editorial_status(eid, "approved", "operator_dashboard")
            self.db.insert_api_event("operator", "approve_editorial", "ok", "Editorial approved by operator",
                                     details={"editorial_id": eid})
            self._json({"approved": True, "editorial_id": eid})
            return
        self._json({"error": "not_found"}, 404)

    def log_message(self, format: str, *args: Any) -> None:
        # Do not log query strings or headers, which could carry credentials.
        return


class NRHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, handler, *, db: Database, config=CONFIG):
        self.db = db
        self.config = config
        super().__init__(address, handler)


def serve(host: str = "0.0.0.0", port: int | None = None) -> None:
    port = port or int(os.getenv("NR_PORT", "8000"))
    db = Database(CONFIG.db_path)
    pipeline = NewsPipeline(CONFIG, db)
    scheduler = PersistentScheduler(db, CONFIG)
    worker = DurableWorker(pipeline)
    threads = [
        threading.Thread(target=scheduler.run, name="NR-Scheduler", daemon=True),
        threading.Thread(target=worker.run, name="NR-Worker", daemon=True),
    ]
    for t in threads:
        t.start()
    server = NRHTTPServer((host, port), DashboardHandler, db=db, config=CONFIG)
    db.insert_api_event("service", "start", "ok", f"AZAD STUDIO newsroom service listening on {host}:{port}",
                        details={"dry_run": CONFIG.dry_run, "timezone": "Asia/Kolkata"})
    try:
        server.serve_forever(poll_interval=1)
    except KeyboardInterrupt:
        pass
    finally:
        scheduler.stop_event.set()
        worker.stop_event.set()
        server.server_close()


if __name__ == "__main__":
    serve()
