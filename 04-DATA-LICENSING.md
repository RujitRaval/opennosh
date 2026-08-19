# 04 — Food data licensing

**Read before writing any code.** This constraint determines the database schema. Discovering it after launch means a migration and a licence change, which is the kind of thing that kills community projects.

I am not a lawyer and this is not legal advice. The facts below are verifiable; the conclusions are engineering judgment. Get a lawyer's read before you publish if you intend to offer anything hosted or paid.

---

## The three sources and their terms

| Source | Licence | Redistributable? | Share-alike? |
|---|---|---|---|
| **USDA FoodData Central** | Public domain / CC0 | Yes, freely | No |
| **Open Food Facts — data** | Open Database License (ODbL) | Yes, with conditions | **Yes** |
| **Open Food Facts — individual contents** | Database Contents License (DbCL) | Yes | — |
| **Open Food Facts — product images** | CC BY-SA | Yes, with attribution | **Yes** |
| **Commercial APIs** (Nutritionix, Edamam, FatSecret, Spoonacular) | Proprietary ToS | **No** | — |

## Why ODbL is the problem

ODbL is a **share-alike licence for databases**. In plain terms: if you take Open Food Facts data, combine it into a derived database, and publish that database, you may be obliged to publish the derived database under ODbL too.

That is fine for the food data. It is **not** fine if OFF-derived rows sit in the same table as:

- Your own original food packs (which you want under a permissive licence so anyone can reuse them)
- User-generated logging data (which is private and must never be publishable under any circumstance)

A single `foods` table containing USDA rows, OFF rows, and community rows is a licensing mess with no clean answer.

## The architecture this forces

**Three separate stores, never merged at rest.**

```
┌─────────────────────────────────────────────────────────┐
│  foods_reference    (CC0)                               │
│  USDA FoodData Central import. Public domain.           │
│  Freely redistributable. Ships with the app.            │
├─────────────────────────────────────────────────────────┤
│  foods_community    (CC0 or CC BY, contributor-signed)  │
│  Community food packs. THIS IS THE PROJECT'S ASSET.     │
│  Must stay clean of ODbL input. Ships with the app.     │
├─────────────────────────────────────────────────────────┤
│  foods_odbl         (ODbL, attributed, OPTIONAL)        │
│  Open Food Facts. Barcode lookups.                      │
│  Separate table. Separate export path. Opt-in at deploy.│
│  Does NOT ship in the default image.                    │
└─────────────────────────────────────────────────────────┘
```

Rules the implementing agent must enforce:

1. **No cross-table writes.** A row never moves from `foods_odbl` into `foods_community`. If a user edits an OFF-sourced food, the edit creates a new `foods_community` row with `derived_from: null` and values re-entered from the label — not copied.
2. **Every row carries `source` and `license` columns.** Non-nullable. No exceptions.
3. **Export respects licence.** `foods_community` exports as a clean CC0 dump. `foods_odbl` exports separately, with ODbL notice attached, or not at all.
4. **User log data is never exported in any bulk path.** Different concern, same table discipline.
5. **OFF integration is a plugin, not a dependency.** Default deployment works without it. This also protects you if OFF's terms or availability change.

## The licence decisions to make

**Application code: MIT.**
Rationale: the entire strategic opening here is that the leading competitor is non-commercially licensed. Permissive is the differentiator. AGPL would protect against a cloud provider running a hosted version — but nobody is going to build a business hosting a self-hosted calorie tracker, so AGPL buys protection against a threat that doesn't exist while costing you the positioning that matters. MIT.

**Community food packs: CC0.**
Rationale: you want these reused everywhere, including by competitors. Every app that imports your food packs is a project that has a reason to send corrections upstream. CC0 maximises adoption; adoption is the moat. Requires a lightweight contributor sign-off in the PR template so contributors knowingly waive rights.

**Do not** accept food packs under ODbL, CC BY-SA, or anything share-alike. State this in `CONTRIBUTING.md` and enforce it in CI. A single share-alike pack merged into `foods_community` contaminates the whole dataset and is genuinely painful to unwind.

## The rule contributors will break

People will scrape data from MyFitnessPal, Cronometer, Nutritionix, or a national food composition database with restrictive terms, and submit it as a food pack. It will look fine.

Mitigations, in order of usefulness:

1. **Mandatory `source` field per entry** with an enum: `lab_analysis`, `government_database`, `manufacturer_label`, `published_recipe_calculation`, `own_measurement`. No free-text "internet."
2. **CI check** rejecting packs with missing or invalid provenance.
3. **PR template checkbox**: "I confirm this data was not copied from a proprietary database or app."
4. **Statistical smell test** in CI — entries matching a known commercial dataset's rounding signature get flagged for manual review.

You will not catch everything. Document the policy, act on reports, and move on. Perfect provenance is not achievable and pretending otherwise will stall the project.

## USDA import specifics

- Bulk downloads available as CSV and JSON; full download includes all data types.
- API: free with a data.gov key, rate limited to ~1,000 requests/hour. **Use bulk download for the seed import, not the API.**
- Data types: Foundation Foods (lab-analysed, highest quality), SR Legacy, FNDDS (survey), Branded Foods, Experimental.
- **Recommendation:** seed with Foundation Foods + SR Legacy only. Branded Foods is large, US-skewed, and duplicates what OFF does better via barcode.
- Update cadence is quarterly. Build the importer as a re-runnable job, not a one-time script.
