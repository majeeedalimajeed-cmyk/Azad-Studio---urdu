# AZAD STUDIO two-photo card demo

**Status: local layout preview — synthetic placeholder images, not Pixabay photos.**

The updated two-photo renderer was validated locally with two clearly labelled synthetic placeholder images in the approved `NEWS IMAGE` boxes. The `LATEST NEWS` banner and both news sections are somewhat smaller, creating clean space below the bottom monogram for the operator byline **By - Abu Aimal and Aimal Akram**.

- Card: `data/cards/two-photo-layout-demo-v2-1.png`
- Dimensions: 1080×1350
- Visual inspection: `passed`
- `media_count`: `2`
- Photo checks: `photo_1_pixels=true`, `photo_2_pixels=true`
- Byline check: `byline_pixels=true`
- Exact supplied monogram: `true`

The local placeholders say `LAYOUT DEMO — NOT A NEWS PHOTO`; they are not intended for publication. Production cards use source-supplied rights-cleared photos first, then relevant Pixabay Content License photos downloaded locally through the documented API. If no relevant licensed photo is found, the renderer uses a clean text-led card.

A live demo with real Pixabay safe-search results and credits is produced by `verify-pixabay.yml` after the new commit is pushed. The Pixabay API key remains a write-only GitHub Actions secret and is never printed or committed.
