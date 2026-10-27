# AZAD STUDIO — continuous AI-assisted news operation

**Status: DRY RUN. No public post has been made. Unattended publishing must stay disabled until the launch checklist is complete.**

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
- Live publishing additionally requires `NR_DRY_RUN=false`, `NR_PUBLISH_ENABLED=true`, `NR_PUBLISH_SAFETY_OK=true`, `NR_OPERATOR_APPROVED=true`, a passed visual inspection, `NR_FB_USER_ACCESS_TOKEN`, `NR_FB_PAGE_ACCESS_TOKEN`, `NR_FB_PAGE_ID`, `NR_EXPECTED_PAGE_NAME`, API-confirmed Page identity, and an operator-approved successful test post.
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
python3 -m nr_news.cli test-publish --editorial-id EDITORIAL_ID --i-understand-this-posts-publicly
python3 -m nr_news.cli report
python3 -m nr_news.cli serve
```

`python3 -m nr_news.cli selftest` runs the offline suite and writes the 12 required behavior checks to `reports/TEST_RESULTS.md`. A separate `fixture` command runs the end-to-end dry-run card and caption.

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

The repository includes four workflows in `.github/workflows/` (full mapping: `deploy/github-actions.md`):

- `ci.yml` — runs tests, checks the exact logo/fonts, and renders a dry-run fixture card on every push/PR.
- `verify-facebook.yml` — manually resolves and confirms the Facebook Page through the Graph API.
- `hourly-newsroom.yml` — scheduled hourly scan/verification using the durable SQLite cache; stays in dry run by default.
- `container.yml` — builds and pushes the Docker image to GitHub Container Registry.

All four workflows have been dispatched successfully in dry-run mode; see `reports/LAUNCH_CHECKLIST.md` for run links.

Required **repository secrets**:

| Secret | Purpose |
|---|---|
| `FB_USER_ACCESS_TOKEN` | User token used for `/me/accounts` and `/me/permissions` identity checks. |
| `FB_PAGE_ACCESS_TOKEN` | Page token used for `/{page_id}` verification and photo publishing. |
| `FB_PAGE_ID` | API-confirmed Page ID lookup hint. |
| `GROK_API_KEY` | xAI key mapped to `NR_LLM_API_KEY` for Agent A/B/C and the reviewer. |
| `OPENAI_API_KEY` | Optional fallback if the gateway is switched back to OpenAI. |

Required **repository variables**: `NR_EXPECTED_PAGE_NAME`, `NR_FB_PAGE_ID`, `NR_GRAPH_API_VERSION`, `NR_LLM_API_BASE`, `NR_LLM_MODEL`, `NR_ROMAN_URDU_MODEL`, `NR_VALIDATOR_MODEL`, `NR_DRY_RUN`, and `NR_PUBLISH_ENABLED`.

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
8. Approve a real candidate in the review queue, inspect the final rendered card, and run `test-publish` with explicit operator approval.
9. Confirm the returned Graph API `post_id` and the post's visible Page location.
10. Enable live publishing only after an operator signs off on the full test post and audit record.

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
