"""Optional video/TTS scaffolding. It never blocks the card workflow.

Without a configured Urdu TTS provider and ffmpeg, this writes reviewable
scripts, subtitles and a 1080x1920 storyboard but produces no fabricated audio
or video. The Roman Urdu version is produced from the approved Urdu copy by
Agent C; this workflow does not create a second translation path.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..config import CONFIG
from ..db import Database
from ..models import EditorialContent
from ..util import iso, json_dumps, sha256_text


def _srt_time(seconds: float) -> str:
    ms = int(seconds * 1000)
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


class VideoWorkflow:
    def __init__(self, db: Database | None = None, config=CONFIG):
        self.db = db or Database(config.db_path)
        self.config = config

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
            "narration": "Urdu only by default; Urdu followed by Roman Urdu is supported",
            "tts_status": f"edge-tts {self.config.tts_voice} (Urdu only)",
            "music": "none; add licensed track only after rights review",
            "publication_ready": False,
        }
        (out_dir / "storyboard.json").write_text(json_dumps(storyboard), encoding="utf-8")
        self.db.insert_api_event("video", "prepare", "review",
                                 "Video scripts and bilingual subtitles prepared; no audio/video published.",
                                 details={"editorial_id": editorial_id, "directory": str(out_dir)})
        result: dict[str, Any] = {"directory": str(out_dir), **storyboard}

        # Build the actual Short (Urdu-only male TTS) from the rendered card,
        # and upload it to YouTube when the story is live and OAuth is set.
        try:
            from ..shorts import (ShortUnavailable, YouTubeClient, build_short,
                                  youtube_description, youtube_title)
            card = self.db.one(
                "SELECT * FROM cards WHERE editorial_id=? AND visual_status='passed' "
                "ORDER BY page_number LIMIT 1", (editorial_id,))
            if card and Path(card["file_path"]).exists():
                built = build_short(Path(card["file_path"]), content, out_dir,
                                    config=self.config)
                result.update({"video_path": built["video_path"],
                               "duration_seconds": built["duration_seconds"],
                               "voice": built["voice"],
                               "publication_ready": True})
                self.db.insert_api_event(
                    "video", "build_short", "ok",
                    "1080x1920 Short built with Urdu-only narration.",
                    details={"editorial_id": editorial_id, **built})
                editorial = self.db.get_editorial(editorial_id) or {}
                youtube = YouTubeClient(config=self.config)
                if (editorial.get("status") == "published" and youtube.available
                        and self.config.youtube_enabled and not self.config.dry_run):
                    if not self.db.get_setting(f"youtube_video_for_{editorial_id}"):
                        uploaded = youtube.upload_short(
                            Path(built["video_path"]),
                            title=youtube_title(content),
                            description=youtube_description(content),
                        )
                        self.db.set_setting(
                            f"youtube_video_for_{editorial_id}", uploaded["video_id"])
                        self.db.insert_api_event(
                            "youtube", "upload", "success",
                            "YouTube Short published.", http_status=200,
                            details={"editorial_id": editorial_id, **uploaded})
                        result["youtube"] = {"status": "success", **uploaded}
                    else:
                        result["youtube"] = {"status": "already_uploaded"}
            else:
                result["short_status"] = "no_card_available"
        except Exception as exc:  # video must never block card publishing
            from ..shorts import ShortUnavailable  # noqa: F401
            message = f"{type(exc).__name__}: {exc}"
            self.db.insert_api_event("video", "build_short", "error", message,
                                     details={"editorial_id": editorial_id})
            result["short_error"] = message
        return result
