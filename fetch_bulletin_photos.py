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

# Pinned exact Commons files (operator photo-fix, 28 Oct 2026): the
# widest-image picker previously chose geographically wrong photos
# (Nepal tap for Hyderabad, Meghalaya polling for Andhra Pradesh, a
# 2017 Delhi EC drive for a BRS/Telangana story). These are pinned so
# re-runs reproduce the verified matches:
#   1 council-election results -> EVMs being carried, Patna (Bihar is
#     one of the four states in the story)
#   3 Hyderabad water orders   -> Hussain Sagar, Hyderabad
#   5 AP local-poll change     -> voters queueing in Visakhapatnam, AP
#   6 graduate voter demand    -> Osmania University, Hyderabad
PINNED: dict[int, str] = {
    1: "Polling officials are carrying Electronic Voting Machine (EVMs) and other election related materials required for the General Elections 2024, in Patna on May 31, 2024 (2).jpg",
    3: "Buddha Statue, Hussain Sagar, Hyd.JPG",
    5: "Voters standing in a queue to cast their votes, at a polling booth, during the 8th Phase of General Elections-2014, at Madhavadhara, in Visakhapatnam, Andhra Pradesh on May 07, 2014.jpg",
    6: "OsmaniaUnivArtsCollege.JPG",
}

# story number -> Commons search phrases, best first
QUERIES: dict[int, list[str]] = {
    1: ["electronic voting machine India polling", "Vidhana Soudha Bengaluru",
        "Karnataka legislative assembly building"],
    2: ["Telangana Secretariat Hyderabad", "Hyderabad government building",
        "BR Ambedkar Telangana Secretariat"],
    3: ["Hussain Sagar Hyderabad", "water tank Hyderabad",
        "drinking water tap India"],
    4: ["drought cracked earth field", "dry cracked soil drought India"],
    5: ["voters queue Andhra Pradesh election", "polling station India election queue",
        "Amaravati Andhra Pradesh"],
    6: ["Osmania University Hyderabad", "voter registration India",
        "Indian voter id card election"],
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


def fetch_pinned(story_no: int) -> dict | None:
    """Download a PINNED exact Commons file (1920px thumb)."""
    import urllib.parse

    fname = PINNED[story_no]
    url = ("https://commons.wikimedia.org/wiki/Special:FilePath/"
           + urllib.parse.quote(fname) + "?width=1920")
    try:
        blob = requests.get(url, headers=HEADERS, timeout=60).content
    except Exception:
        return None
    if len(blob) < 20_000:
        return None
    dest = OUT / f"story{story_no}.jpg"
    dest.write_bytes(blob)
    return {"title": f"File:{fname}", "url": url, "license": "see Commons file page",
            "query": "pinned exact match", "file": dest.name}


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {}
    for story_no, queries in QUERIES.items():
        if story_no in PINNED:
            pinned = fetch_pinned(story_no)
            if pinned:
                manifest[str(story_no)] = pinned
                print(f"story {story_no}: PINNED {pinned['title']}")
                continue
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
