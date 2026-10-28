"""Fetch freely-licensed story photos for the 28 Oct 2026 bulletin.

Source: Wikimedia Commons (public-domain / CC images only). One relevant
photo per story, saved next to the bulletin output so the slide photo
band shows a real visual instead of the branded fallback. Relevant —
never decorative-fake: government buildings, polling, drought land,
water, the High Court.

Run:  python fetch_bulletin_photos.py
"""
from __future__ import annotations

import json
from pathlib import Path

import requests

OUT = Path(__file__).parent / "bulletin_output" / "2026-10-28" / "photos"
API = "https://commons.wikimedia.org/w/api.php"
HEADERS = {"User-Agent": "AZAD STUDIO-newsroom/1.0 (bulletin photo fetch)"}

# story number -> Commons search phrases, best first
QUERIES: dict[int, list[str]] = {
    1: ["Vidhana Soudha Bengaluru", "Karnataka legislative assembly building",
        "electronic voting machine India"],
    2: ["Telangana Secretariat Hyderabad", "Hyderabad government building",
        "BR Ambedkar Telangana Secretariat"],
    3: ["drinking water tap India", "Hussain Sagar Hyderabad",
        "water tank Hyderabad"],
    4: ["drought cracked earth field", "dry cracked soil drought India"],
    5: ["polling station India election queue", "Amaravati Andhra Pradesh",
        "India election voting queue"],
    6: ["voter registration India", "Indian voter id card election",
        "Hyderabad street"],
    7: ["High Court Hyderabad building", "Telangana High Court Hyderabad",
        "Hyderabad court building"],
}


def search_photo(query: str) -> dict | None:
    params = {
        "action": "query", "format": "json", "generator": "search",
        "gsrsearch": f"filetype:bitmap {query}", "gsrnamespace": 6,
        "gsrlimit": 12, "prop": "imageinfo",
        "iiprop": "url|size|mime|extmetadata", "iiurlwidth": 1600,
    }
    try:
        data = requests.get(API, params=params, headers=HEADERS, timeout=30).json()
    except Exception:
        return None
    pages = (data.get("query") or {}).get("pages") or {}
    best = None
    for page in pages.values():
        info = (page.get("imageinfo") or [{}])[0]
        if info.get("mime") not in ("image/jpeg", "image/png"):
            continue
        w, h = info.get("width", 0), info.get("height", 0)
        if w < 1200 or h < 500:  # photo band needs a decent landscape frame
            continue
        lic = ((info.get("extmetadata") or {}).get("LicenseShortName") or {}).get("value", "")
        cand = {"title": page.get("title"), "url": info.get("thumburl") or info.get("url"),
                "license": lic, "width": w, "height": h}
        if best is None or w > best["width"]:
            best = cand
    return best


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for story_no, queries in QUERIES.items():
        chosen = None
        for q in queries:
            chosen = search_photo(q)
            if chosen:
                chosen["query"] = q
                break
        if not chosen:
            print(f"story {story_no}: NO photo found")
            continue
        dest = OUT / f"story{story_no}.jpg"
        blob = requests.get(chosen["url"], headers=HEADERS, timeout=60).content
        if len(blob) < 20_000:
            print(f"story {story_no}: download too small, skipped")
            continue
        dest.write_bytes(blob)
        manifest[str(story_no)] = {**chosen, "file": dest.name}
        print(f"story {story_no}: {chosen['title']} [{chosen['license']}] -> {dest.name}")
    (OUT / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False))
    print("saved manifest with", len(manifest), "photos")


if __name__ == "__main__":
    main()
