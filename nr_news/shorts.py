"""YouTube Shorts builder: broadcast-style synced slides + Urdu-only male
TTS -> 1080x1920 MP4, uploaded through the YouTube Data API v3
(OAuth2 refresh-token flow). News cards are NOT used in the video; each
narration segment gets its own timed slide and headline ticker.

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


WELCOME_URDU = "آزاد اسٹوڈیو میں خوش آمدید"
TODAYS_NEWS_URDU = "آج کی تازہ ترین خبریں"
BREAKING_URDU = "بریکنگ نیوز"

# Urgency signals for the reserved breaking-news Short (headline scan).
BREAKING_MARKERS = (
    "بریکنگ", "فوری خبر", "بڑی خبر", "فوری",
    "breaking", "just in", "urgent", "flash",
)


def detect_breaking(*texts: str | None) -> bool:
    """True when any supplied headline text carries an urgency signal."""
    blob = " ".join(t for t in texts if t).lower()
    return any(marker in blob for marker in BREAKING_MARKERS)


def narration_segments(content: EditorialContent, *, full_details: bool = False) -> list[dict[str, Any]]:
    """Ordered narration segments; each becomes one timed slide + voice clip.

    Segments: welcome -> today's-news intro -> [breaking announcement] ->
    headline -> bullets. Scheduled Shorts read the first three bullets; a
    breaking Short reads every bullet (full details).
    """
    bullets = list(content.urdu_bullets)
    if not full_details:
        bullets = bullets[:3]
    segments: list[dict[str, Any]] = [
        {"kind": "welcome", "text": WELCOME_URDU},
        {"kind": "latest", "text": TODAYS_NEWS_URDU},
    ]
    if full_details:
        segments.append({"kind": "breaking", "text": BREAKING_URDU})
    segments.append({"kind": "headline", "text": content.urdu_headline})
    for idx, bullet in enumerate(bullets, 1):
        segments.append({"kind": "bullet", "index": idx,
                         "total": len(bullets), "text": bullet})
    cleaned: list[dict[str, Any]] = []
    for seg in segments:
        text = clean_text(seg["text"], 400).rstrip("۔. ").strip()
        if text:
            cleaned.append({**seg, "text": text})
    return cleaned


def urdu_script(content: EditorialContent, *, full_details: bool = False) -> str:
    """Urdu-only narration script: welcome, today's-news intro, headline,
    then bullets, sentence breaks. Breaking (full_details) reads all bullets.
    """
    parts = [seg["text"] for seg in narration_segments(content, full_details=full_details)]
    return "۔\n".join(parts) + "۔"


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


def _run_ffmpeg(cmd: list[str], config=CONFIG) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=600)


SLIDE_W, SLIDE_H = 1080, 1920
SEGMENT_GAP_SECONDS = 0.30
_FPS = 30


def render_slide(content: EditorialContent, segment: dict[str, Any],
                 out_path: Path, *, config=CONFIG) -> Path:
    """Render one 1080x1920 broadcast slide for a narration segment.

    Premium dark newsroom look: brand bar with the exact monogram, headline
    context kept on screen, the active bullet highlighted in a gold-framed
    card with progress dots, and a red-label ticker bar along the bottom.
    """
    from PIL import Image, ImageDraw

    from .renderer import CardRenderer, gradient, isolate_latin, load_logo, proportional_logo

    renderer = CardRenderer(config)
    logo = load_logo()
    im = gradient((SLIDE_W, SLIDE_H), (8, 13, 28), (3, 4, 8))
    d = ImageDraw.Draw(im, "RGBA")

    def rtl(text, size, *, weight="Bold", fill=(248, 247, 242), xy=(0, 0),
            anchor="ra", max_width=920, max_lines=4, line_factor=1.65):
        fnt = renderer._font(True, size, weight)
        lines = renderer.wrap(d, text, fnt, max_width, True)
        while len(lines) > max_lines and size > 24:
            size -= 2
            fnt = renderer._font(True, size, weight)
            lines = renderer.wrap(d, text, fnt, max_width, True)
        lh = size * line_factor
        for i, line in enumerate(lines):
            d.text((xy[0], xy[1] + i * lh), isolate_latin(line), font=fnt, fill=fill,
                   anchor=anchor, direction="rtl", language="ur")
        return lines, fnt, lh

    # --- Brand bar ------------------------------------------------------
    d.rectangle((0, 0, SLIDE_W, 150), fill=(6, 8, 16))
    d.rectangle((0, 150, SLIDE_W, 154), fill=(226, 184, 74))
    scaled = proportional_logo(logo, 104)
    im.paste(scaled, (44, 23), scaled)
    brand_f = renderer._font(False, 42, "Bold")
    d.text((44 + scaled.width + 26, 75), "AZAD STUDIO", font=brand_f,
           fill=(255, 231, 151), anchor="lm")
    d.rounded_rectangle((SLIDE_W - 292, 44, SLIDE_W - 44, 106), radius=31,
                        fill=(190, 24, 35))
    latest_f = renderer._font(False, 32, "Bold")
    d.text((SLIDE_W - 168, 75), "LATEST", font=latest_f, fill=(255, 255, 255),
           anchor="mm")

    def photo_band() -> None:
        """Relevant rights-cleared story visual; calm branded fallback.

        Only the photo itself is shown (credits stay in the hidden audit,
        per operator rule). Missing files fall back without breaking.
        """
        from PIL import Image, ImageOps

        from .renderer import normalize_media

        used = False
        for item in normalize_media(content.media, 2):
            lp = item.get("local_path")
            if lp and Path(lp).exists():
                try:
                    with Image.open(lp) as src:
                        fitted = ImageOps.fit(src.convert("RGB"), (SLIDE_W, 446))
                    im.paste(fitted, (0, 154))
                    used = True
                    break
                except Exception:
                    continue
        if not used:
            d.rectangle((0, 154, SLIDE_W, 600), fill=(10, 15, 28))
            try:
                wm = proportional_logo(logo, 320)
                if wm.mode != "RGBA":
                    wm = wm.convert("RGBA")
                wm.putalpha(wm.split()[3].point(lambda a: int(a * 0.15)))
                im.paste(wm, (SLIDE_W - wm.width - 30, 210), wm)
            except Exception:
                pass
        # Fade the band's lower edge into the page for a clean join.
        fade = Image.new("L", (1, 110))
        for yy in range(110):
            fade.putpixel((0, yy), int(255 * yy / 109))
        im.paste(Image.new("RGB", (SLIDE_W, 110), (4, 6, 12)), (0, 490),
                 fade.resize((SLIDE_W, 110)))
        d.rectangle((0, 597, SLIDE_W, 600), fill=(226, 184, 74))

    # --- Segment body ---------------------------------------------------
    kind = segment["kind"]
    if kind == "welcome":
        from .renderer import paste_logo
        paste_logo(im, logo, SLIDE_W // 2, 430, 210)
        rtl(segment["text"], 62, fill=(255, 231, 151),
            xy=(SLIDE_W // 2, 830), anchor="mm", max_lines=2)
        rtl(TODAYS_NEWS_URDU, 42, fill=(248, 247, 242),
            xy=(SLIDE_W // 2, 990), anchor="mm", max_lines=2)
    elif kind == "latest":
        rtl(segment["text"], 66, fill=(248, 247, 242),
            xy=(SLIDE_W // 2, 860), anchor="mm", max_lines=2)
        sub_f = renderer._font(False, 30, "SemiBold")
        d.text((SLIDE_W // 2, 1000), "AZAD STUDIO", font=sub_f,
               fill=(226, 184, 74), anchor="mm")
    elif kind == "breaking":
        photo_band()
        d.rounded_rectangle((80, 680, SLIDE_W - 80, 900), radius=36,
                            fill=(190, 24, 35))
        rtl(segment["text"], 78, fill=(255, 255, 255),
            xy=(SLIDE_W // 2, 790), anchor="mm", max_lines=1)
        rtl(content.urdu_headline, 38, fill=(226, 214, 180),
            xy=(SLIDE_W - 80, 990), anchor="ra", max_width=920, max_lines=3)
    elif kind == "headline":
        photo_band()
        # Today's-news kicker, then the story headline large and readable.
        d.rectangle((SLIDE_W - 62, 646, SLIDE_W - 50, 682), fill=(190, 24, 35))
        rtl(TODAYS_NEWS_URDU, 30, fill=(226, 184, 74),
            xy=(SLIDE_W - 84, 650), anchor="ra", max_lines=1)
        rtl(segment["text"], 58, xy=(SLIDE_W - 80, 790), max_lines=4)
    else:  # bullet: photo + persistent headline + one highlighted bullet
        photo_band()
        rtl(content.urdu_headline, 36, fill=(228, 232, 242),
            xy=(SLIDE_W - 80, 648), max_lines=3)
        d.rounded_rectangle((56, 900, SLIDE_W - 56, 1500), radius=30,
                            fill=(13, 18, 32), outline=(226, 184, 74), width=3)
        d.rectangle((SLIDE_W - 84, 940, SLIDE_W - 70, 1460), fill=(226, 184, 74))
        badge_cx, badge_cy = SLIDE_W - 150, 992
        d.ellipse((badge_cx - 36, badge_cy - 36, badge_cx + 36, badge_cy + 36),
                  fill=(226, 184, 74))
        num_f = renderer._font(False, 38, "Bold")
        d.text((badge_cx, badge_cy + 2), str(segment["index"]), font=num_f,
               fill=(20, 14, 2), anchor="mm")
        # Fit at a large readable size, then centre inside the card.
        size = 48
        while True:
            fnt_b = renderer._font(True, size, "Bold")
            lines_b = renderer.wrap(d, segment["text"], fnt_b, SLIDE_W - 280, True)
            if len(lines_b) <= 6 or size <= 32:
                break
            size -= 2
        lh_b = size * 1.65
        y_text = 960 + max(0.0, (470 - len(lines_b) * lh_b) / 2)
        for i, line in enumerate(lines_b):
            d.text((SLIDE_W - 130, y_text + i * lh_b), isolate_latin(line),
                   font=fnt_b, fill=(248, 247, 242), anchor="ra",
                   direction="rtl", language="ur")
        total = int(segment.get("total") or 1)
        idx = int(segment.get("index") or 1)
        dots_w = total * 46 - 24
        x0 = (SLIDE_W - dots_w) // 2
        for i in range(1, total + 1):
            cx = x0 + (i - 1) * 46 + 11
            d.ellipse((cx - 11, 1579, cx + 11, 1601),
                      fill=(226, 184, 74) if i <= idx else (74, 82, 104))

    # --- Ticker bar (red label + scrolling strip are overlaid at build) --
    d.rectangle((0, 1690, SLIDE_W, 1816), fill=(2, 2, 4))
    d.rectangle((0, 1690, SLIDE_W, 1694), fill=(226, 184, 74))

    out_path.parent.mkdir(parents=True, exist_ok=True)
    im.save(out_path)
    return out_path


def build_short(content: EditorialContent, out_dir: Path,
                *, config=CONFIG, full_details: bool = False) -> dict[str, Any]:
    """Build a broadcast-style Short synced segment-by-segment to the TTS.

    Each narration segment is synthesised separately, timed, and paired with
    its own slide: welcome -> today's news -> headline -> highlighted
    bullets, changing exactly as the anchor moves on. A scrolling headline
    ticker runs along the bottom. Returns paths and per-segment timings.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    segments = narration_segments(content, full_details=full_details)
    script = urdu_script(content, full_details=full_details)
    (out_dir / "voiceover_urdu.txt").write_text(script, encoding="utf-8")

    seg_dir = out_dir / "segments"
    seg_dir.mkdir(parents=True, exist_ok=True)

    # Scrolling ticker strip + red label, rendered as images so the marquee
    # works on any ffmpeg build (no drawtext dependency). The label is
    # overlaid last, so the headline slides underneath it like a real ticker.
    from PIL import Image, ImageDraw, ImageFont

    from .renderer import font as _pil_font, isolate_latin

    ticker_text = clean_text(content.roman_headline, 200) or "AZAD STUDIO"
    strip_font = _pil_font(config.latin_font_path, 30, "Medium")
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    strip_w = int(probe.textlength(ticker_text, font=strip_font)) + 160
    strip = Image.new("RGBA", (strip_w, 64), (0, 0, 0, 0))
    ImageDraw.Draw(strip).text((80, 32), ticker_text, font=strip_font,
                               fill=(235, 238, 245, 255), anchor="lm")
    strip_path = seg_dir / "ticker_strip.png"
    strip.save(strip_path)
    label = Image.new("RGBA", (238, 126), (0, 0, 0, 0))
    ld = ImageDraw.Draw(label)
    ld.rectangle((0, 0, 238, 126), fill=(190, 24, 35, 255))
    urdu_f = ImageFont.truetype(str(config.urdu_font_path), 30)
    ld.text((119, 63), isolate_latin("تازہ ترین"), font=urdu_f,
            fill=(255, 255, 255, 255), anchor="mm", direction="rtl", language="ur")
    label_path = seg_dir / "ticker_label.png"
    label.save(label_path)

    clip_paths: list[Path] = []
    audio_parts: list[Path] = []
    timed: list[dict[str, Any]] = []
    elapsed = 0.0
    for i, seg in enumerate(segments):
        audio = synthesize_tts(seg["text"] + "۔", config.tts_voice, config.tts_rate,
                               seg_dir / f"seg_{i:02d}.mp3")
        seg_seconds = audio_duration(audio)
        clip_seconds = seg_seconds + SEGMENT_GAP_SECONDS
        slide = render_slide(content, seg, seg_dir / f"slide_{i:02d}.png", config=config)
        clip = seg_dir / f"clip_{i:02d}.mp4"
        fade_out = max(0.0, clip_seconds - 0.22)
        fc_full = (
            f"[0:v][1:v]overlay=x='1080-mod((t+{elapsed:.2f})*130\\,{1080 + strip_w})'"
            ":y=1721[t1];[t1][2:v]overlay=0:1690[t2];"
            f"[t2]fade=t=in:st=0:d=0.22,fade=t=out:st={fade_out:.2f}:d=0.22,"
            "format=yuv420p[v]"
        )
        vf_plain = (
            "scale=1080:1920,fade=t=in:st=0:d=0.22,"
            f"fade=t=out:st={fade_out:.2f}:d=0.22,format=yuv420p"
        )
        attempts = [
            [ffmpeg_exe(), "-y", "-loop", "1", "-i", str(slide),
             "-loop", "1", "-i", str(strip_path), "-loop", "1", "-i", str(label_path),
             "-i", str(audio), "-filter_complex", fc_full,
             "-af", f"apad,atrim=0:{clip_seconds:.2f}",
             "-map", "[v]", "-map", "3:a"],
            [ffmpeg_exe(), "-y", "-loop", "1", "-i", str(slide), "-i", str(audio),
             "-vf", vf_plain, "-af", f"apad,atrim=0:{clip_seconds:.2f}",
             "-map", "0:v", "-map", "1:a"],
        ]
        built_clip = False
        for head in attempts:
            cmd = head + [
                "-c:v", "libx264", "-preset", "medium", "-crf", "20",
                "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
                "-t", f"{clip_seconds:.2f}", str(clip),
            ]
            proc = _run_ffmpeg(cmd, config)
            if proc.returncode == 0 and clip.exists():
                built_clip = True
                break
        if not built_clip:
            raise ShortUnavailable(
                "ffmpeg segment failed: "
                + redact(proc.stderr[-500:], config.secret_values))
        clip_paths.append(clip)
        audio_parts.append(audio)
        timed.append({**seg, "seconds": round(seg_seconds, 2),
                      "starts_at": round(elapsed, 2)})
        elapsed += clip_seconds

    # Concatenate segment clips into the final Short.
    list_file = seg_dir / "clips.txt"
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in clip_paths),
                         encoding="utf-8")
    video_path = out_dir / "short.mp4"
    proc = _run_ffmpeg([
        ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
        "-c", "copy", "-movflags", "+faststart", str(video_path)], config)
    if proc.returncode != 0 or not video_path.exists():
        raise ShortUnavailable(
            "ffmpeg concat failed: " + redact(proc.stderr[-500:], config.secret_values))

    # Combined narration track for audit (best effort).
    combined = out_dir / "voiceover_urdu.mp3"
    try:
        silence = seg_dir / "silence.mp3"
        _run_ffmpeg([ffmpeg_exe(), "-y", "-f", "lavfi", "-i",
                     "anullsrc=r=44100:cl=stereo", "-t", str(SEGMENT_GAP_SECONDS),
                     "-c:a", "libmp3lame", "-q:a", "4", str(silence)], config)
        parts: list[Path] = []
        for j, audio in enumerate(audio_parts):
            parts.append(audio)
            if j < len(audio_parts) - 1:
                parts.append(silence)
        alist = seg_dir / "audio.txt"
        alist.write_text("".join(f"file '{p.resolve()}'\n" for p in parts),
                         encoding="utf-8")
        _run_ffmpeg([ffmpeg_exe(), "-y", "-f", "concat", "-safe", "0", "-i",
                     str(alist), "-c:a", "libmp3lame", "-q:a", "3", str(combined)],
                    config)
    except Exception:
        combined = audio_parts[0] if audio_parts else combined

    return {
        "video_path": str(video_path),
        "audio_path": str(combined),
        "duration_seconds": round(elapsed, 2),
        "script": script,
        "voice": config.tts_voice,
        "segments": timed,
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
