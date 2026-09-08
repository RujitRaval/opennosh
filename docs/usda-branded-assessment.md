# USDA Branded Foods activation assessment

opennosh assessed the USDA FoodData Central April 2026 Branded Foods JSON release without importing it. The release remains on hold. Its identity conflicts and projected catalog size exceed the current fail-closed activation gates.

## Pinned input

- Release: USDA Branded Foods, April 30, 2026
- Archive: `FoodData_Central_branded_food_json_2026-04-30.zip`
- Compressed bytes: `204270542`
- Expanded JSON bytes: `3315545449`
- SHA-256: `57b0f122e61cf2840f03c11e9520275d0d2018dc036e16273fcd3cd370db2256`
- Rows examined: `455458`

The assessment verifies the exact filename, byte count, digest, single JSON member, expanded byte count, compression ratio, top-level data type, row limit, and positive unique FDC identifiers before it reports activation eligibility.

## Identity and duplicate controls

Branded products are keyed by normalized market country plus a checksum-valid GTIN padded to GTIN-14. A name or brand match alone never causes automatic merging.

| Result | Count |
| --- | ---: |
| Valid identity rows | 443,350 |
| Invalid GTIN rows | 12,108 |
| Duplicate identity groups | 19,780 |
| Rows in duplicate groups | 40,074 |
| Conflicting product-content groups | 19,780 |
| Exact product-content duplicate groups | 0 |
| Eligible after quarantine | 403,276 |
| Quarantined | 52,182 |

Every invalid identity is quarantined. Every member of a duplicate identity group is quarantined; the tool does not guess which product is canonical. Conflicts compare normalized descriptions, brands, ingredients, serving information, nutrient rows, and portions. There were zero duplicate FDC IDs.

## Activation decision

Status: `hold`.

Two independent gates block direct activation:

1. All 19,780 duplicate GTIN-and-country groups contain conflicting product content and require a reviewed canonical-version policy.
2. The 403,276 otherwise eligible products exceed the current 250,000-record importer and search-projection evidence ceiling.

The machine-readable result is in `docs/data/usda-branded-2026-04-30-assessment.json`. Reproduce it with:

```bash
PYTHONPATH=api:. uv run --frozen python -m opennosh_api.importers.usda_branded_assessment \
  /path/to/FoodData_Central_branded_food_json_2026-04-30.zip \
  --manifest config/usda-branded-assessment.v1.json
```

Do not add Branded Foods to `config/usda-reference-release.v1.json` until both blockers are resolved and the exact assessment still passes.
