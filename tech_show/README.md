# AZAD STUDIO Daily Tech Show — research foundation

Status: **NOT LIVE; no social publishing is implemented in this component.**

This branch contains two tested building blocks: official-feed discovery (research leads only) and a fail-closed prerequisite checker. Research output is not an approved product or factual review. No scheduler in this branch is enabled, and no photos, clips, narration, claims or posts are produced by it.

## Intended daily format

- Select one current gadget, prioritising India.
- 10:00 IST Hindi 6–10 minute video; 14:00 IST English 6–10 minute video; 18:00 IST poster. These slots were selected by the user.
- Facebook and Instagram: two video posts and one poster. YouTube: two full videos and a poster-based Short. Descriptions are in English and Roman Urdu, with relevant source and rights links.
- Use genuine, rights-cleared product photos and video clips. Do not use generic images of another product as if they depict the reviewed product. Pixabay access alone does not clear trademarks, third-party rights or prove identity.
- Facts, price, dates and comparisons require official corroboration. Clearly state when unavailable; do not promote rumors to facts.
- Independently check narration pronunciation, factual claims and creative quality. Use relevant hashtags but never promise virality.
- Do not duplicate public posts after ambiguous failures. Use remote atomic claims and record platform post IDs.
- First episode used a user-authorized stills-only exception; that exception does not replace the requirement for video in later episodes.

## Discovery

`python research.py --as-of 2026-10-29T12:00:00+05:30 --out candidates.json`

The `--as-of` value above is an example, not today's date. Omit it to use the runtime's India time after confirming the runtime clock. Apple India, Samsung India and Google Pixel RSS are queried. Failures are recorded; results are **unverified leads**, not instructions to publish. Verify date, model, specifications, prices, photo/video rights and authenticity separately.

## Required before production activation

1. Build and validate factual extraction and independent editorial checks against primary sources.
2. Find real model-matched stills and video with verifiable reuse permission and in-caption credits.
3. Render, inspect and validate both videos, poster and poster Short; confirm branding and correct pronunciation.
4. Implement and end-to-end test Facebook, Instagram (including suitable video transfer), and YouTube publishing with per-platform duplicate claims and failure handling.
5. Confirm runtime clock and timezone; add daily scheduling and a kill switch.
6. Perform full tests without unintended public posts, then verify a real scheduled publication. Until then, **do not enable posting**.

Run local tests: `python -m unittest discover -s tests -v`.
