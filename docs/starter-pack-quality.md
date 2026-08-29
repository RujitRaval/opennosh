# Starter food-pack quality evidence

This page records how the four launch starter packs were built and what “real quality” means in
reviewable terms. The checked-in YAML is the distributable artifact; opennosh does not download
USDA data when it starts.

## What shipped

| Pack | Entries | USDA records | Recipe calculations |
|---|---:|---:|---:|
| `gujarati-home-cooking` | 50 | 30 | 20 |
| `indian-staples-north` | 60 | 60 | 0 |
| `common-vegetarian-proteins` | 30 | 29 | 1 |
| `supplements-and-powders` | 25 | 25 | 0 |
| **Total** | **165** | **144** | **21** |

Every entry retains visible `contributed_by: RujitRaval` credit. The packs contain generic foods,
not branded products or restaurant menu items.

## Primary sources

The generator reads two official USDA FoodData Central bulk JSON releases:

- [FNDDS 2021–2023, October 2024][fndds] — prepared foods, regional dishes, and household portions
- [SR Legacy, April 2018][sr] — generic ingredients, proteins, and powders

USDA FoodData Central data is public domain under CC0. The schema records that boundary as
`source_license: CC0-1.0`. The exact archives used for the first release were:

| Archive | SHA-256 |
|---|---|
| `FoodData_Central_survey_food_json_2024-10-31.zip` | `dfb06ae7ddc397ccd570b91c14b75438ab2ba39f64f22d321f61d4a52a77f3eb` |
| `FoodData_Central_sr_legacy_food_json_2018-04.zip` | `0fe8ae486a2c8eb42cb96413f058deb51863a46c8fb8eeb4b1fb45006dd338ef` |

Each government entry links to its exact FDC record and names the dataset and release in
`source_note`. Nutrient values are not silently “fixed” to make validation pass: records whose
published calories disagreed with their core macros beyond opennosh's 15% integrity threshold were
excluded.

## Calculated regional dishes

FoodData Central does not represent many Gujarati home dishes. Twenty Gujarati entries and one
plain-seitan entry therefore use `published_recipe_calculation`, with contributor-original
calculation material dedicated under CC0 1.0.

Every calculated entry discloses:

- each component's gram weight and USDA FDC ID;
- the finished cooked yield;
- that water and immaterial-energy seasonings are omitted;
- the per-100-gram result and rounding method; and
- the warning that real home recipes vary.

For example, the checked-in moong dal khichdi model uses 80 g white rice (FDC 169756), 60 g mung
beans (FDC 174256), 5 g canola oil (FDC 172336), and 1 g turmeric (FDC 172231), with a 425 g cooked
yield. It calculates to 128.8 kcal, 4.74 g protein, 1.47 g fat, and 24.05 g carbohydrate per 100 g.

## Reproducible spot checks

| Entry | Provenance | kcal | Protein (g) | Fat (g) | Carbohydrate (g) |
|---|---|---:|---:|---:|---:|
| Bread, chappatti or roti | FNDDS, FDC 2707713 | 299 | 7.85 | 9.20 | 46.10 |
| Cheese, paneer | FNDDS, FDC 2705740 | 299 | 15.90 | 15.50 | 22.50 |
| Whey-based protein powder | SR Legacy, FDC 173180 | 352 | 78.10 | 1.56 | 6.25 |
| Moong dal khichdi | disclosed batch calculation | 128.8 | 4.74 | 1.47 | 24.05 |
| Plain seitan | disclosed cooked-yield calculation | 132.1 | 26.86 | 0.66 | 4.93 |

These are traceability checks, not claims that one database record represents every brand or
household recipe.

## Automated evidence

Run:

```bash
make foodpack-validate
uv run pytest api/tests/foodpacks/test_starter_pack_generator.py \
  api/tests/foodpacks/test_starter_packs.py -q
```

The committed release produces zero validator errors and zero warnings. The focused generator,
committed-pack, and signed-release tests additionally prove:

- the original four-pack, 165-entry launch foundation remains intact;
- every governed extension pack is validated with zero errors or warnings and is included in the
  signed release with totals derived from the current catalog;
- 144 government records and 21 disclosed recipe calculations;
- unique slugs and no nutrient profiles within the validator's 1% near-duplicate threshold;
- a named positive-weight portion for every entry;
- explicit labeling when a logging portion is an opennosh estimate rather than a USDA measure;
- exact source links, release labels, license boundaries, and visible credit; and
- no known brand markers in entry names.

The generator tests cover 90% of the audited refresh paths, including missing source IDs, nutrient
extraction, portion ranking, recipe arithmetic, integrity failures, rendering, and stale output.

To regenerate from locally downloaded official archives:

```bash
uv run python scripts/build_starter_food_packs.py \
  --fndds-json /path/to/surveyDownload.json \
  --sr-legacy-json /path/to/FoodData_Central_sr_legacy_food_json_2018-04.json
```

Add `--check` to compare regenerated output with the committed YAML without rewriting it.

## Limits and correct use

This is a practical logging seed, not clinical or diagnostic data. USDA entries are generic;
manufacturing and preparation vary. Recipe values change with ingredient choice and water loss.
Mass-defined scoops are deliberately labeled because supplement scoops are not universal. Where an
SR Legacy record has no household measure, the source note identifies opennosh's portion as a
logging estimate. Users should prefer a product label, laboratory result, or their own private
recipe when that better represents what they ate.

[fndds]: https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_survey_food_json_2024-10-31.zip
[sr]: https://fdc.nal.usda.gov/fdc-datasets/FoodData_Central_sr_legacy_food_json_2018-04.zip
