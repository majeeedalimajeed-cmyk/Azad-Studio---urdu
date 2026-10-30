# AZAD STUDIO — Publishing Plan (from 29 Oct 2026)

Operator decisions, locked in. While the YouTube channel upload limit
lasts, only **3 videos per day**; the count increases next month.

## Daily rhythm (this month)

1. **Morning session** — the best morning news, **10 stories**
   (headline + one key point each, spoken welcome first, matched photos).
2. **Breaking news** — whenever something big breaks, one video with
   **full details, immediately**. Breaking items are taken from the
   Facebook desk's own update scans (the same candidate pool the
   Facebook/Instagram automation checks), then verified before air.
   Watch especially: the **1 November INDIA-bloc protest in Delhi**,
   government decisions, war news, sports news.
3. **Evening session** — the day's important political news, **10 stories**.

After a week the desk moves to 2–3 minute sessions; from next month
the number of daily videos increases beyond three.

## House rules (all videos, YouTube + Facebook)

- **No repeats:** every published story is recorded in
  `story_log.json`; builders refuse to reuse a logged story.
- **Format by length:** over 90 seconds → landscape 1920×1080 (16:9);
  under 90 seconds → portrait 1080×1920 (9:16, Shorts).
- **Photos must match the news** — pinned/verified photos per story on
  YouTube; relevant photos on Facebook cards too (no generic filler).
- **Spoken welcome first** over the channel intro; low soft news bed
  under the anchor throughout; hard cuts (no blinking); Urdu headlines
  ticker; **sources are never mentioned in any video** (audit notes only).
- Neutral and fair: reports are labelled reports, allegations are
  labelled allegations — never presented as confirmed facts.

## Builders

- `morning_short_2026-10-29.py` — the 29 Oct 60-second morning Short.
- `session_video.py <session.json>` — any dated morning/evening/breaking
  session (see `sessions/`), with dedup + photo fetch built in.
- `bulletin_2026-10-28.py` — the long-form bulletin pattern.

## Real-footage political videos (adopted 29 Oct 2026)

Political packages are now built primarily from **authentic footage**
of politicians speaking (parliament, press conferences, rallies,
interviews), under `FOOTAGE_PROTOCOL.md`: original voice only (no AI
anchor over speech), 9:16 vertical, Urdu subtitles checked word by
word, verified speaker/event/date, rights checked and recorded, and
a completed review report (`footage_review_template.md`) with human
approval before anything is published. **Nothing auto-publishes.**
Builder: `footage_clip.py` (rights-cleared local file + exact
timestamps in, packaged vertical out).
