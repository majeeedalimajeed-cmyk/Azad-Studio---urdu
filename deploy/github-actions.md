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

## Variables

| Name | Example |
|---|---|
| `NR_EXPECTED_PAGE_NAME` | `Azad Studio` |
| `NR_FB_PAGE_ID` | `1383450338187017` |
| `NR_GRAPH_API_VERSION` | `v26.0` |
| `NR_LLM_API_BASE` | `https://api.x.ai/v1` |
| `NR_LLM_MODEL` | `grok-4.7` |
| `NR_ROMAN_URDU_MODEL` | `grok-4.7` |
| `NR_VALIDATOR_MODEL` | `grok-4.6` |
| `NR_DRY_RUN` | `true` |
| `NR_PUBLISH_ENABLED` | `false` |

## Workflows

- **CI — tests and dry-run card**: push/PR/manual. Runs `check-assets`, `pytest`, and a verified fixture render. Uploads the PNG card as an artifact.
- **Verify Facebook Page identity**: manual. Maps secrets to env vars and runs `python -m nr_news.cli verify-facebook`. It does not post.
- **Hourly newsroom scan**: hourly/manual. Restores the SQLite cache, verifies Page identity, runs the hourly scan and queued verification jobs, generates a report, and uploads cards/audits/reports. It stays in dry run until the variables are changed and all launch holds clear.
- **Build container image**: push/manual. Builds the Dockerfile and pushes to `ghcr.io/<owner>/azad-studio-urdu`.

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
```

Never paste a token into `.env`, a workflow file, an issue, or chat. Rotate any token that has been exposed.
