# AZAD STUDIO — continuous AI-assisted news operation

**Status: AUTO POST MODE ENABLED. One operator-approved public test post is live on the AZAD STUDIO Page. Continuous generation is currently blocked because both Facebook tokens expired at 23:30 IST and no working LLM gateway is configured; see `reports/LAUNCH_CHECKLIST.md`. To stop, set `NR_AUTO_POST_ENABLED=false`.**

This is the AZAD STUDIO newsroom operation. The supplied reference card used an NR monogram; that exact asset is retained byte-for-byte and unchanged. Only the textual brand name has been updated to AZAD STUDIO. It publishes only to the API-confirmed Facebook Page `Azad Studio` (ID `1383450338187017`, `https://www.facebook.com/1383450338187017`). The original `https://www.facebook.com/profile.php?id=61595929711037` remains only as a lookup hint and audit note.

The supplied gold-on-black monogram from the reference card is used byte-for-byte from `uploads/assets.png`. It is never redrawn, recolored, distorted, or substituted. The renderer refuses to draw a card if the logo's SHA-256 does not match `d0b02c4e7f31f4f58d1e48c08fd3c8d6668bcbbe9e15ef0652a6ff584ede2b3d`.

---

## What is implemented

### Agents

| Agent | Module | Responsibility |
|---|---|---|
| A — Research | `nr_news/agents/research.py`, `nr_news/sources.py` | Poll permitted public pages/RSS/APIs, extract short factual excerpts, deduplicate, cluster similar stories, triage importance, and queue verification. |
| B — Verification / editorial control | `nr_news/agents/verification.py` | Check freshness, classify claims as confirmed / alleged / proposed / attributed / developing, fetch official PDFs/pages where available, corroborate sensitive claims from genuinely independent sources, exclude crime/rumor/sensationalism, and hold uncertain stories for human review. |
| C — Roman Urdu translator | `nr_news/agents/translator.py` | Separate model role. Receives only the approved Urdu headline, bullets, and verification notes; preserves names, dates, figures, titles, and uncertainty without adding opinion. |
| C-check — independent reviewer | same module, separate configured model | Compares Urdu and Roman Urdu for entity, numeric, and uncertainty drift. Disagreements are held. |
| D — Card rendering | `nr_news/agents/card.py`, `nr_news/renderer.py` | Deterministic 1080×1350 black/gold/white card with exact `LATEST NEWS` heading, IST date/time, RTL Urdu, LTR Roman Urdu, exact supplied monogram top and bottom, text-led fallback, and programmatic overflow/visual checks. |
| E — Media rights | `nr_news/agents/media.py` | Rejects source images without explicit credit/license/permission. Never presents AI-generated imagery as a real news photograph. Produces a text-led card when no authentic cleared photo exists. |
| Publisher | `nr_news/agents/publisher.py`, `nr_news/facebook.py` | Official Meta Graph API v26.0 Page photo publishing only. Records attempts, post IDs, timestamps, captions, source URLs, API errors, and retries. Never claims success without an API confirmation. |
| Optional video | `nr_news/agents/video.py` | Generates scripts, subtitles, and storyboards for 1080×1920 Shorts/Reels. It does not generate audio/video without additional TTS/video tooling and never blocks card publishing. |

### Sources

| Source | Public endpoint used | Current dry-run behavior |
|---|---|---|
| Munsif Daily | `https://munsifdaily.com/feed/` and permitted WP REST articles | Direct RSS works from the workspace. Crime/suicide headlines are excluded. |
| Etemaad Daily | `https://www.etemaaddaily.com/index.php` Urdu edition, with canonical-host fallback | Direct Urdu home page and article fetching works after fixing a domain typo. Non-publishable HTML snapshots remain only as an outage fallback. |
| The Indian Express | `https://indianexpress.com/feed/` | Direct requests receive CloudFront 403 from this sandbox. A clearly marked non-publishable RSS snapshot in `data/snapshots/` is used only for monitoring and outage logging. |
| The Wire | `https://cms.thewire.in/wp-json/wp/v2/posts` | Public REST API works. The ordinary `/feed` routes return the site's SPA shell and are not used. |

All source requests use a polite user agent, a per-host minimum interval, conditional GETs, timeouts, size limits, redirects, robots checks, and retry/backoff logic. A 403 or unavailable robots policy is treated as a source outage, not bypassed. Snapshot-only candidates are marked `raw.snapshot_only = true` and held; they can never produce a publishable card without a fresh direct re-read.

