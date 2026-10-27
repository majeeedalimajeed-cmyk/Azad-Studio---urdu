# AZAD STUDIO — launch checklist

**Decision: AUTO POST MODE IS ENABLED, but continuous generation is currently BLOCKED by two credential issues.** One operator-approved public test post is live. See the blockers below before expecting new posts.

The textual brand is AZAD STUDIO; the supplied NR monogram and layout remain unchanged. The original `profile.php?id=61595929711037` is only a lookup hint. The API-confirmed publishing target is Page ID `1383450338187017`, name `Azad Studio `, canonical link `https://www.facebook.com/1383450338187017`.

## What is already done

- Continuous auto-post variables are set to true in GitHub Actions: `NR_DRY_RUN=false`, `NR_PUBLISH_ENABLED=true`, `NR_PUBLISH_SAFETY_OK=true`, `NR_OPERATOR_APPROVED=true`, `NR_VERIFY_APPROVED=true`, `NR_AUTO_APPROVE_VERIFIED=true`, `NR_AUTO_POST_ENABLED=true`.
- Editorial policy: **no crime news**, **more political news**, **regional first and then the rest**. Crime is excluded at ingest, verification, and generated-editorial stages. Durable job priority ranks regional politics first, then national/international politics, then regional development, then other verified news.
- One operator-approved public test post was published and independently confirmed through the Graph API:
  - Post ID: `1383450338187017_122096734239530990`
  - Permalink: https://www.facebook.com/122096734251530990/posts/122096734239530990
  - Created: `2026-10-27T17:59:35+0000` (23:29:35 IST)
  - Caption marker: `0bda2e161ffb`
  - Editorial ID: `4ee3e37504ab4132956d8c0f017fcb98`
  - Card: `data/cards/2026-10-27-4ee3e37504ab-1.png`
  - Evidence: `reports/TEST_POST_RESULT.md`, workflow run `40555660776`.
- Exact supplied monogram SHA-256 `d0b02c4e7f31f4f58d1e48c08fd3c8d6668bcbbe9e15ef0652a6ff584ede2b3d` verified; 1080×1350 RTL Urdu / LTR Roman Urdu card passed visual inspection.
- Local tests: `24 passed`. `check-assets` passes with `raqm_available:true`.
- Two-photo card integration is implemented: source-supplied rights-cleared photos first, then relevant safe-search Pixabay Content License photos downloaded locally, credited, cached, and audited. The `LATEST NEWS` banner and both news sections are somewhat smaller; compact bullets keep the two news-image boxes readable and leave space for **By - Abu Aimal and Aimal Akram** below the bottom monogram. A text-led card remains the fallback. `verify-pixabay.yml` renders a non-published demo without printing the key.
- Stop mechanism: `deploy/STOP_AUTO_POSTING.md` and `deploy/toggle_auto_posting.py --stop`. Setting `NR_AUTO_POST_ENABLED=false` (and `NR_PUBLISH_ENABLED=false`) stops new public posts.

## Current blockers — action required

| # | Blocker | Evidence | Required action |
|---|---|---|---|
| 1 | Both Facebook tokens expired | `verify-facebook` diagnostics run `40559104282` returned `OAuthException 190`, subcode `463`, “Session has expired on Tuesday, 27-Oct-26 11:00:00 PDT” for `/me`, `/{page_id}`, and `/{page_id}/feed` using `FB_PAGE_ACCESS_TOKEN`. Hourly auto run `40557387623` failed at identity verification. | Replace `FB_PAGE_ACCESS_TOKEN` with a genuine long-lived Page token. Optionally also replace `FB_USER_ACCESS_TOKEN` with a long-lived user token for `/me/accounts` and feed reconciliation. The Page-token fallback in `nr_news/facebook.py` is implemented and unit-tested, but it cannot validate an expired token. |
| 2 | No working LLM gateway | `verify-llm` runs `40556440279` and `40556907254`: `GROK_API_KEY` is rejected by xAI (“Incorrect API key”), and `OPENAI_API_KEY` returns `insufficient_quota` / `credit_balance_exhausted`. The fixture test post did not need LLM generation, but new stories do. | Provide a valid xAI/Grok key in `GROK_API_KEY`, or add OpenAI credits and set the model/base variables accordingly, or supply another OpenAI-compatible gateway. Until then, Agent A/C/validator calls are held and no new editorial is generated. |

Because of blocker 2, the hourly workflow will not invent or publish unverified news. Because of blocker 1, it currently cannot even confirm the Page identity. No duplicate, crime, rumor, or unlicensed-image post is possible while these holds remain.

## Required behavior tests

