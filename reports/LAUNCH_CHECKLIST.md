# AZAD STUDIO — launch checklist

**Decision: DO NOT ENABLE UNATTENDED PUBLIC POSTING YET.**

Brand note: the textual name has been corrected to AZAD STUDIO. The supplied NR monogram asset, layout, sources, and publishing safety logic remain unchanged. The originally configured `profile.php?id=61595929711037` is retained only as a lookup hint; the Graph API-confirmed Page ID `1383450338187017` and canonical Page link are now the publishing identity.

The offline pipeline, scheduler logic, card renderer, rights workflow, audit database, dry-run fixture, and Facebook Page identity verification all pass. Live publishing remains blocked by design because a production LLM key is not available locally, one source still has restricted direct access, and an operator-approved test post has not been run.

## Required behavior tests

| # | Test | Status | Evidence |
|---|---|---|---|
| 1 | All four sources are monitored | PASS | `reports/DRY_RUN_REPORT.md` shows Munsif, Etemaad, and The Wire OK; Indian Express is checked with a logged 403 outage and a clearly marked non-publishable snapshot. `reports/SOURCE_MONITOR_NOTES.md` records endpoints. |
| 2 | Stories are fresh and verified | PASS | Verified TGPSC fixture uses a 26 Oct 2026 Etemaad article and an official 26 Oct 2026 TGPSC web note. VerificationAgent checks timestamps, claim status, evidence URLs and independent corroboration. |
| 3 | Crime and murder reports are excluded | PASS | Regression test and explicit exclusion rules. The dry-run source scan contains crime headlines that produced no card. |
| 4 | Urdu summaries preserve original facts | PASS | Bilingual fixture, fact inventory, entity/number consistency checks. No complete article text is stored or reproduced. |
| 5 | Roman Urdu matches approved Urdu | PASS | Agent C receives only approved Urdu headline/bullets/verification notes; independent reviewer plus deterministic checks preserve names, dates, figures and uncertainty. |
| 6 | Exact supplied monogram appears correctly | PASS | SHA-256-gated asset, proportional black-margin crop only, exact top/bottom placement, and visual inspection of the final card. |
| 7 | Card layout is readable and consistent | PASS | Final 1080×1350 card at `data/cards/2026-10-27-ff3cec5b3340-1.png`; RTL Urdu and LTR Roman Urdu render with Noto fonts and libraqm. |
| 8 | Duplicate stories are blocked | PASS | Canonical URL, content hash, cluster similarity, idempotency keys, and regression tests. |
| 9 | Empty hourly runs publish nothing | PASS | Empty-scan test asserts zero cards, captions, and publish attempts. |
| 10 | Breaking-news verification starts promptly | PASS | 5-minute breaking scheduler and fresh-candidate priority job test. |
| 11 | Facebook publishing targets only the correct Page | PASS | Graph API `verify-identity` confirmed Page ID `1383450338187017`, name `Azad Studio`, permissions `pages_manage_posts`/`pages_read_engagement`/`pages_show_list`, and tasks `CREATE_CONTENT`/`MANAGE`/`MODERATE`. The numeric profile ID is not treated as the publishing Page ID. |
| 12 | API results, errors, retries are recorded | PASS | SQLite `api_events`, `publish_attempts`, `jobs`, and source outage logging. A simulated Graph API failure test records the error; success requires an API `post_id`. |

## Operational status

| Item | Status | Notes / required action |
|---|---|---|
| Dry-run mode | PASS | `NR_DRY_RUN=true`, `NR_PUBLISH_ENABLED=false`, `NR_OPERATOR_APPROVED=false`. Keep these until all holds clear. |
| Exact supplied monogram hash | PASS | Re-run `check-assets` after deployment. |
| Fonts / RTL shaping | PASS | Re-run `check-assets` with libraqm installed. |
| Offline unit/integration tests | PASS | `13 passed` via `pytest` and `nr_news.cli selftest`. |
| GitHub Actions secrets / variables | PASS | Repository secrets `FB_USER_ACCESS_TOKEN`, `FB_PAGE_ACCESS_TOKEN`, `FB_PAGE_ID`, `GROK_API_KEY`, `OPENAI_API_KEY` and Grok/xAI variables are configured. Workflows are in `.github/workflows/`. |
| Production LLM gateway | PARTIAL | GitHub Actions maps `GROK_API_KEY` to `NR_LLM_API_KEY` with `https://api.x.ai/v1`, `grok-4.7`, and reviewer `grok-4.6`. A local key file is not present, so local LLM jobs remain held. Provide a 600-permission key file only if local generation is needed. |
| Direct source access | PARTIAL | Munsif, Etemaad, and The Wire direct monitoring works. Indian Express still returns CloudFront 403 from this workspace; its non-publishable snapshot remains a monitoring aid only. Reconfirm from the production host. |
| Human review workflow | READY / HOLD | Review queue and API/CLI approval exist. An operator must approve real editorials before publication. |
| Facebook Page ownership/admin rights | API VERIFIED / USER CONFIRMATION PENDING | `/me/accounts` returned `MANAGE`, `CREATE_CONTENT`, and `MODERATE` tasks for the Page. The operator should still explicitly confirm ownership/admin rights. |
| Exact Page name / Page ID | PASS | `NR_EXPECTED_PAGE_NAME=Azad Studio`; `NR_FB_PAGE_ID=1383450338187017`; `verify-facebook` succeeded. |
| Page permissions / tasks | PASS | `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, `CREATE_CONTENT`, `MANAGE`, `MODERATE` confirmed via Graph API. |
| Page access token | PASS / ROTATE LATER | User and Page tokens are stored only in 600-permission files outside the repo and as GitHub Actions secrets. Because token material was pasted into chat, rotate/revoke it when the project is complete. |
| Operator-approved test post | HOLD | Render and visually inspect a real card, run `test-publish`, confirm the Graph API `post_id` and visible Page location. |
| 24/7 host deployment | READY / HOLD | A live dashboard/scheduler preview runs on port 8000 in this workspace. Dockerfile, Compose, systemd unit, and `.env.example` are included for deployment to an always-on host with TLS, backups, monitoring, and secret management. |
| Optional 1080×1920 Shorts/Reels | NOT BLOCKING | Scaffolding exists; audio/video generation requires additional tooling and is disabled. |

## Go-live order

1. Operator explicitly confirms Page ownership/admin rights.
2. Provide the Grok key locally only if local generation is required; otherwise rely on GitHub Actions secrets.
3. Verify direct Indian Express access and robots policies from the production host.
4. Deploy the service and re-run `check-assets`, `selftest`, `verify-facebook`, and a manual hourly scan.
5. Approve one real verified editorial, inspect its card, and run an operator-approved test post.
6. Confirm the returned `post_id` and the post on the API-confirmed Page.
7. Only then set `NR_DRY_RUN=false`, `NR_PUBLISH_ENABLED=true`, `NR_PUBLISH_SAFETY_OK=true`, and `NR_OPERATOR_APPROVED=true`.
8. Rotate any credentials that were exposed during setup.

Until step 7 is complete, all outputs are dry-run artifacts and no public post should be claimed as published.
