# GitHub Actions setup — AZAD STUDIO newsroom

This repository uses **repository secrets** for credentials and **repository variables** for non-secret configuration. Secret values are never stored in source, logs, cards, captions, or the SQLite database.

## Secrets

| Name | Source | Used as |
|---|---|---|
| `FB_USER_ACCESS_TOKEN` | Meta user token with Page management permissions | `NR_FB_USER_ACCESS_TOKEN` |
| `FB_PAGE_ACCESS_TOKEN` | Page access token for the API-confirmed Page | `NR_FB_PAGE_ACCESS_TOKEN` |
| `FB_PAGE_ID` | API-confirmed Page ID from `/me/accounts` | `NR_FB_PAGE_ID` |
| `GROK_API_KEY` | xAI API key | `NR_LLM_API_KEY` |
| `OPENAI_API_KEY` | Optional fallback key | `NR_LLM_API_KEY` fallback |

The long-lived Page token belongs in `FB_PAGE_ACCESS_TOKEN`. `FB_USER_ACCESS_TOKEN` should be a long-lived user token for the initial `/me/accounts` identity check and feed reconciliation; GitHub secret values are write-only, so do not paste them into chat, source, or logs. If the user token expires after the Page has already been API-confirmed, `FacebookGraphClient.verify_identity()` falls back to the Page token and the previously confirmed Page ID/name stored in the durable SQLite cache. Refresh `FB_USER_ACCESS_TOKEN` when convenient to restore full `/me/accounts` and feed-reconciliation coverage.

## Variables

| Name | Example | Purpose |
|---|---|---|
| `NR_EXPECTED_PAGE_NAME` | `Azad Studio` | Exact expected Page name. |
| `NR_FB_PAGE_ID` | `1383450338187017` | API-confirmed Page ID lookup hint. |
| `NR_GRAPH_API_VERSION` | `v26.0` | Graph API version. |
| `NR_LLM_API_BASE` | `https://api.x.ai/v1` | OpenAI-compatible Grok base URL. |
| `NR_LLM_MODEL` | `grok-4.7` | Agent A Urdu model. |
| `NR_ROMAN_URDU_MODEL` | `grok-4.7` | Agent C Roman Urdu model. |
| `NR_VALIDATOR_MODEL` | `grok-4.6` | Independent reviewer model. |
| `NR_DRY_RUN` | `true` / `false` | Dry-run switch. |
| `NR_PUBLISH_ENABLED` | `false` / `true` | Public publishing switch. |
| `NR_PUBLISH_SAFETY_OK` | `false` / `true` | Explicit safety interlock. |
| `NR_OPERATOR_APPROVED` | `false` / `true` | Operator approval flag. |
| `NR_VERIFY_APPROVED` | `false` / `true` | Verification approval flag. |
| `NR_AUTO_APPROVE_VERIFIED` | `false` / `true` | Auto-approve verified editorials. |
| `NR_AUTO_POST_ENABLED` | `false` / `true` | Continuous auto-post kill switch. |

For live auto posting, set `NR_DRY_RUN=false`, `NR_PUBLISH_ENABLED=true`, `NR_PUBLISH_SAFETY_OK=true`, `NR_OPERATOR_APPROVED=true`, `NR_VERIFY_APPROVED=true`, `NR_AUTO_APPROVE_VERIFIED=true`, and `NR_AUTO_POST_ENABLED=true`. To stop, set `NR_PUBLISH_ENABLED=false`, `NR_AUTO_POST_ENABLED=false`, and `NR_PUBLISH_SAFETY_OK=false` (see `deploy/STOP_AUTO_POSTING.md`).

## Workflows

- **CI — tests and dry-run card**: push/PR/manual. Runs `check-assets`, `pytest`, and a verified fixture render. Uploads the PNG card as an artifact.
- **Verify Facebook Page identity**: manual. Maps secrets to env vars and runs `python -m nr_news.cli verify-facebook`. It does not post.
- **Verify LLM gateway**: manual. Checks the configured model gateway and model IDs without printing keys.
- **Operator-approved public test post**: manual. Runs the verified regional fixture through the full live publishing path with explicit operator approval, confirms the post through the Graph API, records `facebook_test_post_passed` in the durable SQLite cache, and writes `reports/TEST_POST_RESULT.md`.
- **Hourly newsroom scan**: hourly/manual. Restores the SQLite cache, verifies Page identity, runs the hourly scan and queued verification jobs, generates a report, and uploads cards/audits/reports. With the auto-post variables enabled it publishes only verified, fresh, non-crime, deduplicated editorials; otherwise it remains a dry run.
- **Build container image**: push/manual. Builds the Dockerfile and pushes to `ghcr.io/<owner>/azad-studio-urdu`.

## Editorial priority

The hourly worker claims jobs by durable priority:

1. Regional politics (Hyderabad / Telangana government, elections, courts, policy).
2. National politics.
3. International politics / diplomacy.
4. Regional public-interest news.
5. Development / economy / education / health news.
6. Other verified news.

Crime, graphic violence, suicide/accident, rumors, and unsupported allegations are excluded at ingest, verification, and generated-editorial stages.

## Local equivalent

Create a local `.env` (ignored by git) containing non-secret config plus `*_FILE` paths to 600-permission secret files. `Config._load_dotenv()` loads only keys not already present in the environment, so GitHub Actions/Docker/systemd values always win. Example:

```dotenv
NR_EXPECTED_PAGE_NAME=Azad Studio
NR_FB_PAGE_ID=1383450338187017
NR_FB_USER_ACCESS_TOKEN_FILE=/home/user/.config/nr_news/fb_token.txt
NR_FB_PAGE_ACCESS_TOKEN_FILE=/home/user/.config/nr_news/fb_page_token.txt
NR_LLM_API_BASE=https://api.x.ai/v1
NR_LLM_MODEL=grok-4.7
NR_ROMAN_URDU_MODEL=grok-4.7
NR_VALIDATOR_MODEL=grok-4.6
NR_DRY_RUN=true
NR_PUBLISH_ENABLED=false
NR_PUBLISH_SAFETY_OK=false
NR_OPERATOR_APPROVED=false
NR_VERIFY_APPROVED=false
NR_AUTO_APPROVE_VERIFIED=false
NR_AUTO_POST_ENABLED=false
```

Never paste a token into `.env`, a workflow file, an issue, or chat. Rotate any token that has been exposed.
