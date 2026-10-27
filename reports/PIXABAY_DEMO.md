# AZAD STUDIO two-photo card demo

**Status: local layout preview — synthetic placeholder images, not Pixabay photos.**

The updated renderer now follows the supplied reference design: a **narrow angled `LATEST NEWS` ribbon** instead of a full-width banner, a two-line date badge, dotted world-map motif, twin-slashed gold headline bands, tall rounded `NEWS IMAGE` panels, and a diamond separator. The exact supplied monogram remains at top and bottom, and the smaller top section leaves clean space below the bottom monogram for the operator byline **By - Abu Aimal and Aimal Akram**.

- Card: `data/cards/reference-faithful-demo-1.png`
- Dimensions: 1080×1350
- Visual inspection: `passed`
- `media_count`: `2`
- `heading_box`: `(35, 166, 700, 248)` — narrow ribbon check passed
- Photo checks: `photo_1_pixels=true`, `photo_2_pixels=true`
- Byline check: `byline_pixels=true`
- Exact supplied monogram: `true`

The local placeholders say `LAYOUT DEMO — NOT A NEWS PHOTO`; they are not intended for publication. Production cards use source-supplied rights-cleared photos first, then relevant Pixabay Content License photos downloaded locally through the documented API. If no relevant licensed photo is found, the renderer uses a clean text-led card.

A live demo with real Pixabay safe-search results and credits is produced by `verify-pixabay.yml` after the new commit is pushed. The Pixabay API key remains a write-only GitHub Actions secret and is never printed or committed.
