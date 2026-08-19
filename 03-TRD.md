# 03 — TRD: opennosh

Companion to `02-PRD.md`. Together these two are the input to the `prd-to-github-issues` pipeline.

---

## 1. Stack

| Layer | Choice | Rationale |
|---|---|---|
| Backend | **Python 3.11+ / FastAPI** | Matches your existing environment; best-in-class for the data-import work that dominates this project |
| ORM / migrations | SQLAlchemy 2.x + Alembic | Migrations matter — food schema will change as packs reveal edge cases |
| Database | **PostgreSQL 16** | Needs real full-text search and generated columns; SQLite can't carry the food search |
| Search | Postgres FTS (`tsvector` + `pg_trgm`) | No Elasticsearch. A second stateful service kills one-command deployment |
| Frontend | **Next.js + Tailwind + shadcn/ui** | Matches your stack; responsive web only for v1 |
| Validation | Pydantic v2 (API) + JSON Schema (food packs) | Food packs are validated by CI outside the app, so the schema must be standalone |
| Auth | Session cookie + Argon2id | No third-party auth service. Self-hosted means no external dependency for login |
| Container | Docker Compose: `api`, `web`, `db` | Three services maximum |
| Tests | pytest (backend), Vitest (frontend) | Every issue ships tests — the pipeline requires it |

**Constraint:** no external API call is required for core functionality. The app must work fully air-gapped after the initial food seed. This is a load-bearing product promise, not a nice-to-have.

## 2. Data model

Licence separation from `04-DATA-LICENSING.md` is enforced in the schema. Do not collapse these tables.

```
users
  id, email, password_hash, created_at, settings_json

auth_sessions
  id, user_id, token_hash, csrf_token_hash, expires_at, revoked_at, created_at

auth_rate_limits
  scope, key_hash, window_started_at, attempt_count, updated_at

foods_reference          -- CC0, from USDA. Read-only at runtime.
  id, fdc_id, description, food_category, source='usda',
  license='CC0', nutrients_json, portions_json, updated_at

foods_community          -- CC0, from food packs. Read-only at runtime.
  id, pack_id, pack_version, slug, name, name_local, locale,
  category, provenance, source_uri, source_license, source_note,
  nutrients_json, portions_json, pack_license='CC0-1.0', contributed_by

foods_odbl               -- ODbL, opt-in v1 integration. Separate export path.
  id, barcode, product_name, brand, nutrients_json,
  source='openfoodfacts', database_license='ODbL-1.0',
  contents_license='DbCL-1.0', source_url, attribution_text
  -- Product images are not imported in v1.

foods_custom             -- private to one user, never exported in bulk
  id, user_id, name, nutrients_json, portions_json, created_at

recipes
  id, user_id, name, yield_grams, is_public
recipe_ingredients
  id, user_id, recipe_id, food_source_table, food_source_id, grams

log_entries
  id, user_id, logged_at, meal_slot, food_ref, grams,
  computed_nutrients_json          -- denormalised at write time

body_metrics
  id, user_id, recorded_at, metric_type, value, unit

workouts
  id, user_id, performed_at, notes
workout_sets
  id, user_id, workout_id, exercise_id, set_index, reps,
  load_value, load_unit             -- unit is an enum, see §4

exercises
  id, slug, name, muscle_groups, equipment, source,
  source_id, source_url, derivative_source_url,
  license_spdx, license_url, author, author_url,
  attribution_text, translation_attribution_json

targets
  id, user_id, day_type, kcal, protein_g, carb_g, fat_g, active_from
```

### Design decisions worth stating so they aren't relitigated

- **`nutrients_json` not a nutrient table.** Micronutrient sets vary wildly by source; a normalised `food_nutrients` table with 150 sparse rows per food is a performance and complexity tax. Store a validated JSON object keyed by nutrient code. Index the handful used in search and totals as generated columns.
- **`computed_nutrients_json` is denormalised on write.** A log entry must not change retroactively when the underlying food data is corrected. Users' historical logs are immutable records of what they believed they ate.
- **`food_ref` is polymorphic** across four tables. Store as `(source_table, source_id)`. Ugly but correct — the licence separation is worth more than schema elegance here.
- **Every user-owned row carries an indexed, non-nullable `user_id`.** Child rows also use composite owner-and-parent foreign keys, so a recipe ingredient or workout set cannot point at another user's parent record.
- **Nutrient values are always per 100g internally.** All display conversion happens at the edge. Every unit bug in this category comes from mixed internal representations.

## 3. Food pack loader

The most important subsystem. See `docs/foodpack-spec.md` for the format.

Pipeline: `discover → parse → validate (JSON Schema) → check provenance → dedupe by slug → upsert → record pack version`

- Idempotent. Re-running the loader on the same packs is a no-op.
- Packs are versioned. An updated pack updates rows; it does not create duplicates.
- Validation failures are per-entry, not per-pack. One bad entry does not reject 200 good ones — it reports and skips.
- The loader runs as a CLI command (`opennosh foods load ./packs`) **and** in CI against pull requests. Same code path, so CI and runtime cannot diverge.

## 4. Exercise and load modelling

Deliberately loose, because equipment is heterogeneous.

`load_unit` enum: `kg`, `lb`, `bodyweight`, `band`, `machine_units`, `rpe_only`.

Rationale: cable and digital-resistance machines report a number that isn't a true weight and isn't comparable across devices. Forcing it into a `weight_kg` field produces silently wrong volume totals. Store the unit, compute volume only within matching units, and refuse to aggregate across incompatible ones.

