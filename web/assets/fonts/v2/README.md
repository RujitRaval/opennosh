# Living Commons public fonts v2

This directory is the auditable source package for the route-scoped public font build. The
production WOFF2 files are generated into `web/public/fonts/v2/`; Tracker routes never import the
public stylesheet or preload manifest.

## Licensed inputs

The immutable source files were retained from the Google Fonts Latin WOFF2 downloads recorded by
v1 on 2026-08-23. Their SIL Open Font License texts are retained in `licenses/` and reproduced
beside the deployed derivatives at `web/public/fonts/v2/licenses/`.

Source Sans reserves “Source,” and IBM Plex Mono reserves “Plex.” Because subsetting creates
modified font software, the build rewrites every derivative family, full, unique, and PostScript
name and uses `opennosh-*` output filenames. The build fails if a configured derivative identity
contains a family’s declared Reserved Font Name.

- Archivo Variable: source weights 100–900 and widths 62–125.
- Source Sans 3 Variable: source weights 200–900.
- IBM Plex Mono: static weights 400, 500, and 600.

`font-build.v2.json` records every source hash, output hash, output byte count, supported interface
script, delivery phase, and budget. `glyphs/latin.txt` is the explicit codepoint contract. A new
interface language must declare its script subset before it can ship; a food-data locale never
changes the interface font payload.

## Rebuild

From a clean clone with the locked development dependencies installed:

```bash
uv run --frozen python scripts/build_public_fonts.py --write
uv run --frozen python scripts/build_public_fonts.py
npm --prefix web run check:font-budgets
```

The first command is the intentional update path. It narrows Archivo to the used 600–900 weight
and 75–125 width ranges, narrows Source Sans 3 to 400–700, subsets all faces to the Latin contract,
and rewrites the manifest hashes. The second command rebuilds into a temporary directory and fails
unless every output byte matches the committed files. FontTools 4.60.1 and
`SOURCE_DATE_EPOCH=0` are mandatory.

## Delivery contract

- The default Latin route preloads Archivo and Source Sans 3 only.
- IBM Plex Mono is declared in public CSS but is not preloaded.
- Critical transfer must remain at or below 160 KiB across two requests.
- Total transfer after all three mono weights must remain at or below 220 KiB across five requests.
- Public faces use `font-display: swap` and metric-compatible local fallbacks.
- Font-attributable CLS must remain at or below 0.02 on desktop and mobile slow-arrival tests.
- Direct `/tracker` and `/tracker/trends` documents must reference and transfer zero v2 font bytes.

The route-local stylesheet and stable public URLs are intentional. Next.js 16 automatic
`next/font` preloads are not used because its production manifest can hoist them across independent
root layouts, causing Tracker to receive Living Commons font resources.
