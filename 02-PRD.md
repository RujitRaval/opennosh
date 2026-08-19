# 02 — PRD: OpenPlate

Status: draft. Resolve `08-OPEN-QUESTIONS.md` before running the issue pipeline.

---

## 1. Problem

People who track nutrition seriously face three compounding problems:

1. **Subscription capture.** The features that make tracking useful — custom macro targets, per-meal goals, micronutrient views — are paywalled at $40–$100/year per app.
2. **Fragmentation.** Nutrition, strength training, and body metrics live in separate apps with separate subscriptions and no unified view.
3. **Food data that doesn't cover their food.** Coverage for home-cooked regional cuisine is poor to nonexistent across every major tracker. Users of Indian, West African, Levantine, Southeast Asian, and regional Chinese home cooking are logging approximations of approximations.

The open-source alternatives each solve one piece, and the most complete one is licensed non-commercially, which blocks reuse.

## 2. What we're building

A self-hosted nutrition and training tracker where **the food database is a git repository**. The application and dataset licenses remain open decisions; MIT and CC0 are the current recommendations, not grants.

The application is deliberately unremarkable — log food, log lifts, log weight, see trends. The differentiator is that when a food is missing, a user can add it permanently for everyone, in a text file, via a pull request, in under fifteen minutes.

## 3. Who it's for

**Primary:** technically-capable people who already self-host something, track their intake deliberately (body recomposition, athletic goals, medical necessity), and eat food that mainstream trackers handle badly.

**Secondary:** developers of *other* nutrition apps who want a freely reusable food dataset. They are a distribution channel, not a burden — every downstream app is a source of upstream corrections.

**Explicitly not for:** casual weight-loss users who want a polished mobile app with social features. That user is well served and we will lose to MyFitnessPal on every axis they care about.

## 4. MVP scope

### 4.1 Food data
- Seed import of USDA FoodData Central (Foundation + SR Legacy) from bulk download
- Food pack loader — reads community packs from a directory, validates against schema, imports
- Food search across reference + community tables, with generic foods ranked above branded duplicates
- Manual custom food entry (private to the user, not published)
- Recipe composition — a recipe is a food built from other foods, with a yield weight

### 4.2 Logging
- Daily food log with meal grouping (configurable meal names, not hardcoded breakfast/lunch/dinner)
- Portion entry by weight, by named household serving, or by recipe portion
- Body weight and measurement logging
- Strength training log: exercise, sets, reps, load. Free-form enough to accommodate cable/digital-resistance equipment where "weight" is a machine-reported number

### 4.3 Targets and views
- User-set macro and calorie targets, including per-day-type targets (a user may run different targets on training vs rest days)
- Daily view: consumed vs target, macros, and a numeric micronutrient table
- Trend view: weight, intake, and volume over time

### 4.4 Platform
- Single `docker compose up` deployment
- REST API covering everything the UI does
- Multi-user with individual data isolation
- Data export: full user data as JSON, on demand, no gatekeeping

## 5. Explicit non-goals for v1

Stated so an agent doesn't invent them:

- No mobile native apps. Responsive web only. (Mobile is the obvious v2 and the obvious contributor magnet after launch.)
- No GPS/activity tracking. Endurain and FitTrackee do this well; integrate later, don't rebuild.
- No wearable ingestion. Deliberate — it's a separate project and possibly a separate repo.
- No social feed, friends, challenges, or leaderboards.
- No AI food-photo estimation. Adds a model dependency and an accuracy liability on day one.
- No barcode scanning in the default build. It requires the ODbL layer; ship it as an opt-in plugin.
- No meal planning or recipe recommendation.
- No coaching, no "insights," no automated advice.

## 6. Success criteria

The build succeeds if, ninety days after launch:

- Ten or more food packs merged from five or more distinct contributors *(the real test — see `01-RESEARCH.md` §7)*
- A stranger can go from `git clone` to logging their first meal in under ten minutes
- At least one other open-source project has imported the dataset under the selected open-data license

Stars are a lagging indicator of the above, not a goal.

## 7. Health safety constraints — non-negotiable

This is a calorie-tracking application. That category has a documented relationship with disordered eating, and an open-source project has the same duty of care as a commercial one.

The implementing agent must treat these as hard requirements, not suggestions:

- **No numeric target below a configurable floor without an explicit, deliberate override.** Default floor: 1,200 kcal/day. The override must be a settings-level action, not an inline nudge.
- **No streaks, no shaming, no "you went over" language.** Report numbers neutrally. A day over target renders identically in tone to a day under.
- **No goal weight validation against BMI charts**, and no unsolicited commentary on the user's target.
- **No social comparison surfaces of any kind.**
- **A dismissible resource pointer in settings** linking to the National Alliance for Eating Disorders' [current treatment-finder and helpline page](https://www.allianceforeatingdisorders.com/find-treatment/). Verify the published contact details before every release.
- **Fasting-window tracking is out of scope for v1.** It attracts a use pattern this project should not optimise for.

Put this section verbatim into `CONVENTIONS.md` in the repo so both the implementing and reviewing agents see it.

## 8. Key user flows that must work end to end

1. **Cold start:** deploy → create account → set targets → search "chicken breast" → log 150g → see macros against target
2. **Missing food:** search "thepla" → no result → create custom food with macros → log it → prompted (not forced) to consider contributing it as a pack entry
3. **Recipe:** define "Sunday dal" from six ingredients with a 1,400g yield → log 300g → correct macros attributed
4. **Contribution:** fork repo → copy pack template → add ten entries → CI validates → PR merged → entry appears in next release for every user
5. **Exit:** export all data as JSON and leave. This must be as frictionless as signing up. It's the whole promise.
