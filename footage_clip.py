"""Build an AZAD STUDIO real-footage political package (9:16).

Takes a RIGHTS-CLEARED local footage file and an exact clip window.
The politician's original audio is preserved untouched — no AI
voiceover is ever added over speech. Output: vertical 1080x1920 with
the speaker visible on a blurred-pad frame, subtle AZAD STUDIO
branding, an Urdu headline, the speaker's name + event date, and the
verified Urdu subtitles burned in (from an SRT you supply).

This tool only edits; it never publishes. Complete the review report
(footage_review_template.md) and get human approval before posting.

Usage:
  python footage_clip.py --video rally.mp4 --start 00:12:30 --end 00:13:40 \
      --headline "..." --speaker "..." --date "..." --subtitles clip.srt \
      --credit "Footage: <source>" --out package.mp4
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, str(Path(__file__).parent))
from nr_news.config import CONFIG  # noqa: E402
from nr_news.renderer import CardRenderer, isolate_latin, load_logo, proportional_logo  # noqa: E402
from nr_news.shorts import ffmpeg_exe  # noqa: E402

W, H = 1080, 1920


def make_overlay(headline: str, speaker: str, date: str, credit: str, out: Path) -> Path:
    """Static broadcast furniture: brand bar, headline, speaker/date."""
    renderer = CardRenderer(CONFIG)
    logo = load_logo()
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im, "RGBA")

    # Brand bar
    d.rectangle((0, 0, W, 112), fill=(6, 8, 16, 235))
    d.rectangle((0, 112, W, 116), fill=(226, 184, 74, 255))
    scaled = proportional_logo(logo, 84)
    im.paste(scaled, (40, 14), scaled)
    brand_f = renderer._font(False, 38, "Bold")
    d.text((40 + scaled.width + 22, 56), "AZAD STUDIO", font=brand_f,
           fill=(255, 231, 151, 255), anchor="lm")

    # Headline zone (top, under the bar) — the footage sits below it.
    fnt = renderer._font(True, 52, "Bold")
    lines = renderer.wrap(d, headline, fnt, 920, True)
    y = 150
    for line in lines[:3]:
        d.text((W - 80, y), isolate_latin(line), font=fnt, fill=(248, 247, 242, 255),
               anchor="ra", direction="rtl", language="ur")
        y += 86

    # Lower third: speaker + verified date (bottom-left block).
    if speaker or date:
        d.rectangle((0, H - 330, W, H - 326), fill=(226, 184, 74, 255))
        if speaker:
            sf = renderer._font(True, 46, "Bold")
            d.text((W - 80, H - 300), isolate_latin(speaker), font=sf,
                   fill=(255, 231, 151, 255), anchor="ra", direction="rtl", language="ur")
        if date:
            df = renderer._font(False, 34, "SemiBold")
            d.text((W - 80, H - 232), date, font=df, fill=(228, 232, 242, 255),
                   anchor="ra")
    if credit:
        cf = renderer._font(False, 26, "Medium")
        d.text((40, H - 60), credit, font=cf, fill=(180, 188, 205, 230), anchor="lm")
    im.save(out)
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--start", required=True, help="hh:mm:ss or seconds")
    ap.add_argument("--end", required=True)
    ap.add_argument("--headline", required=True, help="concise Urdu headline")
    ap.add_argument("--speaker", default="")
    ap.add_argument("--date", default="", help="verified event date, e.g. 29 Oct 2026")
    ap.add_argument("--subtitles", default="", help="verified Urdu SRT (checked word by word)")
    ap.add_argument("--credit", default="", help="source credit where required")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    work = Path(args.out).parent / "footage_work"
    work.mkdir(parents=True, exist_ok=True)
    overlay = make_overlay(args.headline, args.speaker, args.date, args.credit,
                           work / "overlay.png")

    # Vertical frame: blurred full-frame pad + the footage fitted by
    # width in the middle band, speaker visible; original audio as-is.
    vf = (
        "split[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "gblur=sigma=28,eq=brightness=-0.12[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
        f"[base][2:v]overlay=0:0"
    )
    if args.subtitles:
        srt = str(Path(args.subtitles).resolve()).replace(":", "\\:").replace("'", "\\'")
        vf += (f"[v];[v]subtitles='{srt}':force_style='FontName=Noto Nastaliq Urdu,"
               "FontSize=15,PrimaryColour=&HFFFFFF,OutlineColour=&H90000000,"
               "BorderStyle=1,Outline=2,Alignment=2,MarginV=210'[vout]")
        map_v = "[vout]"
    else:
        vf += "[v]"
        map_v = "[v]"
    cmd = [ffmpeg_exe(), "-y", "-ss", args.start, "-to", args.end, "-i", args.video,
           "-loop", "1", "-i", str(overlay),
           "-filter_complex", vf, "-map", map_v, "-map", "0:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
           "-shortest", "-movflags", "+faststart", args.out]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # Fall back without burned subtitles rather than failing the edit.
        if args.subtitles:
            print("subtitle burn failed, retrying without subtitles...")
            return _retry_without_subtitles(args, overlay)
        raise SystemExit("ffmpeg failed: " + proc.stderr[-600:])
    print("built:", args.out)


def _retry_without_subtitles(args, overlay: Path) -> None:
    vf = (
        "split[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,"
        "gblur=sigma=28,eq=brightness=-0.12[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[base];"
        "[base][2:v]overlay=0:0[v]"
    )
    cmd = [ffmpeg_exe(), "-y", "-ss", args.start, "-to", args.end, "-i", args.video,
           "-loop", "1", "-i", str(overlay), "-filter_complex", vf,
           "-map", "[v]", "-map", "0:a", "-c:v", "libx264", "-preset", "medium",
           "-crf", "19", "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
           "-shortest", "-movflags", "+faststart", args.out]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("ffmpeg failed: " + proc.stderr[-600:])
    print("built (WARNING: subtitles not burned):", args.out)


if __name__ == "__main__":
    main()
