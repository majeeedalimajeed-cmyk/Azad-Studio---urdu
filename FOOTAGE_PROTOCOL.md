# AZAD STUDIO — Real Political Footage Newsroom
# Strict Video Verification & Publication Safety Protocol

Adopted 29 Oct 2026. This protocol governs every real-footage political
video. It overrides any faster workflow wherever the two conflict.

## Editorial model

- Political news videos are built **primarily from authentic footage**
  of politicians speaking: parliament, press conferences, rallies,
  interviews, public events.
- The politician's **original voice and audio** are kept. **No AI anchor
  voiceover is ever laid over a speech.** Context, if needed, is a short
  Urdu text introduction or a clearly separate explanation before/after
  the clip.
- Format: professional **9:16 vertical** for mobile, unless another
  format is requested. Subtle AZAD STUDIO branding, accurate Urdu
  subtitles, concise headline, speaker's name, event date (when
  verified). Speaker stays visible; audio stays clear.
- India and Telangana politics are prioritised when relevant.

## Workflow (never skip a stage)

1. **Research** — find the current development; locate the original
   video or an authorised news-footage source; verify speaker, event,
   date, context and key claims.
2. **Source verification** — record the original source URL and
   uploader. Compare the selected clip against the full speech /
   original recording wherever possible. A social-media repost, public
   availability, or an AI summary is NOT proof of authenticity.
3. **Authenticity check** — look for suspicious cuts, altered audio,
   misleading edits, mismatched lip movement, synthetic visuals, old
   footage presented as current. Flag anything that cannot be
   confidently verified. Never claim genuine/fake without evidence.
   Never fabricate footage, quotes, reactions or events.
4. **Copyright & rights** — check licence/permission/public-domain
   status and record the evidence and conditions. Music, background
   footage, photos and third-party content are checked separately.
   Publicly viewable ≠ copyright-free. Credit is recorded where
   required, but credit alone is not permission. **If the legal basis
   is unclear: STOP and request human review.**
5. **Clip selection** — the most important **30–90 seconds**, with
   accurate timestamps and enough surrounding context to represent
   the statement fairly. No fabricated quotes; never splice unrelated
   statements together.
6. **Accuracy** — separate verified facts from allegations, opinions
   and unproven claims; verify important claims with reliable
   independent sources; preserve meaning and context; never edit to
   reverse meaning; headlines/thumbnails must not exaggerate what the
   footage proves.
7. **Subtitles** — Urdu subtitles checked **word by word** against the
   original audio. Never invent dialogue or silently "correct" a
   speaker into a different meaning. Translations are labelled;
   editorial explanation is kept clearly separate from the speaker's
   words.
8. **Review report & human approval** — before export, complete the
   review report (`footage_review_template.md`) and assign a status:
   - `VERIFIED — READY FOR HUMAN APPROVAL`
   - `NEEDS REVIEW — DO NOT PUBLISH`
   - `HIGH RISK — BLOCK PUBLICATION UNTIL RESOLVED`

**Nothing is ever published automatically.** If any critical check
fails, the video stays unpublished and the report says exactly what
needs checking. The goal is accurate, responsible political journalism
— not merely fast video generation.

## Tooling

- `footage_clip.py` — builds a package from a rights-cleared local
  footage file: exact start/end trim, original audio untouched,
  AZAD STUDIO brand bar, Urdu headline, speaker + date, burned Urdu
  subtitles (from a verified SRT), optional scrolling ticker, source
  credit line where required.
- `footage_review_template.md` — the mandatory pre-export report.

## Addendum (30 Oct 2026) — current footage, longer videos, ticker

Operator rules added after the first sample (which used 2016 archive
footage and was rejected as too old):

1. **Current footage only.** Pick the day's / week's actual political
   speeches. Preferred sources, in order: Parliament (Sansad TV —
   Lok Sabha / Rajya Sabha proceedings), PMO India, Press Information
   Bureau (PIB), government ministry/department channels, official
   press conferences and government webcasts (NIC `webcast.gov.in` /
   `pmindiawebcast.nic.in`). Prioritise India / Telangana politics.
2. **Longer videos are welcome** now that YouTube posting is open:
   the duration rule applies to footage too — **<= 90 s → portrait
   9:16 (1080x1920); > 90 s → landscape 16:9 (1920x1080)**.
   `footage_clip.py` selects this automatically (`--format auto`).
3. **Scrolling ticker is standard furniture** for the news-channel
   look (`--ticker`, items separated by `|`). If a word-for-word Urdu
   translation of the speech is not possible, the ticker runs in
   **English / Roman Urdu** summarising what the speaker is saying,
   and the package must say so in its review report. Where a checked
   Urdu translation IS possible, burn Urdu subtitles as before — the
   ticker complements them, never replaces accuracy.
4. Archive footage (like the 2016 sample) is for format tests only,
   must be labelled with its true date, and is never presented as
   current news.
