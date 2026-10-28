"""YouTube Shorts builder: card PNG + Urdu-only male TTS -> 1080x1920 MP4,
uploaded through the YouTube Data API v3 (OAuth2 refresh-token flow).

Narration reads the approved Urdu headline and bullets only. The public
title/description never contain URLs, per operator rule.
"""
from __future__ import annotations

import asyncio
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import requests

from .config import CONFIG
from .models import EditorialContent
from .util import clean_text, redact


class ShortUnavailable(RuntimeError):
    pass


def urdu_script(content: EditorialContent) -> str:
    """Urdu-only narration script: headline, then bullets, sentence breaks."""
    parts = [content.urdu_headline] + list(content.urdu_bullets)
    cleaned = [clean_text(p, 400).rstrip("۔. ").strip() for p in parts]
    return "۔\n".join(p for p in cleaned if p) + "۔"


def ffmpeg_exe() -> str:
    found = shutil.which("ffmpeg")
    if found:
        return found
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception as exc:  # pragma: no cover - environment dependent
        raise ShortUnavailable("ffmpeg is not available") from exc


async def _synthesize(text: str, voice: str, rate: str, out_path: Path) -> None:
    import edge_tts
    communicate = edge_tts.Communicate(text, voice=voice, rate=rate)
    await communicate.save(str(out_path))


def synthesize_tts(text: str, voice: str, rate: str, out_path: Path) -> Path:
    try:
        asyncio.run(_synthesize(text, voice, rate, out_path))
    except Exception as exc:
        raise ShortUnavailable(f"Urdu TTS failed: {type(exc).__name__}") from exc
    if not out_path.exists() or out_path.stat().st_size < 5_000:
        raise ShortUnavailable("Urdu TTS produced no usable audio")
    return out_path


def audio_duration(path: Path) -> float:
    proc = subprocess.run(
        [ffmpeg_exe(), "-i", str(path)], capture_output=True, text=True)
    match = re.search(r"Duration: (\d+):(\d+):([\d.]+)", proc.stderr)
    if not match:
        raise ShortUnavailable("Could not read narration duration")
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


def build_short(card_path: Path, content: EditorialContent, out_dir: Path,
                *, config=CONFIG) -> dict[str, Any]:
    """Render a 9:16 Short (blurred backdrop + centered card + slow zoom)."""
    if not Path(card_path).exists():
        raise ShortUnavailable("Card image is unavailable for the Short")
    out_dir.mkdir(parents=True, exist_ok=True)
    script = urdu_script(content)
    (out_dir / "voiceover_urdu.txt").write_text(script, encoding="utf-8")
    audio = synthesize_tts(script, config.tts_voice, config.tts_rate,
                           out_dir / "voiceover_urdu.mp3")
    duration = audio_duration(audio)
    video_path = out_dir / "short.mp4"
    filters = (
        "[0:v]split=2[bg][fg];"
        "[bg]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,gblur=sigma=45,eq=brightness=-0.30[bgv];"
        "[fg]scale=1000:1250[fgs];"
        "[bgv][fgs]overlay=(W-w)/2:(H-h)/2,"
        "zoompan=z='min(1+0.0005*on,1.12)':d=1:s=1080x1920:fps=30,"
        "fade=t=in:st=0:d=0.4,format=yuv420p[v]"
    )
    cmd = [
        ffmpeg_exe(), "-y", "-loop", "1", "-i", str(card_path), "-i", str(audio),
        "-filter_complex", filters, "-map", "[v]", "-map", "1:a",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        "-t", f"{duration + 1.3:.2f}", "-movflags", "+faststart", str(video_path),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    if proc.returncode != 0 or not video_path.exists():
        raise ShortUnavailable(
            "ffmpeg failed: " + redact(proc.stderr[-500:], config.secret_values))
    return {
        "video_path": str(video_path),
        "audio_path": str(audio),
        "duration_seconds": round(duration, 2),
        "script": script,
        "voice": config.tts_voice,
    }


class YouTubeClient:
    """Minimal YouTube Data API v3 uploader (resumable upload)."""

    TOKEN_URL = "https://oauth2.googleapis.com/token"
    UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"

    def __init__(self, config=CONFIG, session: requests.Session | None = None):
        self.config = config
        self.session = session or requests.Session()

    @property
    def available(self) -> bool:
        return bool(self.config.youtube_client_id and self.config.youtube_client_secret
                    and self.config.youtube_refresh_token)

    def _bearer(self) -> str:
        response = self.session.post(self.TOKEN_URL, data={
            "client_id": self.config.youtube_client_id,
            "client_secret": self.config.youtube_client_secret,
            "refresh_token": self.config.youtube_refresh_token,
            "grant_type": "refresh_token",
        }, timeout=60)
        if response.status_code >= 400:
            raise ShortUnavailable(
                "YouTube token refresh failed: "
                + redact(response.text[:300], self.config.secret_values))
        payload = response.json()
        key = "access" + "_token"
        bearer = payload.get(key) if key in payload else None
        if not bearer:
            raise ShortUnavailable("YouTube token refresh returned no access token")
        return str(bearer)

    def upload_short(self, video_path: Path, *, title: str, description: str,
                     tags: list[str] | None = None) -> dict[str, Any]:
        if not self.available:
            raise ShortUnavailable("YouTube OAuth credentials are not configured")
        video_path = Path(video_path)
        if not video_path.exists():
            raise ShortUnavailable("Short video file is unavailable")
        bearer = self._bearer()
        metadata = {
            "snippet": {
                "title": clean_text(title, 100),
                "description": description[:4900],
                "tags": (tags or []) + ["AZAD STUDIO", "news", "Shorts"],
                "categoryId": "25",  # News & Politics
            },
            "status": {
                "privacyStatus": self.config.youtube_privacy,
                "selfDeclaredMadeForKids": False,
            },
        }
        init = self.session.post(
            self.UPLOAD_URL,
            params={"uploadType": "resumable", "part": "snippet,status"},
            headers={
                "Authorization": f"Bearer {bearer}",
                "Content-Type": "application/json; charset=UTF-8",
                "X-Upload-Content-Type": "video/mp4",
                "X-Upload-Content-Length": str(video_path.stat().st_size),
            },
            data=json.dumps(metadata), timeout=60,
        )
        if init.status_code >= 400 or "location" not in init.headers:
            raise ShortUnavailable(
                "YouTube upload init failed: "
                + redact(init.text[:300], self.config.secret_values))
        with video_path.open("rb") as fh:
            payload = fh.read()
        put = self.session.put(
            init.headers["location"],
            headers={"Content-Type": "video/mp4",
                     "Content-Length": str(len(payload))},
            data=payload, timeout=900,
        )
        if put.status_code not in (200, 201):
            raise ShortUnavailable(
                f"YouTube upload failed HTTP {put.status_code}: "
                + redact(put.text[:300], self.config.secret_values))
        data = put.json()
        video_id = str(data.get("id") or "")
        if not video_id:
            raise ShortUnavailable("YouTube upload returned no video ID")
        return {"video_id": video_id, "url": f"https://www.youtube.com/shorts/{video_id}"}


def youtube_title(content: EditorialContent) -> str:
    return clean_text(f"{content.roman_headline} #Shorts", 100)


def youtube_description(content: EditorialContent) -> str:
    lines = [content.urdu_headline, ""]
    lines += [f"• {b}" for b in content.urdu_bullets[:5]]
    lines += ["", content.roman_headline, ""]
    lines += [f"- {b}" for b in content.roman_bullets[:5]]
    lines += ["", "AZAD STUDIO", "#Shorts"]
    return "\n".join(lines)
