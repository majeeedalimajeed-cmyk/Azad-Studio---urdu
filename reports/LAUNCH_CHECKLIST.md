# AZAD STUDIO — launch checklist

**Decision: DO NOT ENABLE UNATTENDED PUBLIC POSTING YET.**

Brand note: the textual name has been corrected to AZAD STUDIO. The supplied NR monogram asset, layout, sources, Facebook destination, and publishing safety logic remain unchanged.

The offline pipeline, scheduler logic, card renderer, rights workflow, audit database, and dry-run fixture all pass. Live Facebook publishing is blocked by design because Page identity, credentials, permissions, a production LLM gateway, stable direct source access, and an operator-approved test post have not been confirmed.

## Required behavior tests

| # | Test | Status | Evidence |
|---|---|---|---|
| 1 | All four sources are monitored | PASS | `reports/DRY_RUN_REPORT.md` shows Munsif and The Wire OK; Etemaad and Indian Express checked with logged outages and non-publishable snapshots. `reports/SOURCE_MONITOR_NOTES.md` records endpoints. |
| 2 | Stories are fresh and verified | PASS | Verified TGPSC fixture uses a 26 Oct 2026 Etemaad article and an official 26 Oct 2026 TGPSC web note. VerificationAgent checks timestamps, claim status, evidence URLs and independent corroboration. |
| 3 | Crime and murder reports are excluded | PASS | Regression test and explicit exclusion rules. The dry-run source scan contains crime headlines that produced no card. |
| 4 | Urdu summaries preserve original facts | PASS | Bilingual fixture, fact inventory, entity/number consistency checks. No complete article text is stored or reproduced. |
| 5 | Roman Urdu matches approved Urdu | PASS | Agent C receives only approved Urdu headline/bullets/verification notes; independent reviewer plus deterministic checks preserve names, dates, figures and uncertainty. |
| 6 | Exact supplied monogram appears correctly | PASS | SHA-256-gated asset, proportional black-margin crop only, exact top/bottom placement, and visual inspection of the final card. |
| 7 | Card layout is readable and consistent | PASS | Final 1080×1350 card at `data/cards/2026-10-27-94e197bbcbe7-1.png`; RTL Urdu and LTR Roman Urdu render with Noto fonts and libraqm. |
| 8 | Duplicate stories are blocked | PASS | Canonical URL, content hash, cluster similarity, idempotency keys, and regression tests. |
| 9 | Empty hourly runs publish nothing | PASS | Empty-scan test asserts zero cards, captions, and publish attempts. |
| 10 | Breaking-news verification starts promptly | PASS | 5-minute breaking scheduler and fresh-candidate priority job test. |
| 11 | Facebook publishing targets only the correct Page | HOLD | Only `https://www.facebook.com/profile.php?id=61595929711037` is configured. The numeric profile ID is not treated as a Page ID. API identity resolution, exact Page name, permissions, and token are still required. |
| 12 | API results, errors, retries are recorded | PASS | SQLite `api_events`, `publish_attempts`, `jobs`, and source outage logging. A simulated Graph API failure test records the error; success requires an API `post_id`. |

## Operational holds

| Item | Status | Required action |
|---|---|---|
| Dry-run mode | PASS | Currently `NR_DRY_RUN=true`, `NR_PUBLISH_ENABLED=false`, `NR_OPERATOR_APPROVED=false`. Keep these until all holds clear. |
| Exact supplied monogram hash | PASS | Re-run `check-assets` after deployment. |
| Fonts / RTL shaping | PASS | Re-run `check-assets` with libraqm installed. |
| Offline unit/integration tests | PASS | `13 passed` via `pytest` and `nr_news.cli selftest`. |
| Production LLM gateway | HOLD | Configure `NR_LLM_API_KEY`, a primary model, a separate Agent C model, and an independent reviewer model. |
| Direct source access | HOLD | Etemaad DNS and Indian Express 403 are inconsistent from this workspace. Confirm stable permitted production access; retain snapshots only as non-publishable monitoring aids. |
| Human review workflow | READY / HOLD | Review queue and API/CLI approval exist. An operator must approve real editorials before publication. |
| Facebook Page ownership/admin rights | HOLD | User must confirm. |
| Exact Page name / Page ID | HOLD | Provide `NR_EXPECTED_PAGE_NAME`; run `verify-facebook` with a user token and Page token. |
| Page permissions / tasks | HOLD | Confirm `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, `CREATE_CONTENT`, `MANAGE`, `MODERATE`. |
| Page access token | HOLD | Provide via secret file or secure environment variable. Never paste it into this conversation, source, logs, or images. |
| Operator-approved test post | HOLD | Render and visually inspect a real card, run `test-publish`, confirm the Graph API `post_id` and visible Page location. |
| 24/7 host deployment | READY / HOLD | A live dashboard/scheduler preview is running on port 8000 in this workspace. Dockerfile, Compose, systemd unit, and `.env.example` are included for deployment to an always-on host with TLS, backups, monitoring, and secret management. |
| Optional 1080×1920 Shorts/Reels | NOT BLOCKING | Scaffolding exists; audio/video generation requires additional tooling and is disabled. |

## Go-live order

1. Confirm Page ownership/admin rights and exact Page name.
2. Configure a secure production LLM gateway and secret storage.
3. Verify direct source access and robots policies from the production host.
4. Deploy the service and re-run `check-assets`, `selftest`, and a manual hourly scan.
5. Verify Page identity via the Graph API and record the API-confirmed Page ID.
6. Approve one real verified editorial, inspect its card, and run an operator-approved test post.
7. Confirm the returned `post_id` and the post on the correct Page.
8. Only then set `NR_DRY_RUN=false`, `NR_PUBLISH_ENABLED=true`, `NR_PUBLISH_SAFETY_OK=true`, and `NR_OPERATOR_APPROVED=true`.

Until step 8 is complete, all outputs are dry-run artifacts and no public post should be claimed as published.
