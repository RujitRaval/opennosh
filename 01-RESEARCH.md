# 01 — Research

Compiled August 2026. Verify anything load-bearing before acting; this category moves.

---

## 1. The market pain is real and priced

- MyFitnessPal gates its most-used features behind a subscription. Macro targets by meal, custom nutrition goals, and food-photo logging are all paid tiers.
- Category economics as summarised by a competing project: one good nutrition app runs **$40–$100/year**, and covering nutrition + training + activity + body composition takes **three or four subscriptions per person** — with still no single report across them.
- Trust damage is a live factor. MyFitnessPal's 2018 breach is still cited by users as the reason they left and never came back.

## 2. The self-hosted category is crowded but shallow

The self-hosting community tracked **1,000+ brand-new open-source projects in 2025 alone** (~19/week). Generic entrants die. But the specific incumbents each have a clear structural weakness:

| Project | License | Covers | Gap |
|---|---|---|---|
| **SparkyFitness** | **Source-available, non-commercial** — requires permission for commercial use | Nutrition, exercise, hydration, sleep, fasting, mood, body metrics, AI coach | **Not open source.** Blocks forks, hosted offerings, and anyone who cares about FOSS licensing |
| **wger** | AGPL-3.0 | Strength training, nutrition, body weight, REST API, gym management | Dated UX; weak food search; no wearable or equipment ingest |
| **OpenNutriTracker** | Open source, mobile | Nutrition logging, custom foods, custom goals | Nutrition only; metric-only units; no daily numeric micronutrient view |
| **FitTrackee / Endurain** | Open source, self-hosted | GPS activity tracking | No nutrition, no strength training |
| **Waistline / FoodYou** | Open source, mobile | Nutrition logging | Mobile-only, narrow |
| **OpenScale** | Open source | Body composition history | Single-purpose |

Two structural findings:

1. **The best-featured option is not actually open source.** SparkyFitness's non-commercial licence is the single clearest opening in this category. Anyone who wants to fork, host, or build commercially on top is locked out, and FOSS-minded users notice.
2. **Fragmentation is documented, not assumed.** An independent 2026 roundup put it directly: no self-hosted fitness app handles both runs and gym workouts well in one package — the standing advice is to pair an activity tracker with wger for strength.

## 3. Demand signal in the users' own words

From AlternativeTo's MyFitnessPal open-source alternatives page:

> A user comment expresses hope for a free, open-source alternative with all the same features as MyFitnessPal, noting MyFitnessPal is very complete but paid and proprietary.

That's the thesis stated by the target user, unprompted.

## 4. Tailwind: the 2026 data-liberation wave

Adjacent momentum that this project can ride:

- Strava shut down its free API tier, pushing users toward self-hosted trackers.
- WHOOP requires a mandatory subscription starting at **$199/year**; an open-source project (Goose) is attempting subscription-free device access, currently pre-alpha.
- Oura users are independently building subscription-free access to their own health data.

The narrative — *"stop renting access to your own body's data"* — is already circulating. This project is a beneficiary, not the originator, which is a good position.

## 5. Where the real moat is

Not the app. The app is a CRUD tracker with charts; a competent agent pipeline can build it.

The moat is **food data coverage outside Western packaged goods**, and it is genuinely bad everywhere:

- **USDA FoodData Central**: ~300,000+ foods, laboratory-validated, updated quarterly, **public domain (CC0)**. Excellent for US generic foods. Explicitly limited international coverage.
- **Open Food Facts**: 2.5M+ crowd-sourced products across 150+ countries. Coverage is barcode-driven, so it skews to packaged retail goods. **Licensed ODbL** (see `04-DATA-LICENSING.md` — this has architectural consequences).
- Commercial APIs (Nutritionix, Edamam, Spoonacular, FatSecret) fill gaps but cost money and cannot be redistributed.

**Nobody has good data for home-cooked regional food.** Dal, sabzi, rotli, thepla, khichdi, upma, dosa batter, regional Chinese home cooking, West African staples, Levantine home cooking. These are cooked in hundreds of millions of kitchens and are near-absent as reliable entries. MyFitnessPal's coverage here is a swamp of user-submitted garbage with no provenance.

**This is the wedge, the moat, and the contribution surface simultaneously.** It is also the part you personally need, which is the correct condition for a community project.

## 6. Honest risks

| Risk | Severity | Mitigation |
|---|---|---|
| ODbL contamination of the core database | **High** | Strict layer separation — `04-DATA-LICENSING.md` |
| Nutrition data accuracy disputes become political | Medium | Provenance is mandatory per entry; no anonymous unsourced values |
| Category saturation — "another tracker" | Medium | Lead with the food-pack angle, not the tracker angle |
| Health/eating-disorder adjacency | **High** | See `02-PRD.md` §7. Non-negotiable design constraints |
| Solo maintainer burnout | High | Food packs must be reviewable in <10 min or the model fails |
| Monetization is genuinely weak | Medium | Accept this. It's a reputation and distribution asset, not a business |

## 7. What would make me kill this

If, after building the food pack format, you cannot get **ten packs from five strangers within sixty days of launch**, the community thesis is wrong and this is just a solo tracker in a saturated category. That's the kill signal. Set the date in advance.