| # | Test | Status | Evidence |
|---|---|---|---|
| 1 | All four sources are monitored | PASS | `reports/DRY_RUN_REPORT.md` shows Munsif, Etemaad, and The Wire OK; Indian Express is checked with a logged 403 outage and a clearly marked non-publishable snapshot. |
| 2 | Stories are fresh and verified | PASS | Verified TGPSC fixture uses a 26 Oct 2026 Etemaad article and an official 26 Oct 2026 TGPSC web note. VerificationAgent checks timestamps, claim status, evidence URLs and independent corroboration. |
| 3 | Crime reports are excluded | PASS | Expanded exclusion regexes at ingest, verification and generated-editorial stages; regression tests include police/arrest/fraud terms. |
| 4 | More political news with regional-first ordering | PASS | Additive/category priority and durable `jobs.priority` ordering; regression tests. |
| 5 | Urdu summaries preserve original facts | PASS | Bilingual fixture, fact inventory, entity/number consistency checks. No complete article text is reproduced. |
| 6 | Roman Urdu matches approved Urdu | PASS | Agent C receives only approved Urdu; independent reviewer plus deterministic checks preserve names, dates, figures and uncertainty. |
| 7 | Exact supplied monogram appears correctly | PASS | SHA-256-gated asset, proportional black-margin crop only, exact top/bottom placement, visual inspection. |
| 8 | Card layout is readable and consistent | PASS | Final live card at `data/cards/2026-10-27-4ee3e37504ab-1.png`; RTL/LTR rendering with Noto fonts and libraqm. Two-photo compact layout passed visual inspection with `photo_1_pixels` and `photo_2_pixels` checks. |
| 9 | Duplicate stories are blocked | PASS | Canonical URL, content hash, cluster similarity, idempotency keys, and regression tests. |
| 10 | Empty hourly runs publish nothing | PASS | Empty-scan test asserts zero cards, captions, and publish attempts. |
| 11 | Breaking-news verification starts promptly | PASS | 5-minute breaking scheduler and fresh-candidate priority job test. |
| 12 | Facebook publishing targets only the correct Page | PASS (pre-expiry) | Graph API confirmed Page ID `1383450338187017`, name `Azad Studio `, required permissions and tasks. Re-verify after token refresh. |
| 13 | API results, errors, retries are recorded | PASS | SQLite `api_events`, `publish_attempts`, `jobs`, and source outage logging. Success requires an API `post_id`. |
| 14 | Operator-approved test post | PASS | Run `40555660776`; `reports/TEST_POST_RESULT.md`; Graph API `post_id` confirmed. |
| 15 | Two relevant rights-cleared photos | PASS (offline) | Two-photo renderer/audit tests, Pixabay query tests, and mock two-photo media selection pass. Live Pixabay API demo is validated separately by `verify-pixabay.yml`; no key is printed or hotlinked. |

## Operational status

| Item | Status | Notes |
|---|---|---|
| Auto-post variables | ENABLED | Set with `deploy/toggle_auto_posting.py --start`; use `--stop` to halt. |
| Public test post | PASS | One post live; see `reports/TEST_POST_RESULT.md`. |
| Facebook tokens | HOLD | Both expired; replace with long-lived tokens. |
| LLM gateway | HOLD | Grok key invalid; OpenAI key has no credits. |
| Exact supplied monogram / fonts | PASS | `check-assets` passes. |
| Offline unit/integration tests | PASS | `24 passed`. |
| Two-photo / Pixabay integration | PARTIAL | Offline layout, rights, and byline tests pass in local commits `dada7bf` / `9dbe99b` plus uncommitted v2 layout edits; synthetic two-photo layout preview at `data/cards/two-photo-layout-demo-v2-1.png` passed visual inspection. Push and `verify-pixabay.yml` are pending because the GitHub PAT is no longer persisted in the workspace. |
| GitHub Actions workflows | PARTIAL | CI, identity (pre-expiry), container, hourly dry-run, public test post, and LLM verification all ran; current hourly auto run is blocked by token expiry. Pixabay demo workflow is ready for dispatch. |
| Direct source access | PARTIAL | Munsif, Etemaad, and The Wire work. Indian Express returns CloudFront 403 and uses a non-publishable snapshot only. |
| Human review workflow | READY | Review queue and CLI/API approval exist; auto-approval only applies to verified editorials under the explicit auto-post flags. |
| 24/7 host deployment | READY | Dashboard preview runs on port 8000; Dockerfile, Compose, systemd unit, and `.env.example` are included. |

## Go-live order after blockers are fixed

1. Replace `FB_PAGE_ACCESS_TOKEN` with a long-lived Page token (and refresh `FB_USER_ACCESS_TOKEN` if available).
2. Provide a working LLM gateway: valid `GROK_API_KEY`, OpenAI credits, or another OpenAI-compatible base/model/key.
3. Run `verify-pixabay.yml`; inspect the two-photo demo card and credits. It does not publish.
4. Re-run `verify-facebook.yml` and `verify-llm.yml`; both must succeed.
5. Re-run `hourly-newsroom.yml`; confirm no duplicate of the existing test post and that any new post has a fresh verified source and Graph API `post_id`.
6. Rotate any credentials that were exposed during setup.
7. To stop continuous posting, run `python3 deploy/toggle_auto_posting.py --stop` or set `NR_AUTO_POST_ENABLED=false` manually.

## Validated / relevant GitHub Actions runs

- CI — tests and dry-run card: https://github.com/majeeedalimajeed-cmyk/Azad-Studio---urdu/actions/runs/40547503315
- Verify Facebook Page identity (original): https://github.com/majeeedalimajeed-cmyk/Azad-Studio---urdu/actions/runs/40547507077
- Verify Facebook Page identity (new token, pre-expiry): https://github.com/majeeedalimajeed-cmyk/Azad-Studio---urdu/actions/runs/40550747731
- Build container image: https://github.com/majeeedalimajeed-cmyk/Azad-Studio---urdu/actions/runs/40547467044
- Hourly newsroom scan (dry run): https://github.com/majeeedalimajeed-cmyk/Azad-Studio---urdu/actions/runs/40547839216
- Operator-approved public test post (success): https://github.com/majeeedalimajeed-cmyk/Azad-Studio---urdu/actions/runs/40555660776
- Verify LLM gateway: https://github.com/majeeedalimajeed-cmyk/Azad-Studio---urdu/actions/runs/40556907254
- Hourly newsroom scan (auto mode, blocked by expired token): https://github.com/majeeedalimajeed-cmyk/Azad-Studio---urdu/actions/runs/40557387623
- Page-token diagnostics (expired token): https://github.com/majeeedalimajeed-cmyk/Azad-Studio---urdu/actions/runs/40559104282
