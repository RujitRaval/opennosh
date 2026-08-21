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
- User-generated logs and recipes (which are private and must never be publishable under any circumstance)

A single `foods` table containing USDA rows, OFF rows, and community rows is a licensing mess with no clean answer.

## The architecture this forces

**Four separate stores, never merged at rest.**

```
┌─────────────────────────────────────────────────────────┐
│  foods_reference    (CC0)                               │
│  USDA FoodData Central import. Public domain.           │
│  Freely redistributable. Ships with the app.            │
├─────────────────────────────────────────────────────────┤
│  foods_community    (CC0 1.0, contributor-dedicated)    │
│  Community food packs. THIS IS THE PROJECT'S ASSET.     │
│  Must stay clean of ODbL input. Ships with the app.     │
├─────────────────────────────────────────────────────────┤
│  foods_odbl         (ODbL, attributed, OPTIONAL)        │
│  Open Food Facts. Barcode lookups.                      │
│  Separate table. Separate export path. Opt-in at deploy.│
│  Integration code ships in v1; no OFF rows are bundled.│
├─────────────────────────────────────────────────────────┤
│  foods_custom     (PRIVATE, owner-scoped)               │
│  User-created foods. Never returned by public search or │
│  included in a community, ODbL, or exercise export.     │
└─────────────────────────────────────────────────────────┘
```

Rules the implementing agent must enforce:

1. **No cross-table writes.** A row never moves from `foods_odbl` into `foods_community`. If a user edits an OFF-sourced food, the edit creates a new `foods_community` row with `derived_from: null` and values re-entered from the label — not copied.
2. **Every redistributable source record carries non-nullable provenance and license metadata appropriate to its store.** Community rows preserve `provenance`, `source_uri`, `source_license`, and `pack_license`; Open Food Facts rows preserve `source`, `source_url`, `database_license`, and `contents_license`; exercise rows preserve their full per-entry attribution record. Private user-created foods and logs are not third-party source records and instead carry authenticated owner IDs.
3. **Export respects licence.** `foods_community` exports as a clean CC0 dump at `/api/v1/export/foods/community`. `foods_odbl` exports separately at `/api/v1/export/foods/odbl`, with ODbL and DbCL notices attached. Allowlisted wger exercises export separately at `/api/v1/export/exercises`, with source, author, CC BY-SA, derivative, and translation attribution intact.
4. **User log and recipe data is never included in public or dataset bulk exports.** A user can still download their own private data through `/export/me`; that authenticated personal export is separate from every food or exercise dataset export.
5. **OFF integration is optional, not a core dependency.** Its code ships in v1, but it is disabled until configured. The default deployment remains usable without network access or bundled OFF data. This also protects users if OFF's terms or availability change.

## Selected licences

**Application license: MIT.**
The project owner selected MIT on 2026-08-19. The root `LICENSE` file is the grant for application code.

**Community food-pack dedication: CC0 1.0 Universal.**
The project owner selected CC0 on 2026-08-19. `packs/LICENSE.md` records the dedication. Attribution is not a legal condition of CC0, but opennosh preserves `contributed_by`, displays contributor credit in the product, and lists contributors in `AUTHORS.md` as a community promise.

**Do not** accept food packs under ODbL, CC BY-SA, or anything share-alike. State this in `CONTRIBUTING.md` and enforce it in CI. A single share-alike pack merged into `foods_community` contaminates the whole dataset and is genuinely painful to unwind.

## The rule contributors will break

People will scrape data from MyFitnessPal, Cronometer, Nutritionix, or a national food composition database with restrictive terms, and submit it as a food pack. It will look fine.

Mitigations, in order of usefulness:

1. **Mandatory structured provenance per entry:** `provenance` uses the enum `lab_analysis`, `government_database`, `manufacturer_label`, `published_recipe_calculation`, or `own_measurement`; `source_uri` and `source_license` follow the allowlist in `docs/foodpack-spec.md`. No free-text "internet" source and no unknown license.
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

## Exercise catalogue boundary

wger's application code is AGPL-3.0-or-later. Its initial exercise and ingredient data is CC BY-SA 3.0, and current records can carry Creative Commons license and attribution metadata per entry. opennosh may import exercise data, but must not copy wger application code, strip ShareAlike obligations, or assume one blanket license covers every exercise.

The wger importer must:

1. accept only entries on the versioned v1 allowlist (`CC-BY-SA-3.0` only); reject missing, ambiguous, NC, ND, and all other licenses pending legal review;
2. preserve source identifier, object URL, derivative source URL, exact SPDX identifier and license URL, author and author URL, attribution text, and translation-level attribution;
3. keep imported entries in a separately identified exercise catalogue and preserve the applicable license on export;
4. reject entries whose license is missing, unsupported, or incompatible with the intended use; and
5. retain fixtures locally so tests never call the wger service.

Source references: [wger documentation license summary](https://wger.readthedocs.io/en/stable/#licence), [wger repository license summary](https://github.com/wger-project/wger#license), and [wger application license](https://github.com/wger-project/wger/blob/master/LICENSE.txt).

## Open Food Facts API boundary

The v1 integration stores ODbL 1.0 database rights separately from DbCL 1.0 individual-content rights. It is disabled by default, and an enabled cache miss writes only a reduced, validated nutrition record to `foods_odbl`; no runtime path writes that record to `foods_community`. Product images are out of scope and are omitted from the upstream field allowlist, so they are neither requested nor cached. A future image feature would need its own CC BY-SA attribution path. Every API request uses a descriptive identifying `User-Agent` containing the opennosh version and maintainer contact. `/api/v1/export/foods/odbl` is the canonical bulk export for this cache, with `/api/v1/export/foods/openfoodfacts` retained as a compatibility alias. Both carry the applicable Open Food Facts notices; the cache is never included in a CC0 food-pack export.

Source reference: [Open Food Facts API licensing guidance](https://openfoodfacts.github.io/documentation/docs/Product-Opener/api/tutorials/license-be-on-the-legal-side/).
