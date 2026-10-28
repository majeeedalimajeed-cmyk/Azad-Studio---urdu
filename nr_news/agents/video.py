"""Video/TTS workflow. It never blocks the card workflow.

``prepare`` writes reviewable Urdu/Roman scripts, bilingual subtitles and a
1080x1920 storyboard for every published story. Actual YouTube Shorts are
produced by :meth:`VideoWorkflow.shorts_cycle`, which runs on the operator
schedule: the best 3 stories at 09:00 IST, the best 3 at 19:00 IST, and one
slot of the daily 6 reserved for an immediate breaking-news Short with full
details. Every narration opens with the Urdu welcome for AZAD STUDIO.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import CONFIG
from ..db import Database
from ..models import Candidate, EditorialContent
from ..util import iso, json_dumps, parse_iso, redact

IST = timezone(timedelta(hours=5, minutes=30), "IST")


def _srt_time(seconds: float) -> str:
    ms = int(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _loads(value: Any, default: Any) -> Any:
    if not value:
        return default
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


class VideoWorkflow:
    def __init__(self, db: Database | None = None, config=CONFIG):
        self.db = db or Database(config.db_path)
        self.config = config

    # ------------------------------------------------------------------
    # Per-story preparation (scripts/storyboard only; no upload here).
    # ------------------------------------------------------------------
    def prepare(self, editorial_id: str, content: EditorialContent) -> dict[str, Any]:
        out_dir = self.config.data_dir / "video" / editorial_id[:12]
        out_dir.mkdir(parents=True, exist_ok=True)
        urdu_lines = [content.urdu_headline] + content.urdu_bullets
        roman_lines = [content.roman_headline] + content.roman_bullets
        urdu_script = "\n".join(urdu_lines)
        roman_script = "\n".join(roman_lines)
        (out_dir / "voiceover_urdu.txt").write_text(urdu_script, encoding="utf-8")
        (out_dir / "voiceover_roman_urdu.txt").write_text(roman_script, encoding="utf-8")

        # Bilingual subtitles, one segment per corresponding line.
        srt_parts: list[str] = []
        cursor = 0.0
        for idx, (urdu, roman) in enumerate(zip(urdu_lines, roman_lines), 1):
            duration = 4.0 + min(3.0, len(urdu) / 45)
            srt_parts.append(f"{idx}\n{_srt_time(cursor)} --> {_srt_time(cursor + duration)}\n{urdu}\n{roman}\n")
            cursor += duration
        (out_dir / "subtitles_bilingual.srt").write_text("\n".join(srt_parts), encoding="utf-8")

        storyboard = {
            "layout": "1080x1920 vertical",
            "duration_seconds_estimate": cursor,
            "background": "rendered AZAD STUDIO news card",
            "visuals": "approved licensed photograph only; otherwise card artwork",
            "subtitle_file": "subtitles_bilingual.srt",
            "narration": "Urdu only; opens with the AZAD STUDIO welcome",
            "tts_status": f"edge-tts {self.config.tts_voice} (Urdu only)",
            "music": "none; add licensed track only after rights review",
            "publication_ready": False,
            "shorts_schedule": "best 3 at 09:00 IST, best 3 at 19:00 IST, "
                               "1 of 6 reserved for immediate breaking news",
        }
        (out_dir / "storyboard.json").write_text(json_dumps(storyboard), encoding="utf-8")
        self.db.insert_api_event("video", "prepare", "review",
                                 "Video scripts and bilingual subtitles prepared; "
                                 "Short scheduled in the 09:00/19:00 IST batches.",
                                 details={"editorial_id": editorial_id, "directory": str(out_dir)})
        return {"directory": str(out_dir), **storyboard}

    # ------------------------------------------------------------------
    # Shorts scheduler
    # ------------------------------------------------------------------
    @staticmethod
    def editorial_content(row: dict[str, Any]) -> EditorialContent:
        return EditorialContent.from_dict({
            "urdu_headline": row["urdu_headline"],
            "urdu_bullets": _loads(row.get("urdu_bullets_json"), []),
            "roman_headline": row["roman_headline"],
            "roman_bullets": _loads(row.get("roman_bullets_json"), []),
            "verification_notes": _loads(row.get("verification_notes_json"), []),
            "source_urls": _loads(row.get("source_urls_json"), []),
            "publication_time": row.get("publication_time"),
            "media": _loads(row.get("media_json"), None),
        })

    def _story_dt(self, row: dict[str, Any]) -> datetime | None:
        return parse_iso(row.get("publication_time") or row.get("created_at"))

    def _age_hours(self, row: dict[str, Any], now: datetime) -> float:
        dt = self._story_dt(row)
        if dt is None:
            return 999.0
        return max(0.0, (now - dt).total_seconds() / 3600.0)

    def _score(self, row: dict[str, Any], now: datetime) -> float:
        """Best-news score: editorial priority, recency, photos, detail."""
        from .research import ResearchAgent

        base = 1
        candidate_row = self.db.get_candidate(row["candidate_id"])
        if candidate_row:
            candidate = Candidate(
                source=candidate_row.get("source") or "",
                publisher=candidate_row.get("publisher") or "",
                url=candidate_row.get("url") or "",
                original_headline=candidate_row.get("original_headline") or "",
                published_at=candidate_row.get("published_at"),
                updated_at=candidate_row.get("updated_at"),
                language=candidate_row.get("language") or "",
                body_excerpt=candidate_row.get("body_excerpt") or "",
                category=candidate_row.get("category") or "",
            )
            base = ResearchAgent.priority(candidate)
        score = base * 10.0
        score += max(0.0, 24.0 - self._age_hours(row, now))
        if row.get("media_json") not in (None, "", "null", "[]"):
            score += 4.0
        if len(_loads(row.get("urdu_bullets_json"), [])) >= 4:
            score += 2.0
        return score

    def _eligible(self, now: datetime) -> list[dict[str, Any]]:
        rows = self.db.query(
            "SELECT * FROM editorials WHERE status='published' "
            "ORDER BY created_at DESC LIMIT 300")
        out = []
        for row in rows:
            if self.db.get_setting(f"youtube_video_for_{row['id']}"):
                continue
            if self._age_hours(row, now) > self.config.shorts_lookback_hours:
                continue
            out.append(row)
        return out

    def _is_breaking(self, row: dict[str, Any]) -> bool:
        from ..shorts import detect_breaking

        candidate_row = self.db.get_candidate(row["candidate_id"]) or {}
        return detect_breaking(row.get("urdu_headline"), row.get("roman_headline"),
                               candidate_row.get("original_headline"))

    def _day_uploads(self, day_key: str) -> list[dict[str, Any]]:
        return _loads(self.db.get_setting(f"shorts_day_{day_key}"), [])

    def _save_day_uploads(self, day_key: str, entries: list[dict[str, Any]]) -> None:
        self.db.set_setting(f"shorts_day_{day_key}", json_dumps(entries))

    def _publish_short(self, row: dict[str, Any], kind: str,
                       now: datetime) -> dict[str, Any]:
        """Render the card, build the Short and upload it. Caller records it."""
        from ..shorts import (ShortUnavailable, YouTubeClient, build_short,
                              youtube_description, youtube_title)
        from .card import CardAgent

        client = YouTubeClient(config=self.config)
        if not client.available:
            raise ShortUnavailable("YouTube OAuth credentials are not configured")
        content = self.editorial_content(row)
        cards = CardAgent(self.db, config=self.config).render(
            row["id"], content, media=content.media)
        if not cards:
            raise ShortUnavailable("Card render failed for the Short")
        out_dir = self.config.data_dir / "video" / row["id"][:12]
        built = build_short(Path(cards[0].path), content, out_dir,
                            config=self.config, full_details=(kind == "breaking"))
        uploaded = client.upload_short(
            Path(built["video_path"]),
            title=youtube_title(content),
            description=youtube_description(content))
        entry = {"editorial_id": row["id"], "kind": kind,
                 "video_id": uploaded["video_id"], "url": uploaded["url"],
                 "at": iso(now)}
        self.db.insert_api_event(
            "youtube", "upload", "success",
            f"YouTube Short published ({kind}).", http_status=200,
            details={**entry, "headline": content.roman_headline})
        return entry

    def _run_batch(self, rows: list[dict[str, Any]], kind: str, take: int,
                   day_key: str, uploads: list[dict[str, Any]],
                   summary: dict[str, Any]) -> None:
        for row in rows[:take]:
            try:
                entry = self._publish_short(row, kind, summary["_now"])
            except Exception as exc:  # one bad story must not stop the batch
                message = redact(f"{type(exc).__name__}: {exc}",
                                 self.config.secret_values)
                self.db.insert_api_event("youtube", "upload", "error", message,
                                         details={"editorial_id": row["id"], "kind": kind})
                summary["errors"].append({"editorial_id": row["id"], "kind": kind,
                                          "error": message})
                continue
            self.db.set_setting(f"youtube_video_for_{row['id']}", entry["video_id"])
            uploads.append(entry)
            self._save_day_uploads(day_key, uploads)
            summary["uploaded"].append(entry)

    def shorts_cycle(self, now: datetime | None = None) -> dict[str, Any]:
        """Run one scheduler tick: breaking first, then due IST batches."""
        now = now or datetime.now(timezone.utc)
        ist = now.astimezone(IST)
        day_key = ist.date().isoformat()
        summary: dict[str, Any] = {
            "status": "ok", "day_ist": day_key, "ist": ist.isoformat(),
            "uploaded": [], "errors": [], "_now": now,
        }
        if not (self.config.video_enabled and self.config.youtube_enabled):
            return {"status": "disabled",
                    "reason": "NR_VIDEO_ENABLED/NR_YOUTUBE_ENABLED is off"}
        if self.config.dry_run:
            return {"status": "disabled", "reason": "dry run"}

        uploads = self._day_uploads(day_key)
        summary["already_today"] = len(uploads)
        kinds_done = {e.get("kind") for e in uploads}
        target = self.config.shorts_daily_target

        # 1) Reserved breaking slot: freshest urgent story goes immediately,
        #    narrated with full details. A late breaking story (after both
        #    batches) still posts as an extra, per operator policy.
        if "breaking" not in kinds_done:
            fresh = [r for r in self._eligible(now)
                     if self._is_breaking(r)
                     and self._age_hours(r, now) <= self.config.shorts_breaking_window_hours]
            if fresh:
                fresh.sort(key=lambda r: self._story_dt(r) or now, reverse=True)
                self._run_batch(fresh[:1], "breaking", 1, day_key, uploads, summary)
                kinds_done = {e.get("kind") for e in uploads}

        # 2) Morning batch at/after 09:00 IST: best 3.
        morning_flag = f"shorts_batch_{day_key}_morning"
        if ist.hour >= self.config.shorts_morning_hour and not self.db.get_setting(morning_flag):
            take = min(self.config.shorts_batch_size, target - len(uploads))
            if take > 0:
                ranked = sorted(self._eligible(now),
                                key=lambda r: self._score(r, now), reverse=True)
                self._run_batch(ranked, "morning", take, day_key, uploads, summary)
            self.db.set_setting(morning_flag, "1")

        # 3) Evening batch at/after 19:00 IST: fills the day to 6, including
        #    the reserved breaking slot when no breaking news happened.
        evening_flag = f"shorts_batch_{day_key}_evening"
        if ist.hour >= self.config.shorts_evening_hour and not self.db.get_setting(evening_flag):
            take = target - len(uploads)
            if take > 0:
                ranked = sorted(self._eligible(now),
                                key=lambda r: self._score(r, now), reverse=True)
                self._run_batch(ranked, "evening", take, day_key, uploads, summary)
            self.db.set_setting(evening_flag, "1")

        summary.pop("_now", None)
        summary["total_today"] = len(uploads)
        summary["kinds_today"] = sorted(k for k in
                                        {e.get("kind") for e in uploads} if k)
        return summary
