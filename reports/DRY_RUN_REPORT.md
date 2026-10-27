# AZAD STUDIO — Operations Report

Generated: 2026-10-27T17:59:40.890075Z (Asia/Kolkata timezone configured)

## Safety state
- Dry run: `False`
- Public publishing enabled: `True`
- Publish safety switch: `True`
- Operator approved: `True`
- Auto post enabled: `True`
- Auto approve verified editorials: `True`
- Editorial priority: regional politics first, then national/international politics, then public-interest development; crime excluded
- Page identity API-confirmed: `True`
- API-confirmed Page ID: `1383450338187017`
- API-confirmed Page name: `Azad Studio `
- Operator-approved test post passed: `true`
- API-confirmed Page link: `https://www.facebook.com/1383450338187017`
- Configured profile URL lookup hint: https://www.facebook.com/profile.php?id=61595929711037
- Graph API version: `v26.0`
- LLM configured: `True`
- Exact supplied monogram SHA-256: `d0b02c4e7f31f4f58d1e48c08fd3c8d6668bcbbe9e15ef0652a6ff584ede2b3d`

## Source monitor
- **etemaad_daily**: ok; items=25; HTTP=200; Urdu home page and new article pages fetched
- **indian_express**: error; items=0; HTTP=None; robots.txt HTTP 403; source marked unavailable
- **munsif_daily**: ok; items=20; HTTP=200; RSS fetched
- **the_wire**: ok; items=25; HTTP=200; public WordPress REST API fetched

## Counts
- Candidates: 69
- Open review items: 20
- Editorials: 1
- Rendered cards: 1
- Publish attempts: success=1, failed/unknown=0

## Rendered cards
- `/home/runner/work/Azad-Studio---urdu/Azad-Studio---urdu/data/cards/2026-10-27-4ee3e37504ab-1.png` — 1080×1350, page 1/1, visual=passed, editorial=published, SHA-256=f0e4521756de…

## Launch-test status
- Exact supplied monogram verified at top and bottom of rendered card: PASS
- Urdu RTL shaping with Noto Nastaliq Urdu/libraqm: PASS
- Roman Urdu number/name/uncertainty validation: PASS on verified fixture
- Crime and rumor exclusion rules implemented: PASS (see tests)
- Duplicate URL/content/cluster prevention implemented: PASS (see tests)
- Empty scan publishes nothing: PASS (see tests)
- Facebook Page identity/targeting: PASS (API-confirmed Page ID and name)
- Live Facebook publishing: ENABLED (config safety=True, API identity=True, test post=true)
- GitHub Actions CI/identity/hourly/container workflows: PASS (see reports/LAUNCH_CHECKLIST.md)
- 24/7 operation: requires deployment to a persistent host using the included service/container files