### Editorial policy and priority

The operator's current policy is **no crime news, more political news, regional first and then the rest**. The durable job queue now orders work by priority:

1. Regional politics: Hyderabad / Telangana government, elections, courts and policy.
2. National politics.
3. International politics and diplomacy.
4. Regional public-interest news.
5. Development, economy, education and health news.
6. Other verified news.

Crime, graphic violence, suicide/accident, rumors and unsupported allegations are excluded at three independent stages: source ingest, Agent B verification, and the generated bilingual editorial. The expanded filter covers police cases, arrests, accused persons, bail, FIRs, fraud, scams, harassment, drugs, encounters and related terms in English and Urdu.

### Schedule and durability

`nr_news/service.py` runs a persistent scheduler, worker, and HTTP dashboard. It uses Asia/Kolkata time and a durable SQLite queue/database. Defaults:

- hourly source scans;
- breaking-news checks every 5 minutes for candidate articles less than 25 minutes old;
- a 07:00 IST morning scan;
- safe retry/backoff for queued jobs;
- empty-hour behavior: no qualifying story means no card, no caption, and no publish attempt.

Database tables include `source_status`, `clusters`, `candidates`, `review_queue`, `editorials`, `cards`, `media_assets`, `publish_attempts`, `api_events`, `jobs`, and `settings`. Deduplication uses canonical URLs, content hashes, cluster similarity, and publish idempotency keys.

### Facebook safety

