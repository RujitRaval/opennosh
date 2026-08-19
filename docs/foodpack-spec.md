# 05 — Food pack specification

This is the canonical contributor specification at `docs/foodpack-spec.md`. Eligible original food-pack material is dedicated under CC0 1.0 as described in [`packs/LICENSE.md`](../packs/LICENSE.md). opennosh still preserves and visibly displays contributor credit.

This is the load-bearing document. If contributing a food pack takes longer than fifteen minutes or a review takes longer than ten, the community model fails and this becomes a solo-maintained tracker.

---

## 1. What a food pack is

A directory containing a manifest and one or more food entry files, covering a coherent set of foods — usually a cuisine, a region, or a category.

```
packs/
  gujarati-home-cooking/
    pack.yaml
    foods/
      breads.yaml
      dals.yaml
      sabzis.yaml
    README.md
```

One pull request adds or updates one pack. That's the unit.

## 2. `pack.yaml`

```yaml
id: gujarati-home-cooking
name: Gujarati home cooking
description: Everyday home-prepared Gujarati dishes as commonly made in domestic kitchens.
version: 1.2.0
locale: en-IN
license: CC0-1.0
maintainers:
  - github: someuser
entry_count: 47
```

`version` is semver. Bump minor for added entries, patch for corrections, major for breaking slug changes.

## 3. Food entry

```yaml
- slug: gujarati-thepla-methi
  name: Methi thepla
  name_local: મેથી થેપલા
  category: bread
  tags: [vegetarian, indian, gujarati]
  contributed_by: someuser

  provenance: published_recipe_calculation
  source_uri: null
  source_license: contributor-original
  source_note: >
    Calculated from a standard household recipe (whole wheat flour 100g,
    fresh fenugreek 30g, oil 15g, yogurt 20g) yielding 6 pieces at 42g each.
    Component values from USDA FDC SR Legacy.

  basis: per_100g
  nutrients:
    energy_kcal: 312
    protein_g: 8.4
    fat_g: 12.1
    saturated_fat_g: 1.6
    carbohydrate_g: 42.0
    fiber_g: 6.2
    sugar_g: 1.1
    sodium_mg: 310
    iron_mg: 3.1

  portions:
    - name: 1 piece
      grams: 42
    - name: 2 pieces
      grams: 84
```

### Required fields
`slug`, `name`, `category`, `contributed_by`, `provenance`, `source_uri`, `source_license`, `basis`, `nutrients.energy_kcal`, `nutrients.protein_g`, `nutrients.fat_g`, `nutrients.carbohydrate_g`

`contributed_by` is the contributor's GitHub username without `@`. opennosh preserves it in exports and displays it as visible entry-level credit.

`source_license` is one of `contributor-original`, `CC0-1.0`, or `public-domain`. Use `contributor-original` only for material the contributor created and may dedicate under CC0. `government_database` entries require an `https` `source_uri` and a source license of `CC0-1.0` or `public-domain`. CI rejects restrictive, unknown, or free-text license values; those sources belong outside community packs under their own isolated license boundary.

### `provenance` enum — no free text
| Value | Meaning |
|---|---|
| `lab_analysis` | Direct laboratory measurement |
| `government_database` | National food composition database. Cite it in `source_note` |
| `manufacturer_label` | Transcribed from packaging |
| `published_recipe_calculation` | Computed from component foods. **Show the components in `source_note`** |
| `own_measurement` | Contributor weighed and calculated. Method required in `source_note` |

There is deliberately no `internet` or `unknown` option. An entry without defensible provenance does not get merged.

### `basis`
`per_100g` or `per_100ml`. Nothing else. All portion conversion derives from this.

## 4. Portions matter more than you think

The most common reason someone abandons a tracker is that logging requires a kitchen scale for everything. Household portions are what make an entry usable.

- Provide at least one named portion per entry.
- Portions are how the food is actually served — "1 roti," "1 katori," "1 ladle," not "1 serving."
- Local naming is encouraged. `name_local` renders alongside the transliteration.

A pack with perfect nutrient data and no portions is worse in practice than one with approximate data and good portions. Say this in the contributor docs.

## 5. Validation — what CI enforces

The validator is a standalone module. Same code runs in CI and in the runtime loader, so they cannot drift.

**Hard failures (block merge):**
- Schema violation, missing required field
- Invalid `provenance` value
- Missing or invalid structured source URI/license, including a non-CC0-compatible government source
- `slug` collision within or across packs
- Macro/energy mismatch beyond tolerance: `|(4·protein + 4·carb + 9·fat) − energy_kcal| > 15%`
- Any nutrient negative, or `energy_kcal > 900` per 100g (nothing exceeds pure fat)
- `license` anything other than `CC0-1.0`
- Portion with `grams <= 0`

**Warnings (flag for review, don't block):**
- Entry has no named portions
- `source_note` under 20 characters
- Nutrient profile statistically near-identical to an existing entry (possible duplicate or copy)
- Fiber exceeds total carbohydrate

The macro/energy cross-check is the single highest-value rule. It catches most transcription errors automatically and means your review is about judgment, not arithmetic.

## 6. The review checklist — keep it under ten minutes

Reviewer checks only:

1. CI green
2. Provenance is plausible for the claimed values
3. Slugs are sensible and stable
4. Portions reflect real serving practice
5. Contributor checked the licence box

Everything else is CI's job. Resist adding review steps — reviewer time is the constraint that determines whether this project scales past you.

## 7. The starter packs you write yourself

Ship these with v1. They demonstrate the format, seed the gap the project is named for, and prove you use your own tool.

| Pack | Entries | Why |
|---|---|---|
| `gujarati-home-cooking` | ~50 | Your kitchen. Highest-quality provenance you can produce |
| `indian-staples-north` | ~60 | Largest underserved population |
| `common-vegetarian-proteins` | ~30 | Paneer, tofu, tempeh, legumes, seitan — badly covered everywhere |
| `supplements-and-powders` | ~25 | Protein powders and supplements are a mess in every tracker |

Four packs, roughly 165 entries. That's your credibility deposit — nobody contributes to an empty format.

## 8. Anti-patterns to reject

- **The mega-pack.** A single PR adding 2,000 entries is unreviewable. Cap at 100 entries per PR and say so in `CONTRIBUTING.md`.
- **Branded packaged goods.** That's Open Food Facts' job, it's barcode-shaped, and it's ODbL territory. Community packs are for generic and home-prepared foods.
- **Restaurant menu items.** Unverifiable, changes constantly, and legally murkier.
- **Personal recipes.** Those belong in a user's private recipe feature, not the shared dataset. The line: would a stranger in that region recognise this as a standard dish? If no, it's a recipe.
