# Source monitoring notes — dry run

The monitor is intentionally conservative: a source outage is recorded and the source is marked unavailable rather than silently bypassed. Local snapshots are clearly marked `snapshot_only` and held before publication.

| Source | Public endpoint | Observed behavior | Dry-run fallback |
|---|---|---|---|
| Munsif Daily | `https://munsifdaily.com/feed/` | HTTP 200; RSS items with current timestamps. | None needed. Crime/suicide headlines are excluded by editorial policy. |
| Etemaad Daily | `https://www.etemaaddaily.com/index.php` | Urdu RTL home page works when DNS/network is available; `robots.txt` is an HTML page containing `meta robots=index,follow`, which the client explicitly verifies for this host. | Non-publishable `data/snapshots/etemaad_urdu_home.html` plus eight article HTML snapshots. All derived candidates carry `snapshot_only=true`. |
| The Indian Express | `https://indianexpress.com/feed/` | Direct workspace requests receive CloudFront 403 for the feed, root, robots, and WP REST endpoints. | Non-publishable `data/snapshots/indian_express_feed.xml`. All derived candidates carry `snapshot_only=true`. |
| The Wire | `https://cms.thewire.in/wp-json/wp/v2/posts` | HTTP 200 JSON with IDs, dates, links, titles, and short excerpts. | None needed. |

The Wire's ordinary `/feed`, `/feed/`, `/rss`, `/feeds`, and `/latest` return the SPA HTML shell, not RSS/XML. `cms.thewire.in/robots.txt` can be user-agent sensitive; production should verify the exact deployed user agent and access policy before enabling unattended publication.

Before any story from a snapshot becomes publishable, the operator must re-read the live article from the original source and confirm its license/access terms, freshness, and factual content. The pipeline does not promote snapshot-only candidates automatically.