The v1 exercise catalogue imports data from wger, not code. Its versioned v1 allowlist contains only `CC-BY-SA-3.0`; entries with missing, ambiguous, NC, ND, or any other license are rejected until legal review explicitly expands that list. The importer stores the source identifier, object URL, derivative source URL, exact SPDX identifier and license URL, author and author URL, attribution text, and translation-level attribution. It never relabels imported exercise data as CC0. The wger application is AGPL-licensed and none of its application code may be copied into the MIT opennosh codebase.

Treat every imported attribution field as untrusted input. Enforce types and conservative length limits, allow only `http` or `https` source URLs, render author and attribution values as escaped text, and render validated URLs through safe link components. Never store or render source-supplied HTML. Importer tests must include malicious attribution and URL fixtures that prove script markup and unsafe schemes cannot execute.

Exercise search, detail, UI notices, and export return the complete attribution record. The exercise export remains separately identified as `CC-BY-SA-3.0`, includes the license and source notices, and is never combined with the CC0 food-pack dump.

The Open Food Facts client sends a descriptive identifying `User-Agent` containing the opennosh version and maintainer contact. It preserves ODbL database and DbCL contents notices in storage and export. v1 does not request, cache, or display product images; adding images later requires a separate CC BY-SA attribution design.

## 5. API surface

REST, JSON, `/api/v1`. The UI consumes only this API — no server-side data access shortcuts, because third-party clients and future mobile apps depend on parity.

```
POST   /auth/register | /auth/login | /auth/logout
GET    /auth/session
GET    /foods/search?q=&locale=&source=
GET    /foods/{source}/{id}
GET    /foods/barcode/{barcode}        -- requires the enabled OFF integration
POST   /foods/custom
GET    /exercises/search?q=&muscle=&equipment=
GET    /exercises/{id}                 -- complete source/license/author attribution
GET    /recipes            POST /recipes            PUT /recipes/{id}
GET    /log?date=          POST /log                DELETE /log/{id}
GET    /targets            PUT  /targets
POST   /body-metrics       GET  /body-metrics?from=&to=
POST   /workouts           GET  /workouts?from=&to=
GET    /export/me                       -- full user data, JSON
GET    /export/foods/community          -- CC0 dump preserving source_uri/source_license/contributed_by
GET    /export/foods/odbl               -- separate, attributed, only if integration enabled
GET    /export/exercises                -- separately identified, per-entry license and attribution
```

Search ranking: exact slug > community pack matching user locale > USDA generic > community other locales > ODbL branded. Generic before branded is the single ranking rule that most improves perceived quality.

## 6. Deployment

```yaml
services:
  db:   postgres:16     # named volume
  api:  ./api           # runs migrations on boot, then seeds if empty
  web:  ./web
```

- First boot runs migrations, then seeds USDA + bundled community packs. Seed is the slow part; show progress, don't hang silently.
- `.env.example` carries every variable with placeholders. No real values in the repo, ever.
- Health endpoint at `/healthz` reporting DB connectivity and seed status.

## 7. Constraints for the implementing agent

Put these in `AGENTS.md`:

- **No network calls during test execution.** Agents run sandboxed. USDA, OFF, and wger fixtures live in `tests/fixtures/`.
- **Migrations are always additive within a PR.** No destructive migration without a `needs-human` label.
- **Never merge the food tables.** Any PR that adds a cross-table write between `foods_odbl` and `foods_community` gets rejected. State the reason in `CONVENTIONS.md` so it isn't rediscovered.
- **`02-PRD.md` §7 health safety constraints are hard requirements.** A PR introducing streaks, shaming copy, or unbounded target entry fails review regardless of code quality.
- **Every PR includes tests.** No exceptions for "trivial" changes.
- **Tenant ownership comes from the authenticated session.** Never accept a request-controlled `user_id` for a user-owned resource. Every CRUD and `/export/me` issue includes cross-user IDOR tests proving one account cannot read, mutate, or delete another account's data.
- **One concern per PR, reviewable in ~15 minutes.**

## 8. Suggested issue decomposition

Rough shape for the pipeline — refine when you run it.

**Foundation (`blocking`, must merge sequentially):**
1. Repo scaffold, Docker Compose, CI
2. Database schema + Alembic migrations (all tables, licence columns non-nullable)
3. Auth, user model, session-derived tenant filtering, and cross-user IDOR test helpers
4. Nutrient representation + unit conversion utilities *(everything downstream depends on this being right)*

**Parallel after foundation:**
5. USDA bulk importer
6. Food pack JSON Schema + validator (standalone, used by CI)
7. Food pack loader CLI
8. Food search endpoint + ranking
9. Log entry CRUD + daily totals
10. Recipe composition
11. Targets, including day-type variants
12. Body metrics
13. Workout logging with the load-unit enum
14. wger exercise importer with a `CC-BY-SA-3.0` allowlist, complete attribution preservation, hostile-input fixtures, and attributed export
15. Open Food Facts barcode integration with an isolated ODbL store and attributed export
16. Data export endpoints
17. Web UI: daily log view
18. Web UI: food search, barcode lookup, and custom food entry
19. Web UI: trends

**`needs-human`:**
20. Final review of health-safety copy throughout the UI
21. Final legal review of MIT, CC0, ODbL, and imported per-entry exercise notices before public launch
