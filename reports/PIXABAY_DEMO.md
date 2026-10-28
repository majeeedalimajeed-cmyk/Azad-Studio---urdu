# AZAD STUDIO two-photo card demo

**Status: live Pixabay verification PASSED in GitHub Actions — non-published demo card.**

`verify-pixabay.yml` run `40642038560` (commit `caa8296`) completed with conclusion **success**:

- Pixabay key configured: **true** (`pixabay_available: true`; the key itself is a write-only Actions secret and was never printed)
- Safe-search query: `education exam students`
- Search results used: 2; downloaded locally: 2 (no hotlinking)
- Rendered card: `data/cards/pixabay-demo-20261028T060842-1.png` (1080×1350)
- Audit: `data/audit/pixabay-demo-20261028T060842-1.json`
- Visual inspection: **passed** — `photo_1_pixels`, `photo_2_pixels`, `byline_pixels`, `exact_logo_asset`, and `narrow_latest_news_ribbon` all true
- `heading_box`: `[35, 166, 700, 248]` (narrow angled ribbon per the supplied reference)
- Byline on card: **By - Abu Aimal and Aimal Akram**

## Photo credits (Pixabay Content License)

- Pixabay / silviarita — https://pixabay.com/service/terms/ (student studying with books)
- Pixabay / Alexas_Fotos — https://pixabay.com/service/terms/ (books and chalkboard)

Two bugs were found and fixed by this live verification:

1. Pixabay matches every query term, so specific story phrases returned 0 hits. `PixabayClient.search()` now relaxes an empty-result query to its leading 3, then 2, terms.
2. The size filter judged photos by the 640px `webformat` preview, discarding every usable image. It now judges by the full image dimensions used for the `largeImageURL` download.

Pixabay images are topical stock illustrations under the Pixabay Content License; they are not presented as photographs of the specific event. If no relevant licensed photo is found, production cards fall back to a clean text-led design. This demo card was **not** published to Facebook or Instagram.