- Graph API version: **v26.0**, the current released version at build time. See Meta's [version history](https://developers.facebook.com/docs/graph-api/changelog/versions/) and [Pages API getting started](https://developers.facebook.com/docs/pages-api/getting-started/).
- Photo publishing uses `POST /v26.0/{page_id}/photos` with `source`, `caption`, and `published=true`, per the [Pages API posts documentation](https://developers.facebook.com/documentation/pages-api/posts).
- Required permissions/tasks include `pages_manage_posts`, `pages_read_engagement`, `pages_show_list`, and Page tasks `CREATE_CONTENT`, `MANAGE`, `MODERATE`. Confirm them in Meta's [permissions reference](https://developers.facebook.com/docs/permissions).
- The numeric profile ID `61595929711037` is **not** assumed to be a Page ID. `FacebookGraphClient.verify_identity()` resolves the configured profile URL, an explicit `NR_FB_PAGE_ID`, or the exact expected Page name via `/me/accounts`; it verifies required permissions/tasks, checks the Page token against `/{page_id}`, and stores the confirmed Page ID/name/time in the database.
- Dry-run Graph API verification has confirmed the publishing identity as **Page ID `1383450338187017`**, name **`Azad Studio`**, canonical link `https://www.facebook.com/1383450338187017`. The original profile URL is kept only as a lookup hint/audit note.
- If the short-lived user token expires, the Page is re-verified directly with the long-lived Page token, the configured Page ID, the exact expected name, and required Page tasks/permissions. A fresh database can therefore bootstrap from a valid Page token alone. Refresh `FB_USER_ACCESS_TOKEN` when convenient to restore full `/me/accounts` and feed reconciliation.
- Live publishing additionally requires `NR_DRY_RUN=false`, `NR_PUBLISH_ENABLED=true`, `NR_PUBLISH_SAFETY_OK=true`, `NR_OPERATOR_APPROVED=true`, `NR_VERIFY_APPROVED=true`, `NR_AUTO_APPROVE_VERIFIED=true`, `NR_AUTO_POST_ENABLED=true`, a passed visual inspection, `NR_FB_USER_ACCESS_TOKEN`, `NR_FB_PAGE_ACCESS_TOKEN`, `NR_FB_PAGE_ID`, `NR_EXPECTED_PAGE_NAME`, API-confirmed Page identity, and an operator-approved successful test post. `NR_AUTO_POST_ENABLED` is the continuous auto-post kill switch; set it to `false` to stop.
- Secrets are read from environment variables or `*_FILE` paths. They are never written to source, logs, captions, cards, or prompts.

---

## Run locally / in this workspace

```bash
cd /home/user/nr_news
python3 -m pytest -q tests
python3 -m nr_news.cli init
python3 -m nr_news.cli fixture
python3 -m nr_news.cli scan --mode hourly --drain-limit 100
python3 -m nr_news.cli report --output reports/DRY_RUN_REPORT.md
python3 -m nr_news.cli serve
```

The dashboard listens on `0.0.0.0:${NR_PORT:-8000}`. Useful endpoints:

- `GET /health`
- `GET /api/status`
- `GET /api/cards`
- `GET /api/review`
- `GET /cards/<filename>`
- `POST /api/scan` with `NR_ADMIN_TOKEN`
- `POST /api/approve` with `NR_ADMIN_TOKEN`

Important CLI commands:

```bash
python3 -m nr_news.cli check-assets
python3 -m nr_news.cli selftest
python3 -m nr_news.cli scan --mode hourly|breaking|morning --drain-limit 100
python3 -m nr_news.cli worker --limit 100
python3 -m nr_news.cli approve --editorial-id EDITORIAL_ID
NR_EXPECTED_PAGE_NAME="EXACT PAGE NAME" python3 -m nr_news.cli verify-facebook
python3 -m nr_news.cli bootstrap-test-post --i-understand-this-posts-publicly
python3 -m nr_news.cli test-publish --editorial-id EDITORIAL_ID --i-understand-this-posts-publicly
python3 -m nr_news.cli report
python3 -m nr_news.cli serve
```

`bootstrap-test-post` is the launch command: it renders the verified regional TGPSC fixture, marks it operator-approved, publishes one public test post, confirms the post ID through the Graph API, and writes `reports/TEST_POST_RESULT.md`. The `--i-understand-this-posts-publicly` flag is the explicit operator consent.

`python3 -m nr_news.cli selftest` runs the offline suite and writes the required behavior checks to `reports/TEST_RESULTS.md`. A separate `fixture` command runs the end-to-end dry-run card and caption.

---

## 24/7 deployment

The repository contains:

- `Dockerfile` — installs Pillow/RTL runtime dependencies and starts `python -m nr_news.cli serve`.
- `docker-compose.yml` — restart-unless-stopped service with persistent `data/` and `reports/` volumes.
- `deploy/nr-news.service` — hardened systemd unit using `/etc/nr-news.env` for secrets.
- `.env.example` — safe configuration template with secret-file paths.

Docker:

```bash
cd /home/user/nr_news
cp .env.example .env
# Fill only secret values / model names / exact Page name in a secure way.
docker compose up -d --build
docker compose logs -f
```

Systemd:

```bash
sudo useradd --system --home /opt/nr-news --shell /usr/sbin/nologin nrnews
sudo mkdir -p /opt/nr-news/data /opt/nr-news/reports
sudo cp -a /home/user/nr_news/. /opt/nr-news/
sudo python3 -m venv /opt/nr-news/venv
sudo /opt/nr-news/venv/bin/pip install -r /opt/nr-news/requirements.txt
sudo install -m 600 /path/to/nr-news.env /etc/nr-news.env
sudo cp /opt/nr-news/deploy/nr-news.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now nr-news
```

Use a real always-on host, reverse proxy/TLS, log rotation, database backups, secret rotation, and uptime alerting. Keep the container or service in dry run until the launch checklist passes.

### GitHub Actions and repository secrets

The repository includes six workflows in `.github/workflows/` (full mapping: `deploy/github-actions.md`):

- `ci.yml` — runs tests, checks the exact logo/fonts, and renders a dry-run fixture card on every push/PR.
- `verify-facebook.yml` — manually resolves and confirms the Facebook Page through the Graph API.
- `verify-llm.yml` — manually checks the configured LLM gateway and model IDs without printing keys.
- `test-post.yml` — manually publishes one operator-approved public test post and confirms it through the Graph API.
- `hourly-newsroom.yml` — scheduled hourly scan/verification using the durable SQLite cache; publishes only when all auto-post safety variables are enabled, otherwise remains a dry run.
- `container.yml` — builds and pushes the Docker image to GitHub Container Registry.

The CI, identity, hourly scan and container workflows were validated successfully in dry-run mode; see `reports/LAUNCH_CHECKLIST.md` for run links. The public test-post workflow is dispatched only after explicit operator approval. The LLM verification workflow currently shows that `GROK_API_KEY` is rejected by xAI and `OPENAI_API_KEY` has no credits; new AI-generated stories are held for review until a working gateway is provided.

Required **repository secrets**:

| Secret | Purpose |
|---|---|
| `FB_USER_ACCESS_TOKEN` | User token used for `/me/accounts` and `/me/permissions` identity checks. |
| `FB_PAGE_ACCESS_TOKEN` | Page token used for `/{page_id}` verification and photo publishing. |
| `FB_PAGE_ID` | API-confirmed Page ID lookup hint. |
| `GROK_API_KEY` | xAI key mapped to `NR_LLM_API_KEY` for Agent A/B/C and the reviewer. |
| `OPENAI_API_KEY` | Optional fallback if the gateway is switched back to OpenAI. |

Required **repository variables**: `NR_EXPECTED_PAGE_NAME`, `NR_FB_PAGE_ID`, `NR_GRAPH_API_VERSION`, `NR_LLM_API_BASE`, `NR_LLM_MODEL`, `NR_ROMAN_URDU_MODEL`, `NR_VALIDATOR_MODEL`, `NR_DRY_RUN`, `NR_PUBLISH_ENABLED`, `NR_PUBLISH_SAFETY_OK`, `NR_OPERATOR_APPROVED`, `NR_VERIFY_APPROVED`, `NR_AUTO_APPROVE_VERIFIED`, and `NR_AUTO_POST_ENABLED`.

To stop continuous posting, set `NR_AUTO_POST_ENABLED=false` and `NR_PUBLISH_ENABLED=false` (and `NR_PUBLISH_SAFETY_OK=false` / `NR_DRY_RUN=true` for a full reset). See `deploy/STOP_AUTO_POSTING.md` and `deploy/toggle_auto_posting.py` for the exact kill-switch procedure.

The Grok gateway is OpenAI-compatible at `https://api.x.ai/v1`; current model IDs such as `grok-4.7` are documented by xAI [1](https://docs.x.ai/overview) and [11](https://docs.x.ai/developers/models). A local `.env` may contain non-secret config and `*_FILE` paths; it is ignored by git and never contains token values. GitHub Actions secrets are write-only and are injected as environment variables at runtime.

---

## Launch checklist — required before live publication

1. Operator explicitly confirms Page ownership/admin rights; Graph API tasks already show `MANAGE`, `CREATE_CONTENT`, and `MODERATE`.
2. Exact Page name is set in `NR_EXPECTED_PAGE_NAME`; API-confirmed Page ID is set in `NR_FB_PAGE_ID`.
3. User and Page tokens are supplied through secret files/GitHub Actions secrets; rotate any token exposed during setup.
4. `verify-facebook` has confirmed the API-resolved Page ID/name, not merely the numeric profile URL hint.
5. Production LLM gateway is configured in GitHub Actions with `GROK_API_KEY`, `grok-4.7`, and reviewer `grok-4.6`; provide a local key file only if local generation is needed.
6. Verify direct production access to all four sources; Munsif, Etemaad, and The Wire are working, while Indian Express still needs production-host confirmation.
7. Reconfirm the exact supplied monogram hash and fonts after deployment.
8. Inspect the final rendered card and run `bootstrap-test-post --i-understand-this-posts-publicly` (or the `test-post.yml` workflow) with explicit operator approval.
9. Confirm the returned Graph API `post_id` and the post's visible Page location; `reports/TEST_POST_RESULT.md` records the independent Graph API confirmation.
10. Enable live auto posting only after an operator signs off on the full test post and audit record, then set the continuous auto-post variables. Use `NR_AUTO_POST_ENABLED=false` as the standing stop switch.

See `reports/LAUNCH_CHECKLIST.md` for the current pass/hold matrix and `reports/DRY_RUN_REPORT.md` for the generated audit snapshot.

---

## Fonts and licenses

- `nr_news/assets/NotoNastaliqUrdu.ttf` and `nr_news/assets/NotoSans.ttf` are bundled under the SIL Open Font License; license texts are in the same directory.
- The exact supplied monogram asset and its SHA-256 are verified before every render.
- Pillow with libraqm is required for correct RTL shaping; the Docker image installs `libraqm0`.

---

## Explicit exclusions

- No murder reports, crime-focused stories, graphic violence, rumors, fabricated news, misleading allegations, or sensationalism.
- No complete article reproduction.
- No copyrighted newspaper images without authorization.
- No AI-generated image presented as a real news photograph.
- No newspaper names or logos on cards.
- No other publisher's branding, configuration, or destination.
- No invented facts, quotes, sources, statistics, or photographs.
- No allegations stated as facts, proposals stated as approved decisions, or repeated-wire reports treated as independent corroboration.
