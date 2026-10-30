"""Build an AZAD STUDIO real-footage political package.

Takes a RIGHTS-CLEARED local footage file and an exact clip window.
The politician's original audio is preserved untouched — no AI
voiceover is ever added over speech.

Format follows the house rule by duration:
  <= 90 seconds -> portrait  1080x1920 (9:16)
  >  90 seconds -> landscape 1920x1080 (16:9)

Furniture: subtle AZAD STUDIO branding, a concise Urdu headline, the
speaker's name + verified event date, burned-in Urdu subtitles (from
an SRT you supply and have checked word by word), and an optional
scrolling news ticker (--ticker) in English / Roman Urdu for the
news-channel look when a word-for-word Urdu translation is not
possible.

This tool only edits; it never publishes. Complete the review report
(footage_review_template.md) and get human approval before posting.

Usage:
  python footage_clip.py --video rally.mp4 --start 00:12:30 --end 00:15:10 \
      --headline "..." --speaker "..." --date "..." --subtitles clip.srt \
      --ticker "Roman Urdu scrolling text | second item" \
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

W, H = 1080, 1920          # portrait
LW, LH = 1920, 1080        # landscape
SUB_BAND_H = 470           # portrait subtitle band; overlaid at (0, H - SUB_BAND_H)
LS_SUB_BAND_H = 250        # landscape subtitle band; overlaid above the ticker
TICKER_H = 68              # scrolling ticker bar height (both orientations)
TICKER_SPEED = 130         # ticker scroll speed, px/sec


def _parse_ts(value: str) -> float:
    """'hh:mm:ss' / 'mm:ss' / plain seconds -> float seconds."""
    parts = str(value).strip().split(":")
    total = 0.0
    for p in parts:
        total = total * 60 + float(p)
    return total


def make_overlay(headline: str, speaker: str, date: str, credit: str,
                 out: Path, ticker: bool = False) -> Path:
    """Portrait static furniture: brand bar, headline, speaker/date.

    Lower third is stacked ABOVE the subtitle band (subtitles occupy
    roughly H-400..H-90). With a ticker, the credit moves up so the
    ticker bar (bottom TICKER_H px) stays clear.
    """
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

    # Lower third: speaker + verified date, above the subtitle band.
    if speaker or date:
        d.rectangle((0, H - 690, W, H - 686), fill=(226, 184, 74, 255))
        if speaker:
            sf = renderer._font(True, 46, "Bold")
            d.text((W - 80, H - 678), isolate_latin(speaker), font=sf,
                   fill=(255, 231, 151, 255), anchor="ra", direction="rtl",
                   language="ur")
        if date:
            df = renderer._font(False, 34, "SemiBold")
            d.text((W - 80, H - 590), date, font=df, fill=(228, 232, 242, 255),
                   anchor="ra")
    if credit:
        cf = renderer._font(False, 26, "Medium")
        credit_y = H - 150 if ticker else H - 60
        d.text((40, credit_y), credit, font=cf, fill=(180, 188, 205, 230),
               anchor="lm")
    if ticker:
        _draw_ticker_bar(d, W, H)
    im.save(out)
    return out


def make_overlay_landscape(headline: str, speaker: str, date: str,
                           credit: str, out: Path,
                           ticker: bool = False) -> Path:
    """Landscape static furniture for 1920x1080.

    Top strap carries brand + headline + speaker/date; bottom carries
    subtitles (separate band) and the scrolling ticker bar.
    """
    renderer = CardRenderer(CONFIG)
    logo = load_logo()
    im = Image.new("RGBA", (LW, LH), (0, 0, 0, 0))
    d = ImageDraw.Draw(im, "RGBA")

    # Brand bar
    d.rectangle((0, 0, LW, 84), fill=(6, 8, 16, 235))
    d.rectangle((0, 84, LW, 88), fill=(226, 184, 74, 255))
    scaled = proportional_logo(logo, 62)
    im.paste(scaled, (36, 11), scaled)
    brand_f = renderer._font(False, 36, "Bold")
    d.text((36 + scaled.width + 20, 42), "AZAD STUDIO", font=brand_f,
           fill=(255, 231, 151, 255), anchor="lm")

    # Headline strap (semi-transparent) with speaker + date under it.
    d.rectangle((0, 96, LW, 292), fill=(4, 6, 12, 165))
    fnt = renderer._font(True, 54, "Bold")
    lines = renderer.wrap(d, headline, fnt, 1760, True)
    y = 108
    for line in lines[:2]:
        d.text((LW - 60, y), isolate_latin(line), font=fnt,
               fill=(248, 247, 242, 255), anchor="ra", direction="rtl",
               language="ur")
        y += 78
    if speaker:
        sf = renderer._font(True, 38, "Bold")
        d.text((LW - 60, 262), isolate_latin(speaker), font=sf,
               fill=(255, 231, 151, 255), anchor="ra", direction="rtl",
               language="ur")
    if date:
        df = renderer._font(False, 30, "SemiBold")
        d.text((60, 262), date, font=df, fill=(228, 232, 242, 255),
               anchor="la")
    if credit:
        cf = renderer._font(False, 24, "Medium")
        d.text((40, LH - TICKER_H - 34), credit, font=cf,
               fill=(200, 206, 220, 220), anchor="lm")
    if ticker:
        _draw_ticker_bar(d, LW, LH)
    im.save(out)
    return out


def _draw_ticker_bar(d: ImageDraw.ImageDraw, width: int, height: int) -> None:
    """Opaque navy ticker bar at the very bottom (label drawn later,
    on top of the scrolling text, as its own PNG)."""
    d.rectangle((0, height - TICKER_H, width, height), fill=(8, 10, 24, 255))
    d.rectangle((0, height - TICKER_H, width, height - TICKER_H + 4),
                fill=(226, 184, 74, 255))


def make_ticker_pngs(ticker_text: str, work: Path) -> tuple[Path, int, Path]:
    """Render (scroll_text_png, scroll_text_width, label_png).

    Latin-script text (English / Roman Urdu) — drawn LTR with the
    channel's Latin font. The label block is a separate gold PNG that
    is composited AFTER the scrolling text so the text passes under it.
    """
    renderer = CardRenderer(CONFIG)
    text = "    +++    ".join(p.strip() for p in ticker_text.split("|")
                              if p.strip())
    text = (text + "    +++    ") * 2  # repeat so the scroll stays full
    fnt = renderer._font(False, 38, "Bold")
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    tw = int(probe.textlength(text, font=fnt)) + 60
    im = Image.new("RGBA", (tw, TICKER_H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    d.text((30, TICKER_H // 2), text, font=fnt, fill=(245, 246, 250, 255),
           anchor="lm")
    scroll_path = work / "ticker_scroll.png"
    im.save(scroll_path)

    label_w = 360
    lab = Image.new("RGBA", (label_w, TICKER_H), (0, 0, 0, 0))
    dl = ImageDraw.Draw(lab)
    dl.rectangle((0, 0, label_w, TICKER_H), fill=(226, 184, 74, 255))
    lf = renderer._font(False, 34, "Bold")
    dl.text((label_w // 2, TICKER_H // 2), "AZAD STUDIO", font=lf,
            fill=(10, 10, 18, 255), anchor="mm")
    label_path = work / "ticker_label.png"
    lab.save(label_path)
    return scroll_path, tw, label_path


def _parse_srt(path: Path) -> list[tuple[float, float, str]]:
    import re

    text = Path(path).read_text(encoding="utf-8-sig")
    cues = []
    for block in re.split(r"\n\s*\n", text.strip()):
        lines = [l for l in block.splitlines() if l.strip()]
        if len(lines) < 2:
            continue
        m = re.search(r"(\d+):(\d+):([\d.,]+)\s*-->\s*(\d+):(\d+):([\d.,]+)",
                      block)
        if not m:
            continue
        g = [float(x.replace(",", ".")) for x in m.groups()]
        start = g[0] * 3600 + g[1] * 60 + g[2]
        end = g[3] * 3600 + g[4] * 60 + g[5]
        body = " ".join(l for l in lines
                        if "-->" not in l and not l.strip().isdigit())
        if body:
            cues.append((start, end, body))
    return cues


def make_subtitle_pngs(srt_path: Path, work: Path,
                       width: int = W, band_h: int = SUB_BAND_H,
                       font_size: int = 46, wrap_w: int = 940,
                       bottom_pad: int = 90) -> list[tuple[float, float, Path]]:
    """Render each Urdu cue as a slim bottom-band PNG (proven fonts).

    Band-sized (not full-frame) PNGs keep the timed overlays cheap
    enough for ffmpeg to composite in real time. The box bottom sits
    at band_h - bottom_pad so it clears the credit/ticker lines.
    """
    renderer = CardRenderer(CONFIG)
    out = []
    for i, (start, end, body) in enumerate(_parse_srt(srt_path)):
        im = Image.new("RGBA", (width, band_h), (0, 0, 0, 0))
        d = ImageDraw.Draw(im, "RGBA")
        fnt = renderer._font(True, font_size, "Bold")
        lines = renderer.wrap(d, body, fnt, wrap_w, True)
        lh = font_size * 1.9
        total_h = len(lines) * lh
        y0 = band_h - bottom_pad - 24 - total_h
        d.rounded_rectangle((40, y0 - 24, width - 40, y0 + total_h + 24),
                            radius=28, fill=(0, 0, 0, 150))
        for j, line in enumerate(lines):
            d.text((width // 2, y0 + j * lh), isolate_latin(line), font=fnt,
                   fill=(255, 255, 255, 255), anchor="ma", direction="rtl",
                   language="ur")
        path = work / f"sub_{i:02d}.png"
        im.save(path)
        out.append((start, end, path))
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--video", required=True)
    ap.add_argument("--start", required=True, help="hh:mm:ss or seconds")
    ap.add_argument("--end", required=True)
    ap.add_argument("--headline", required=True, help="concise Urdu headline")
    ap.add_argument("--speaker", default="")
    ap.add_argument("--date", default="", help="verified event date")
    ap.add_argument("--subtitles", default="",
                    help="verified Urdu SRT (checked word by word)")
    ap.add_argument("--ticker", default="",
                    help="scrolling ticker text, English/Roman Urdu, "
                         "items separated by '|'")
    ap.add_argument("--credit", default="", help="source credit where required")
    ap.add_argument("--format", choices=["auto", "portrait", "landscape"],
                    default="auto",
                    help="auto: <=90s portrait, >90s landscape (house rule)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    duration = _parse_ts(args.end) - _parse_ts(args.start)
    if duration <= 0:
        raise SystemExit("--end must be after --start")
    landscape = (args.format == "landscape"
                 or (args.format == "auto" and duration > 90))
    fw, fh = (LW, LH) if landscape else (W, H)

    work = Path(args.out).parent / "footage_work"
    work.mkdir(parents=True, exist_ok=True)
    has_ticker = bool(args.ticker.strip())
    if landscape:
        overlay = make_overlay_landscape(args.headline, args.speaker,
                                         args.date, args.credit,
                                         work / "overlay.png", has_ticker)
    else:
        overlay = make_overlay(args.headline, args.speaker, args.date,
                               args.credit, work / "overlay.png", has_ticker)

    # Framing: blurred full-frame pad + footage fitted inside; the
    # original audio is passed through untouched.
    if landscape:
        vf = (
            f"[0:v]split[a][b];"
            f"[a]scale={LW}:{LH}:force_original_aspect_ratio=increase,"
            f"crop={LW}:{LH},gblur=sigma=28,eq=brightness=-0.12[bg];"
            f"[b]scale={LW}:{LH}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vv];"
            f"[vv][1:v]overlay=0:0[base]"
        )
        sub_y = LH - TICKER_H - LS_SUB_BAND_H
    else:
        vf = (
            "[0:v]split[a][b];"
            "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=28,eq=brightness=-0.12[bg];"
            "[b]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2-170[vv];"
            "[vv][1:v]overlay=0:0[base]"
        )
        sub_y = H - SUB_BAND_H

    extra_inputs: list[str] = []
    next_idx = 2  # 0 = video, 1 = static overlay
    cur = "base"
    if args.subtitles:
        if landscape:
            cues = make_subtitle_pngs(Path(args.subtitles), work, width=LW,
                                      band_h=LS_SUB_BAND_H, font_size=48,
                                      wrap_w=1700, bottom_pad=20)
        else:
            cues = make_subtitle_pngs(Path(args.subtitles), work)
        for k, (st, en, png) in enumerate(cues):
            extra_inputs += ["-loop", "1", "-i", str(png)]
            nxt = f"s{k}"
            vf += (f";[{cur}][{next_idx}:v]overlay=0:{sub_y}:"
                   f"enable='between(t,{st:.2f},{en:.2f})'[{nxt}]")
            cur = nxt
            next_idx += 1
    if has_ticker:
        scroll_png, scroll_w, label_png = make_ticker_pngs(args.ticker, work)
        extra_inputs += ["-loop", "1", "-i", str(scroll_png)]
        vf += (f";[{cur}][{next_idx}:v]overlay="
               f"x='{fw}-mod(t*{TICKER_SPEED},{fw + scroll_w})':"
               f"y={fh - TICKER_H}[tk]")
        cur = "tk"
        next_idx += 1
        extra_inputs += ["-loop", "1", "-i", str(label_png)]
        vf += f";[{cur}][{next_idx}:v]overlay=0:{fh - TICKER_H}[fin]"
        cur = "fin"
        next_idx += 1

    cmd = [ffmpeg_exe(), "-y", "-ss", args.start, "-to", args.end,
           "-i", args.video,
           "-loop", "1", "-i", str(overlay), *extra_inputs,
           "-filter_complex", vf, "-map", f"[{cur}]", "-map", "0:a",
           "-c:v", "libx264", "-preset", "medium", "-crf", "19",
           "-c:a", "aac", "-b:a", "160k", "-ar", "48000", "-ac", "2",
           "-shortest", "-movflags", "+faststart", args.out]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        # Fall back without burned subtitles rather than failing the edit.
        if args.subtitles:
            print("subtitle burn failed, retrying without subtitles...")
            print("subtitle attempt error:", proc.stderr[-1200:])
            return _retry_without_subtitles(args, overlay, landscape,
                                            duration)
        raise SystemExit("ffmpeg failed: " + proc.stderr[-600:])
    orient = "landscape 1920x1080" if landscape else "portrait 1080x1920"
    print(f"built ({orient}, {duration:.1f}s):", args.out)


def _retry_without_subtitles(args, overlay: Path, landscape: bool,
                             duration: float) -> None:
    if landscape:
        vf = (
            f"[0:v]split[a][b];"
            f"[a]scale={LW}:{LH}:force_original_aspect_ratio=increase,"
            f"crop={LW}:{LH},gblur=sigma=28,eq=brightness=-0.12[bg];"
            f"[b]scale={LW}:{LH}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2[vv];"
            f"[vv][1:v]overlay=0:0[v]"
        )
    else:
        vf = (
            "[0:v]split[a][b];"
            "[a]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,gblur=sigma=28,eq=brightness=-0.12[bg];"
            "[b]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2-170[vv];"
            "[vv][1:v]overlay=0:0[v]"
        )
    cmd = [ffmpeg_exe(), "-y", "-ss", args.start, "-to", args.end,
           "-i", args.video,
           "-loop", "1", "-i", str(overlay), "-filter_complex", vf,
           "-map", "[v]", "-map", "0:a", "-c:v", "libx264",
           "-preset", "medium", "-crf", "19", "-c:a", "aac", "-b:a",
           "160k", "-ar", "48000", "-ac", "2", "-shortest",
           "-movflags", "+faststart", args.out]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit("ffmpeg failed: " + proc.stderr[-600:])
    print("built (WARNING: subtitles not burned):", args.out)


if __name__ == "__main__":
    main()
