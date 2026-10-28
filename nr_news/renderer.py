"""Agent D — deterministic black-and-gold layered card renderer.

The uploaded monogram asset is verified by SHA-256 on every render. Its content is
cropped from the uniform black background and only proportionally resized;
the monogram is never redrawn, recoloured or distorted.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from PIL import Image, ImageDraw, ImageFont, ImageOps

from .config import CONFIG
from .models import EditorialContent
from .util import iso, json_dumps, redact, sha256_file, sha256_text, utcnow

WIDTH, HEIGHT = 1080, 1350
BYLINE = "By - Abu Aimal and Aimal Akram"
BLACK = (5, 5, 6)
PANEL = (12, 11, 10)
GOLD = (226, 184, 74)
GOLD_LIGHT = (255, 231, 151)
GOLD_DARK = (119, 78, 16)
RED = (139, 14, 24)
RED_LIGHT = (190, 24, 35)
WHITE = (248, 247, 242)
MUTED = (199, 176, 115)
DARK_TEXT = (31, 22, 6)


class AssetError(RuntimeError):
    pass


class LayoutError(RuntimeError):
    pass


@dataclass
class RenderedCard:
    path: str
    sha256: str
    width: int
    height: int
    page_number: int
    page_count: int
    visual_status: str
    audit: dict[str, Any]


def font(path: Path, size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(str(path), size)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def verify_logo() -> str:
    if not CONFIG.logo_path.exists():
        raise AssetError("Exact supplied monogram asset is unavailable. Please upload the original assets.png again.")
    digest = sha256_file(CONFIG.logo_path)
    if digest != CONFIG.logo_sha256:
        raise AssetError("Exact supplied monogram asset failed SHA-256 verification. A substitute will not be used.")
    return digest


def load_logo() -> Image.Image:
    verify_logo()
    with Image.open(CONFIG.logo_path) as src:
        im = src.convert("RGBA")
    # Locate all non-black pixels; crop only the uniform black margin.
    rgb = im.convert("RGB")
    mask = Image.new("L", rgb.size, 0)
    pixels = rgb.load()
    mask_pixels = mask.load()
    for y in range(rgb.height):
        for x in range(rgb.width):
            r, g, b = pixels[x, y]
            if max(r, g, b) > 10:
                mask_pixels[x, y] = 255
    bbox = mask.getbbox()
    if not bbox:
        raise AssetError("Supplied monogram asset contains no visible monogram.")
    pad = 14
    bbox = (max(0, bbox[0] - pad), max(0, bbox[1] - pad),
            min(im.width, bbox[2] + pad), min(im.height, bbox[3] + pad))
    return im.crop(bbox)


def proportional_logo(logo: Image.Image, target_height: int) -> Image.Image:
    ratio = target_height / logo.height
    width = round(logo.width * ratio)
    return logo.resize((width, target_height), Image.Resampling.LANCZOS)


def paste_logo(canvas: Image.Image, logo: Image.Image, center_x: int, top: int, height: int) -> tuple[int, int, int, int]:
    scaled = proportional_logo(logo, height)
    x = center_x - scaled.width // 2
    canvas.paste(scaled, (x, top), scaled)
    return (x, top, x + scaled.width, top + scaled.height)


def gradient(size: tuple[int, int], top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    w, h = size
    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / max(1, h - 1)
        color = tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        d.line((0, y, w, y), fill=color)
    return im


def angled_banner(canvas: Image.Image, box: tuple[int, int, int, int], color: Image.Image,
                  border: tuple[int, int, int] = GOLD_LIGHT) -> None:
    x0, y0, x1, y1 = box
    notch = 28
    poly = [(x0, y0), (x1 - notch, y0), (x1, y1), (x0 + notch, y1)]
    mask = Image.new("L", canvas.size, 0)
    md = ImageDraw.Draw(mask)
    md.polygon(poly, fill=255)
    canvas.paste(color, (x0, y0), mask.crop((x0, y0, x1, y1)))
    d = ImageDraw.Draw(canvas)
    d.line(poly + [poly[0]], fill=border, width=3, joint="curve")


def isolate_latin(text: str) -> str:
    # First-strong-isolate / pop-directional-isolate keeps acronyms and email
    # addresses readable inside an RTL paragraph without changing the text file.
    return re.sub(r"([A-Za-z][A-Za-z0-9@._:-]*)", "\u2068\\1\u2069", text)


class CardRenderer:
    def __init__(self, config=CONFIG):
        self.config = config
        self.logo_digest = verify_logo()

    def _font(self, urdu: bool, size: int, weight: str = "Regular") -> ImageFont.FreeTypeFont:
        path = self.config.urdu_font_path if urdu else self.config.latin_font_path
        if not path.exists():
            raise AssetError(f"Required font missing: {path.name}")
        return font(path, size, weight)

    def _text_length(self, draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont,
                     rtl: bool = False) -> float:
        kwargs = {"font": fnt}
        if rtl:
            kwargs.update(direction="rtl", language="ur")
        return draw.textlength(isolate_latin(text) if rtl else text, **kwargs)

    def wrap(self, draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.FreeTypeFont,
             max_width: int, rtl: bool = False) -> list[str]:
        words = text.split()
        if not words:
            return [""]
        lines: list[str] = []
        current = words[0]
        for word in words[1:]:
            trial = current + " " + word
            if self._text_length(draw, trial, fnt, rtl) <= max_width:
                current = trial
            else:
                lines.append(current)
                current = word
        lines.append(current)
        # Very long unbroken strings are split by pixels to prevent clipping.
        result: list[str] = []
        for line in lines:
            if self._text_length(draw, line, fnt, rtl) <= max_width:
                result.append(line)
                continue
            chunk = ""
            for ch in line:
                if self._text_length(draw, chunk + ch, fnt, rtl) <= max_width:
                    chunk += ch
                else:
                    result.append(chunk)
                    chunk = ch
            if chunk:
                result.append(chunk)
        return result

    def measure_bullets(self, draw: ImageDraw.ImageDraw, bullets: list[str], size: int,
                        max_width: int, rtl: bool, line_height_factor: float,
                        compact: bool = False) -> tuple[list[list[str]], int, int]:
        fnt = self._font(rtl, size, "SemiBold" if rtl else "Medium")
        line_height = max(26 if compact else 32, round(size * line_height_factor))
        gap = 14 if compact else 18
        groups = [self.wrap(draw, b, fnt, max_width, rtl) for b in bullets]
        total = sum(len(g) * line_height + gap for g in groups)
        return groups, total, line_height

    def fit_bullets(self, draw: ImageDraw.ImageDraw, bullets: list[str], max_width: int,
                    max_height: int, rtl: bool, compact: bool = False) -> tuple[list[list[str]], ImageFont.FreeTypeFont, int]:
        if compact:
            sizes = range(36, 19, -2) if rtl else range(34, 19, -2)
            factor = 1.34 if rtl else 1.18
        else:
            sizes = range(40, 25, -2) if rtl else range(36, 24, -2)
            factor = 1.72 if rtl else 1.34
        best = None
        for size in sizes:
            groups, total, line_height = self.measure_bullets(draw, bullets, size, max_width, rtl, factor, compact)
            best = (groups, self._font(rtl, size, "SemiBold" if rtl else "Medium"), line_height, total)
            if total <= max_height:
                return best[:3]
        raise LayoutError(f"Bullets do not fit ({best[3]}px > {max_height}px) at minimum readable size")

    def _draw_background(self, canvas: Image.Image, date_line: str, time_line: str) -> None:
        d = ImageDraw.Draw(canvas)
        d.rectangle((0, 0, WIDTH, HEIGHT), fill=BLACK)
        # Subtle central panel and border.
        d.rounded_rectangle((18, 18, WIDTH - 18, HEIGHT - 18), radius=30, outline=GOLD_DARK, width=5)
        d.rounded_rectangle((28, 28, WIDTH - 28, HEIGHT - 28), radius=26, outline=(52, 39, 13), width=2)
        # Programmatic dotted world-map motif behind the date badge.
        cx, cy, radius = 815, 105, 118
        for ring in range(1, 7):
            ry = radius * ring / 6
            for i in range(8, 30):
                angle = 2 * math.pi * i / 36
                x = cx + radius * math.cos(angle)
                y = cy + ry * math.sin(angle)
                if 680 < x < 1030 and 25 < y < 190:
                    d.ellipse((x - 1.5, y - 1.5, x + 1.5, y + 1.5), fill=(69, 51, 16))
        for i in range(5):
            d.line((35 + 2 * i, 32 + 52 * i, 105 + 2 * i, 30 + 52 * i), fill=GOLD_LIGHT if i == 0 else GOLD_DARK, width=5 if i == 0 else 2)
        # Corner accents.
        for sx in (0, 1):
            x0 = 20 if sx == 0 else WIDTH - 145
            d.line((x0, HEIGHT - 120, x0 + 115, HEIGHT - 20), fill=GOLD_DARK, width=16)
            d.line((x0, HEIGHT - 145, x0 + 115, HEIGHT - 34), fill=(58, 41, 10), width=5)
        # Two-line date badge, matching the reference card.
        df = self._font(False, 19, "SemiBold")
        tf = self._font(False, 17, "Medium")
        tw = max(d.textlength(date_line, font=df), d.textlength(time_line, font=tf))
        x0 = WIDTH - 40 - tw - 54
        badge = (x0, 46, WIDTH - 40, 128)
        d.rounded_rectangle(badge, radius=16, fill=(15, 13, 9), outline=GOLD_DARK, width=2)
        d.text((WIDTH - 54, 60), date_line, font=df, fill=GOLD_LIGHT, anchor="ra")
        d.text((WIDTH - 54, 91), time_line, font=tf, fill=MUTED, anchor="ra")
        # Small calendar glyph.
        d.rounded_rectangle((x0 + 10, 62, x0 + 38, 94), radius=5, outline=GOLD_LIGHT, width=2)
        d.line((x0 + 10, 72, x0 + 38, 72), fill=GOLD_LIGHT, width=2)
        d.line((x0 + 18, 58, x0 + 18, 66), fill=GOLD_LIGHT, width=2)
        d.line((x0 + 30, 58, x0 + 30, 66), fill=GOLD_LIGHT, width=2)

    def _draw_headline(self, canvas: Image.Image, text: str, rtl: bool, y: int,
                       height: int = 92) -> None:
        d = ImageDraw.Draw(canvas)
        box = (38, y, WIDTH - 38, y + height)
        angled_banner(canvas, box, gradient((box[2] - box[0], box[3] - box[1]), GOLD_LIGHT, GOLD))
        # Twin slashes on the left edge, as in the supplied reference design.
        d.line((48, y + 12, 72, y + height - 12), fill=GOLD_DARK, width=5)
        d.line((60, y + 12, 84, y + height - 12), fill=GOLD_DARK, width=3)
        sizes = [40, 37, 34, 31, 28, 25, 23] if rtl else [42, 39, 36, 33, 30, 27, 24]
        max_width = 900
        for size in sizes:
            f = self._font(rtl, size, "Bold")
            lines = self.wrap(d, text, f, max_width, rtl)
            line_h = size * (1.45 if rtl else 1.08)
            total_h = len(lines) * line_h
            if (len(lines) <= 2 and total_h <= height - 6
                    and all(self._text_length(d, line, f, rtl) <= max_width for line in lines)):
                start_y = y + (height - total_h) / 2 + (-8 if rtl else 0)
                for idx, line in enumerate(lines):
                    x = WIDTH - 70 if rtl else 75
                    d.text((x, start_y + idx * line_h), isolate_latin(line) if rtl else line,
                           font=f, fill=DARK_TEXT, anchor="ra" if rtl else "la",
                           **({"direction": "rtl", "language": "ur"} if rtl else {}))
                return
        raise LayoutError("Headline does not fit the gold banner")

    def _draw_bullets(self, canvas: Image.Image, bullets: list[str], rtl: bool, y0: int,
                      max_height: int, max_width: int, x_text: int, bullet_x: int,
                      compact: bool = False) -> int:
        d = ImageDraw.Draw(canvas)
        groups, fnt, line_height = self.fit_bullets(d, bullets, max_width, max_height, rtl, compact)
        gap = 14 if compact else 18
        y = y0
        for lines in groups:
            d.ellipse((bullet_x - 9, y + 9, bullet_x + 9, y + 22), fill=GOLD)
            for line in lines:
                d.text((x_text, y), isolate_latin(line) if rtl else line, font=fnt, fill=WHITE,
                       anchor="ra" if rtl else "la", stroke_width=0,
                       **({"direction": "rtl", "language": "ur"} if rtl else {}))
                y += line_height
            y += gap
        return y

    def _draw_photo(self, canvas: Image.Image, media: dict[str, Any], box: tuple[int, int, int, int]) -> None:
        x0, y0, x1, y1 = box
        w, h = x1 - x0, y1 - y0
        with Image.open(media["local_path"]) as src:
            im = ImageOps.fit(src.convert("RGB"), (w, h), method=Image.Resampling.LANCZOS)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, w - 1, h - 1), radius=22, fill=255)
        canvas.paste(im, (x0, y0), mask)
        d = ImageDraw.Draw(canvas)
        d.rounded_rectangle((x0, y0, x1 - 1, y1 - 1), radius=22, outline=GOLD, width=4)
        # Operator rule: no credits or URLs printed on the photo; photographer
        # credit and license references remain in the audit/metadata only.

    def draw_card(self, content: EditorialContent, output_path: Path, *,
                  media: list[dict[str, Any]] | dict[str, Any] | None = None,
                  bullet_slice: tuple[int, int] | None = None, page_number: int = 1,
                  page_count: int = 1) -> RenderedCard:
        verify_logo()
        photos = normalize_media(media, self.config.max_photos_per_card)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        urdu_bullets = list(content.urdu_bullets)
        roman_bullets = list(content.roman_bullets)
        if bullet_slice:
            a, b = bullet_slice
            urdu_bullets, roman_bullets = urdu_bullets[a:b], roman_bullets[a:b]
        if not (3 <= len(urdu_bullets) == len(roman_bullets) <= 5):
            raise LayoutError("Each card requires 3-5 corresponding Urdu and Roman Urdu bullets")

        pub_dt = datetime.fromisoformat((content.publication_time or iso()).replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Kolkata"))
        date_line = pub_dt.strftime("%d %b %Y").upper()
        time_line = pub_dt.strftime("%I:%M %p IST")
        canvas = Image.new("RGB", (WIDTH, HEIGHT), BLACK)
        self._draw_background(canvas, date_line, time_line)
        d = ImageDraw.Draw(canvas)

        logo = load_logo()
        top_logo_box = paste_logo(canvas, logo, 120, 42, 104)
        brand_font = self._font(False, 17, "SemiBold")
        d.text((190, 128), "AZAD STUDIO", font=brand_font, fill=MUTED, anchor="la")

        # Reference-faithful proportions: a narrower LATEST NEWS ribbon, taller
        # news-image panels, and no section labels. The exact monogram, byline,
        # and two-photo support are preserved.
        heading_y, heading_h, heading_x1 = 166, 82, 700
        urdu_headline_y, headline_h = 282, 86
        urdu_bullets_y, urdu_bullets_h = 398, 300
        urdu_photo_box = (620, 392, 1035, 700)
        separator_y = 730
        roman_headline_y = 766
        roman_bullets_y, roman_bullets_h = 884, 276
        roman_photo_box = (620, 876, 1035, 1158)
        bottom_logo_y = 1164
        byline_y = 1250
        footer_y = 1298

        # Main heading must remain exactly LATEST NEWS, but use the narrower
        # angled ribbon from the supplied reference card.
        red_box = (35, heading_y, heading_x1, heading_y + heading_h)
        angled_banner(canvas, red_box, gradient((red_box[2] - red_box[0], red_box[3] - red_box[1]), RED_LIGHT, RED), border=GOLD_LIGHT)
        heading_font = self._font(False, 52, "ExtraBold")
        d.text((150, heading_y + 12), "LATEST NEWS", font=heading_font, fill=WHITE, stroke_width=1, stroke_fill=(60, 0, 0))
        # Megaphone icon, drawn programmatically.
        d.polygon([(52, heading_y + 28), (70, heading_y + 28), (88, heading_y + 16),
                   (88, heading_y + 56), (70, heading_y + 45), (52, heading_y + 45)], fill=WHITE)
        d.arc((50, heading_y + 38, 74, heading_y + 70), 270, 90, fill=WHITE, width=4)

        self._draw_headline(canvas, content.urdu_headline, True, urdu_headline_y, headline_h)

        # Up to two rights-cleared photos occupy reserved panels, mirroring the
        # reference card: one beside the Urdu copy, one beside the Roman copy.
        photo_boxes: list[tuple[int, int, int, int]] = []
        urdu_photo = photos[0] if photos else None
        roman_photo = photos[1] if len(photos) > 1 else None
        urdu_max_width = 900
        urdu_text_x = WIDTH - 135
        urdu_bullet_x = 1025
        if urdu_photo:
            self._draw_photo(canvas, urdu_photo, urdu_photo_box)
            photo_boxes.append(urdu_photo_box)
            urdu_max_width = 500
            urdu_text_x = 590
            urdu_bullet_x = 610
        urdu_end = self._draw_bullets(canvas, urdu_bullets, True, urdu_bullets_y,
                                      urdu_bullets_h, urdu_max_width,
                                      urdu_text_x, urdu_bullet_x, compact=bool(urdu_photo))
        if urdu_end > 706:
            raise LayoutError("Urdu bullet area overflowed its reserved region")

        # Separator with diamond.
        d.line((58, separator_y, 500, separator_y), fill=GOLD_DARK, width=3)
        d.line((580, separator_y, WIDTH - 58, separator_y), fill=GOLD_DARK, width=3)
        diamond = [(540, separator_y - 17), (558, separator_y), (540, separator_y + 17), (522, separator_y)]
        d.polygon(diamond, fill=GOLD)

        self._draw_headline(canvas, content.roman_headline, False, roman_headline_y, headline_h)
        roman_max_width = 900
        roman_text_x = 135
        roman_bullet_x = 88
        if roman_photo:
            self._draw_photo(canvas, roman_photo, roman_photo_box)
            photo_boxes.append(roman_photo_box)
            roman_max_width = 470
            roman_text_x = 120
            roman_bullet_x = 88
        roman_end = self._draw_bullets(canvas, roman_bullets, False, roman_bullets_y,
                                       roman_bullets_h, roman_max_width, roman_text_x,
                                       roman_bullet_x, compact=bool(roman_photo))
        if roman_end > 1160:
            raise LayoutError("Roman Urdu bullet area overflowed its reserved region")

        bottom_logo_box = paste_logo(canvas, logo, WIDTH // 2, bottom_logo_y, 62)
        byline_box = (250, byline_y - 22, 830, byline_y + 22)
        byline_font = self._font(False, 18, "SemiBold")
        d.text((WIDTH // 2, byline_y), BYLINE, font=byline_font, fill=GOLD, anchor="mm")
        footer_font = self._font(False, 15, "Medium")
        footer = f"VERIFIED REPORT  •  {page_number}/{page_count}  •  AZAD STUDIO"
        d.text((WIDTH // 2, footer_y), footer, font=footer_font, fill=MUTED, anchor="mm")

        canvas.save(output_path, format="PNG", optimize=True)
        digest = sha256_file(output_path)
        visual = self.visual_inspection(output_path, top_logo_box, bottom_logo_box,
                                        photo_boxes=photo_boxes, byline_box=byline_box,
                                        heading_box=red_box)
        audit = {
            "rendered_at": iso(),
            "logo_asset": str(self.config.logo_path),
            "logo_sha256": self.logo_digest,
            "logo_crop": "uniform black margin removed; proportional resize only",
            "card_sha256": digest,
            "dimensions": [WIDTH, HEIGHT],
            "urdu_font": self.config.urdu_font_path.name,
            "latin_font": self.config.latin_font_path.name,
            "rtl_engine": "libraqm",
            "heading": "LATEST NEWS",
            "heading_box": red_box,
            "design_reference": "two NEWS IMAGE panels; narrow angled LATEST NEWS ribbon",
            "byline": BYLINE,
            "page_number": page_number,
            "page_count": page_count,
            "media": photos,
            "media_count": len(photos),
            "media_credits": [f"{m.get('credit')} | {m.get('license_ref')}" for m in photos],
            "photo_boxes": photo_boxes,
            "visual_inspection": visual,
            "text_hash": sha256_text(json_dumps(content.to_dict())),
        }
        audit_path = self.config.audit_dir / (output_path.stem + ".json")
        audit_path.write_text(json_dumps(audit), encoding="utf-8")
        return RenderedCard(str(output_path), digest, WIDTH, HEIGHT, page_number, page_count,
                            "passed" if visual["passed"] else "failed", audit)

    def visual_inspection(self, path: Path, top_box: tuple[int, int, int, int],
                          bottom_box: tuple[int, int, int, int],
                          photo_boxes: list[tuple[int, int, int, int]] | None = None,
                          byline_box: tuple[int, int, int, int] | None = None,
                          heading_box: tuple[int, int, int, int] | None = None) -> dict[str, Any]:
        with Image.open(path) as im:
            size = im.size
            rgb = im.convert("RGB")
        checks = {
            "dimensions": size == (WIDTH, HEIGHT),
            "file_nonempty": path.stat().st_size > 50_000,
            "exact_logo_asset": sha256_file(self.config.logo_path) == self.logo_digest,
            "logo_top_pixels": _gold_pixel_count(rgb, top_box) > 100,
            "logo_bottom_pixels": _gold_pixel_count(rgb, bottom_box) > 60,
        }
        if heading_box:
            checks["narrow_latest_news_ribbon"] = (heading_box[2] - heading_box[0]) < 720
        if byline_box:
            checks["byline_pixels"] = _gold_pixel_count(rgb, byline_box) > 40
        for idx, box in enumerate(photo_boxes or [], 1):
            checks[f"photo_{idx}_pixels"] = _photo_pixel_count(rgb, box) > 1000
        return {"passed": all(checks.values()), "checks": checks}

    def render_cards(self, content: EditorialContent, stem: str, *,
                     media: list[dict[str, Any]] | dict[str, Any] | None = None) -> list[RenderedCard]:
        """Render one card, or same-template continuation cards if copy overflows."""
        n = len(content.urdu_bullets)
        path = self.config.card_dir / f"{stem}-1.png"
        try:
            return [self.draw_card(content, path, media=media)]
        except LayoutError as exc:
            if n < 4:
                raise
            # Continuation cards preserve paired Urdu/Roman bullets and repeat the
            # same template, headline and exact supplied monogram. No facts are changed.
            groups = [(0, 3), (max(0, n - 3), n)]
            if groups[0] == groups[1]:
                groups = groups[:1]
            page_count = len(groups)
            cards: list[RenderedCard] = []
            for idx, (a, b) in enumerate(groups, 1):
                card_path = self.config.card_dir / f"{stem}-{idx}.png"
                cards.append(self.draw_card(content, card_path, media=media,
                                            bullet_slice=(a, b), page_number=idx, page_count=page_count))
            if not cards:
                raise exc
            return cards


def _gold_pixel_count(im: Image.Image, box: tuple[int, int, int, int]) -> int:
    crop = im.crop(box)
    count = 0
    pixels = crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()
    for r, g, b in pixels:
        if r > 120 and g > 75 and b < 130 and r > b + 35:
            count += 1
    return count


def normalize_media(media: list[dict[str, Any]] | dict[str, Any] | None,
                    limit: int = 2) -> list[dict[str, Any]]:
    """Accept legacy single-media dicts and new two-photo lists.

    Entries whose local photo file no longer exists (e.g. a fresh CI runner
    without the media cache) are dropped so the card falls back to the clean
    text-led layout instead of crashing the render.
    """
    if not media:
        return []
    if isinstance(media, dict):
        items = [media]
    elif isinstance(media, (list, tuple)):
        items = [m for m in media if isinstance(m, dict)]
    else:
        return []
    usable = []
    for item in items:
        local = item.get("local_path")
        if local and not Path(local).exists():
            continue
        usable.append(item)
    return usable[:limit]


def _photo_pixel_count(im: Image.Image, box: tuple[int, int, int, int]) -> int:
    """Count clearly non-background pixels inside a photo panel."""
    crop = im.crop(box)
    pixels = crop.get_flattened_data() if hasattr(crop, "get_flattened_data") else crop.getdata()
    count = 0
    for r, g, b in pixels:
        # Exclude near-black card background and strong gold borders/credits.
        if max(r, g, b) > 45 and not (r > 170 and g > 130 and b < 125):
            count += 1
    return count
