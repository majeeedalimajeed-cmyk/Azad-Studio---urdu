"""Durable SQLite audit database and job queue."""
from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from .util import iso, json_dumps, sha256_text, utcnow

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    last_checked_at TEXT,
    last_success_at TEXT,
    status TEXT NOT NULL,
    item_count INTEGER DEFAULT 0,
    http_status INTEGER,
    message TEXT,
    etag TEXT,
    last_modified TEXT
);

CREATE TABLE IF NOT EXISTS clusters (
    id TEXT PRIMARY KEY,
    fingerprint TEXT NOT NULL,
    headline TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_material_update_at TEXT,
    status TEXT NOT NULL DEFAULT 'monitoring',
    post_id TEXT,
    UNIQUE(fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_clusters_seen ON clusters(last_seen_at);

CREATE TABLE IF NOT EXISTS candidates (
    id TEXT PRIMARY KEY,
    canonical_url TEXT NOT NULL UNIQUE,
    source TEXT NOT NULL,
    publisher TEXT NOT NULL,
    original_headline TEXT NOT NULL,
    url TEXT NOT NULL,
    published_at TEXT,
    updated_at TEXT,
    fetched_at TEXT NOT NULL,
    language TEXT,
    title_hash TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    body_excerpt_hash TEXT NOT NULL,
    body_excerpt TEXT NOT NULL,
    category TEXT,
    image_url TEXT,
    image_credit TEXT,
    image_license TEXT,
    external_links_json TEXT NOT NULL,
    raw_json TEXT NOT NULL,
    cluster_id TEXT,
    status TEXT NOT NULL DEFAULT 'new',
    review_reason TEXT,
    FOREIGN KEY(cluster_id) REFERENCES clusters(id)
);
CREATE INDEX IF NOT EXISTS idx_candidates_status ON candidates(status);
CREATE INDEX IF NOT EXISTS idx_candidates_cluster ON candidates(cluster_id);
CREATE INDEX IF NOT EXISTS idx_candidates_published ON candidates(published_at);

CREATE TABLE IF NOT EXISTS review_queue (
    id TEXT PRIMARY KEY,
    candidate_id TEXT,
    cluster_id TEXT,
    reason TEXT NOT NULL,
    details_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    created_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_review_status ON review_queue(status);

CREATE TABLE IF NOT EXISTS editorials (
    id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    cluster_id TEXT,
    urdu_headline TEXT NOT NULL,
    urdu_bullets_json TEXT NOT NULL,
    roman_headline TEXT NOT NULL,
    roman_bullets_json TEXT NOT NULL,
    verification_notes_json TEXT NOT NULL,
    source_urls_json TEXT NOT NULL,
    publication_time TEXT,
    media_json TEXT,
    content_hash TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL DEFAULT 'pending_review',
    approved_by TEXT,
    created_at TEXT NOT NULL,
    FOREIGN KEY(candidate_id) REFERENCES candidates(id)
);
CREATE INDEX IF NOT EXISTS idx_editorials_status ON editorials(status);

CREATE TABLE IF NOT EXISTS cards (
    id TEXT PRIMARY KEY,
    editorial_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    width INTEGER NOT NULL,
    height INTEGER NOT NULL,
    page_number INTEGER NOT NULL DEFAULT 1,
    page_count INTEGER NOT NULL DEFAULT 1,
    visual_status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY(editorial_id) REFERENCES editorials(id)
);

CREATE TABLE IF NOT EXISTS media_assets (
    id TEXT PRIMARY KEY,
    url TEXT NOT NULL,
    local_path TEXT,
    credit TEXT,
    license_ref TEXT NOT NULL,
    permission_ref TEXT,
    sha256 TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS publish_attempts (
    id TEXT PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE,
    editorial_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    target_page_id TEXT,
    caption_hash TEXT NOT NULL,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    request_started_at TEXT,
    response_at TEXT,
    post_id TEXT,
    photo_id TEXT,
    last_error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_events (
    id TEXT PRIMARY KEY,
    subsystem TEXT NOT NULL,
    action TEXT NOT NULL,
    status TEXT NOT NULL,
    http_status INTEGER,
    error_code TEXT,
    message TEXT NOT NULL,
    details_json TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_api_events_created ON api_events(created_at);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    dedupe_key TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'queued',
    run_at TEXT NOT NULL,
    locked_at TEXT,
    started_at TEXT,
    completed_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(status, run_at);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.init()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init(self) -> None:
        with self._lock, self.connect() as conn:
            conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple[Any, ...] = ()) -> None:
        with self._lock, self.connect() as conn:
            conn.execute(sql, params)

    def query(self, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(sql, params).fetchall()]

    def one(self, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def set_setting(self, key: str, value: str) -> None:
        self.execute(
            "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at",
            (key, value, iso()),
        )

    def get_setting(self, key: str) -> str | None:
        row = self.one("SELECT value FROM settings WHERE key=?", (key,))
        return row["value"] if row else None

    def record_source_status(self, source: str, ok: bool, item_count: int, message: str,
                             http_status: int | None = None, etag: str | None = None,
                             last_modified: str | None = None) -> None:
        now = iso()
        self.execute(
            """INSERT INTO source_status
               (source,last_checked_at,last_success_at,status,item_count,http_status,message,etag,last_modified)
               VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(source) DO UPDATE SET
                 last_checked_at=excluded.last_checked_at,
                 last_success_at=CASE WHEN excluded.status='ok' THEN excluded.last_success_at ELSE source_status.last_success_at END,
                 status=excluded.status,item_count=excluded.item_count,http_status=excluded.http_status,
                 message=excluded.message,etag=COALESCE(excluded.etag,source_status.etag),
                 last_modified=COALESCE(excluded.last_modified,source_status.last_modified)""",
            (source, now, now if ok else None, "ok" if ok else "error", item_count,
             http_status, message[:1000], etag, last_modified),
        )

    def source_statuses(self) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM source_status ORDER BY source")

    def insert_candidate(self, candidate: dict[str, Any]) -> tuple[str, bool]:
        """Return (id, created). URL uniqueness is the first dedupe layer."""
        existing = self.one("SELECT id FROM candidates WHERE canonical_url=?", (candidate["canonical_url"],))
        if existing:
            return existing["id"], False
        cid = uuid.uuid4().hex
        self.execute(
            """INSERT INTO candidates
               (id,canonical_url,source,publisher,original_headline,url,published_at,updated_at,fetched_at,
                language,title_hash,fingerprint,body_excerpt_hash,body_excerpt,category,image_url,
                image_credit,image_license,external_links_json,raw_json,status)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'new')""",
            (
                cid, candidate["canonical_url"], candidate["source"], candidate["publisher"],
                candidate["original_headline"], candidate["url"], candidate.get("published_at"),
                candidate.get("updated_at"), iso(), candidate.get("language"),
                candidate["title_hash"], candidate["fingerprint"], candidate["body_excerpt_hash"],
                candidate["body_excerpt"], candidate.get("category"), candidate.get("image_url"),
                candidate.get("image_credit"), candidate.get("image_license"),
                json_dumps(candidate.get("external_links", [])), json_dumps(candidate.get("raw", {})),
            ),
        )
        return cid, True

    def update_candidate(self, cid: str, **fields: Any) -> None:
        allowed = {"cluster_id", "status", "review_reason", "updated_at", "body_excerpt", "body_excerpt_hash"}
        pairs = [(k, v) for k, v in fields.items() if k in allowed]
        if not pairs:
            return
        sql = "UPDATE candidates SET " + ",".join(f"{k}=?" for k, _ in pairs) + " WHERE id=?"
        self.execute(sql, tuple(v for _, v in pairs) + (cid,))

    def get_candidate(self, cid: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM candidates WHERE id=?", (cid,))

    def recent_candidates(self, hours: int = 72, limit: int = 200) -> list[dict[str, Any]]:
        return self.query(
            "SELECT * FROM candidates WHERE fetched_at >= datetime('now',?) ORDER BY fetched_at DESC LIMIT ?",
            (f"-{hours} hours", limit),
        )

    def upsert_cluster(self, fingerprint: str, headline: str) -> str:
        row = self.one("SELECT id FROM clusters WHERE fingerprint=?", (fingerprint,))
        if row:
            cid = row["id"]
            self.execute("UPDATE clusters SET last_seen_at=?,headline=? WHERE id=?", (iso(), headline, cid))
            return cid
        cid = uuid.uuid4().hex
        self.execute(
            "INSERT INTO clusters(id,fingerprint,headline,first_seen_at,last_seen_at,status) VALUES(?,?,?,?,?,?)",
            (cid, fingerprint, headline, iso(), iso(), "monitoring"),
        )
        return cid

    def get_cluster(self, cid: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM clusters WHERE id=?", (cid,))

    def set_cluster_status(self, cid: str, status: str, post_id: str | None = None) -> None:
        if post_id:
            self.execute("UPDATE clusters SET status=?,post_id=?,last_material_update_at=? WHERE id=?", (status, post_id, iso(), cid))
        else:
            self.execute("UPDATE clusters SET status=? WHERE id=?", (status, cid))

    def add_review(self, candidate_id: str | None, cluster_id: str | None, reason: str, details: dict[str, Any]) -> str:
        rid = uuid.uuid4().hex
        self.execute(
            "INSERT INTO review_queue(id,candidate_id,cluster_id,reason,details_json,status,created_at) VALUES(?,?,?,?,?,'open',?)",
            (rid, candidate_id, cluster_id, reason, json_dumps(details), iso()),
        )
        return rid

    def insert_editorial(self, candidate_id: str, cluster_id: str | None, content: dict[str, Any],
                         status: str = "pending_review") -> tuple[str, bool]:
        content_hash = sha256_text(json_dumps({
            "urdu_headline": content["urdu_headline"], "urdu_bullets": content["urdu_bullets"],
            "roman_headline": content["roman_headline"], "roman_bullets": content["roman_bullets"],
        }))
        existing = self.one("SELECT id FROM editorials WHERE content_hash=?", (content_hash,))
        if existing:
            return existing["id"], False
        eid = uuid.uuid4().hex
        self.execute(
            """INSERT INTO editorials
               (id,candidate_id,cluster_id,urdu_headline,urdu_bullets_json,roman_headline,roman_bullets_json,
                verification_notes_json,source_urls_json,publication_time,media_json,content_hash,status,created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                eid, candidate_id, cluster_id, content["urdu_headline"], json_dumps(content["urdu_bullets"]),
                content["roman_headline"], json_dumps(content["roman_bullets"]),
                json_dumps(content.get("verification_notes", [])), json_dumps(content.get("source_urls", [])),
                content.get("publication_time"), json_dumps(content.get("media")) if content.get("media") else None,
                content_hash, status, iso(),
            ),
        )
        return eid, True

    def set_editorial_status(self, eid: str, status: str, approved_by: str | None = None) -> None:
        self.execute("UPDATE editorials SET status=?,approved_by=COALESCE(?,approved_by) WHERE id=?", (status, approved_by, eid))

    def get_editorial(self, eid: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM editorials WHERE id=?", (eid,))

    def insert_card(self, editorial_id: str, file_path: str, digest: str, width: int, height: int,
                    page_number: int, page_count: int, visual_status: str) -> str:
        cid = uuid.uuid4().hex
        self.execute(
            "INSERT INTO cards(id,editorial_id,file_path,sha256,width,height,page_number,page_count,visual_status,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (cid, editorial_id, file_path, digest, width, height, page_number, page_count, visual_status, iso()),
        )
        return cid

    def cards(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query(
            "SELECT c.*,e.urdu_headline,e.status AS editorial_status FROM cards c "
            "JOIN editorials e ON e.id=c.editorial_id ORDER BY c.created_at DESC LIMIT ?", (limit,),
        )

    def insert_api_event(self, subsystem: str, action: str, status: str, message: str,
                         http_status: int | None = None, error_code: str | None = None,
                         details: dict[str, Any] | None = None) -> None:
        self.execute(
            "INSERT INTO api_events(id,subsystem,action,status,http_status,error_code,message,details_json,created_at) VALUES(?,?,?,?,?,?,?,?,?)",
            (uuid.uuid4().hex, subsystem, action, status, http_status, error_code, message[:2000],
             json_dumps(details or {}), iso()),
        )

    def api_events(self, limit: int = 100) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM api_events ORDER BY created_at DESC LIMIT ?", (limit,))

    def enqueue_job(self, job_type: str, payload: dict[str, Any], dedupe_key: str | None = None,
                    delay_seconds: int = 0) -> str | None:
        if dedupe_key:
            row = self.one("SELECT id FROM jobs WHERE dedupe_key=? AND status IN ('queued','running')", (dedupe_key,))
            if row:
                return None
        jid = uuid.uuid4().hex
        from datetime import timedelta
        run_at = iso(utcnow() + timedelta(seconds=delay_seconds))
        self.execute(
            "INSERT INTO jobs(id,type,dedupe_key,payload_json,status,run_at) VALUES(?,?,?,?,'queued',?)",
            (jid, job_type, dedupe_key, json_dumps(payload), run_at),
        )
        return jid

    def claim_job(self) -> dict[str, Any] | None:
        with self._lock, self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='queued' AND run_at<=? ORDER BY run_at LIMIT 1", (iso(),)
            ).fetchone()
            if not row:
                return None
            conn.execute("UPDATE jobs SET status='running',locked_at=?,started_at=?,attempts=attempts+1 WHERE id=?",
                         (iso(), iso(), row["id"]))
            return dict(row)

    def finish_job(self, jid: str, error: str | None = None) -> None:
        self.execute("UPDATE jobs SET status=?,completed_at=?,error=? WHERE id=?",
                     ("failed" if error else "done", iso(), error, jid))

    def publish_attempt(self, idempotency_key: str) -> dict[str, Any] | None:
        return self.one("SELECT * FROM publish_attempts WHERE idempotency_key=?", (idempotency_key,))

    def create_publish_attempt(self, *, idempotency_key: str, editorial_id: str, card_id: str,
                               target_page_id: str | None, caption_hash: str, status: str = "planned") -> str:
        pid = uuid.uuid4().hex
        now = iso()
        self.execute(
            """INSERT INTO publish_attempts
               (id,idempotency_key,editorial_id,card_id,target_page_id,caption_hash,status,attempts,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,0,?,?)""",
            (pid, idempotency_key, editorial_id, card_id, target_page_id, caption_hash, status, now, now),
        )
        return pid

    def update_publish_attempt(self, pid: str, **fields: Any) -> None:
        allowed = {"status", "attempts", "request_started_at", "response_at", "post_id", "photo_id", "last_error", "target_page_id"}
        pairs = [(k, v) for k, v in fields.items() if k in allowed]
        if not pairs:
            return
        sql = "UPDATE publish_attempts SET " + ",".join(f"{k}=?" for k, _ in pairs) + ",updated_at=? WHERE id=?"
        self.execute(sql, tuple(v for _, v in pairs) + (iso(), pid))

    def publish_attempts(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.query("SELECT * FROM publish_attempts ORDER BY created_at DESC LIMIT ?", (limit,))

    def status_summary(self) -> dict[str, Any]:
        def count(sql: str, params: tuple[Any, ...] = ()) -> int:
            row = self.one(sql, params)
            return int(next(iter(row.values()))) if row else 0
        return {
            "sources": self.source_statuses(),
            "candidates": count("SELECT COUNT(*) AS n FROM candidates"),
            "review_open": count("SELECT COUNT(*) AS n FROM review_queue WHERE status='open'"),
            "editorials": count("SELECT COUNT(*) AS n FROM editorials"),
            "cards": count("SELECT COUNT(*) AS n FROM cards"),
            "publish_success": count("SELECT COUNT(*) AS n FROM publish_attempts WHERE status='success'"),
            "publish_failed": count("SELECT COUNT(*) AS n FROM publish_attempts WHERE status IN ('failed','unknown')"),
            "jobs_queued": count("SELECT COUNT(*) AS n FROM jobs WHERE status='queued'"),
            "jobs_running": count("SELECT COUNT(*) AS n FROM jobs WHERE status='running'"),
        }
