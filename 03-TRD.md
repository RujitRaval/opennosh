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
| Container | Docker Compose: `ingress`, `web`, `api`, `migrate`, `capacity-preflight`, `db` | One-command deployment with validated database capacity, one-shot migrations, and nginx as the only public web ingress |
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

contribution_drafts      -- owner-scoped operational proposals; not accepted food data
  id, user_id, client_draft_id, workflow_version, draft_version, review_state,
  fields_json, duplicate_candidates_json, submission_id, submission_key_hash,
  submitted_at, created_at, updated_at
contribution_draft_operations
  draft_id, operation_id, resulting_version, created_at

publication_intents
  id, source_draft_id, source_draft_version, reviewed_decision_id,
  approving_actor_id, approved_payload_digest, workflow_version,
  workflow_revision, state, idempotency_key_hash
publication_steps
  id, publication_intent_id, step_name, ordinal, destination, step_version,
  state, queue_job_id, lease_token, lease_owner, lease_expires_at,
  next_attempt_at, input_digest, observation_json
publication_durable_acknowledgements
  id, publication_intent_id, acknowledgement_kind, destination,
  content_digest, external_reference, verified_at
accepted_events
  id, publication_intent_id, repository, commit_sha, pack_id, record_id,
  event_type, receipt_digest, published_at

recipes
  id, user_id, name, yield_grams, is_public
recipe_ingredients
  id, user_id, recipe_id, position, food_source_table, food_source_id,
  food_source_key, food_name, grams, computed_nutrients_json
  -- identity + nutrients denormalised at composition time

log_entries
  id, user_id, logged_at, meal_slot,
  food_source_table, food_source_id, food_source_key, food_name,
  quantity_amount, quantity_unit, portion_name, grams,
  computed_nutrients_json          -- identity + nutrients denormalised at write time

body_metrics
  id, user_id, recorded_at, metric_type, value, unit
  -- metric_type: body_weight, body_fat_percentage, height, or named circumference
  -- unit pairs are constrained: weight kg/lb, body fat percent, length cm/in

workouts
  id, user_id, performed_at, notes
workout_sets
  id, user_id, workout_id, exercise_id, set_index, reps,
  load_value, load_unit             -- unit is an enum, see §4

exercises
  id, slug, name, muscle_groups, equipment, search_text, source,
  source_id, source_url, derivative_source_url,
  license_spdx, license_url, author, author_url,
  attribution_text, translations_json, translation_attribution_json,
  source_updated_at

targets
  id, user_id, day_type, kcal, protein_g, carb_g, fat_g,
  active_from, active_until, below_floor_confirmed,
  safety_review_required, safety_floor_kcal
```

### Design decisions worth stating so they aren't relitigated

- **`nutrients_json` not a nutrient table.** Micronutrient sets vary wildly by source; a normalised `food_nutrients` table with 150 sparse rows per food is a performance and complexity tax. Store a validated JSON object keyed by nutrient code. Index the handful used in search and totals as generated columns.
- **`computed_nutrients_json` is denormalised on write.** A log entry must not change retroactively when the underlying food data is corrected. Users' historical logs are immutable records of what they believed they ate.
- **`food_ref` is polymorphic** across four tables. Store as `(source_table, source_id)`. Ugly but correct — the licence separation is worth more than schema elegance here.
- **Every user-owned row carries an indexed, non-nullable `user_id`.** Child rows also use composite owner-and-parent foreign keys, so a recipe ingredient or workout set cannot point at another user's parent record.
- **Nutrient values are always per 100g internally.** Sources may declare `per_100g` or `per_100ml`; volume-based declarations require an explicit `density_g_per_ml` and are canonicalised before persistence. Gram, millilitre, and named-portion calculations use a fixed 50-significant-digit decimal context, produce immutable snapshots, and quantize only at display or API boundaries. Every unit bug in this category comes from mixed internal representations, ambient decimal settings, or an assumed density.

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

The v1 exercise catalogue imports downloaded `exerciseinfo` data from wger, not code. Its versioned v1 allowlist contains only `CC-BY-SA-3.0`; the short name, full name, and license URL must all agree, and entries with missing, ambiguous, NC, ND, or any other license are rejected until legal review explicitly expands that list. The offline, idempotent importer stores the source identifier and timestamp, object URL, derivative source URL, exact SPDX identifier and license URL, author and author URL, attribution text, cleaned translations, and translation-level attribution. An older source timestamp never overwrites a newer row. It never relabels imported exercise data as CC0. The wger application is AGPL-licensed and none of its application code may be copied into the MIT opennosh codebase.

Treat every imported attribution field as untrusted input. Enforce types and conservative length limits, allow only `http` or `https` source URLs, render author and attribution values as escaped text, and render validated URLs through safe link components. Never store or render source-supplied HTML. Importer tests must include malicious attribution and URL fixtures that prove script markup and unsafe schemes cannot execute.

Exercise search uses PostgreSQL full-text plus exact muscle and equipment filters, bounded pagination, a statement timeout, and per-IP rate limiting. Search, detail, UI notices, and export return the complete attribution record. The exercise export has its own per-IP limit and statement timeout, and rejects catalogues above 10,000 rows or 64 MiB of serialized JSON. It remains separately identified as `CC-BY-SA-3.0`, includes the license, source, and ShareAlike notices, and is never combined with the CC0 food-pack dump.

The Open Food Facts client is disabled by default and never runs during startup. When enabled, a
cache miss calls `GET /api/v3/product/{barcode}` with only `code`, `product_name`, `brands`,
`nutriments`, and `nutrition`; image fields are neither requested nor retained. The client validates
the GTIN check digit before network access, applies a total request deadline without retries,
requests identity encoding, rejects encoded responses, streams through a response size cap, and
sends a descriptive identifying `User-Agent`
containing the opennosh version and
maintainer contact. Successful products are reduced to canonical per-100g nutrition before an
idempotent write to `foods_odbl`. It preserves ODbL database and DbCL contents notices in storage,
lookup, and the separate `/api/v1/export/foods/odbl` response (with
`/api/v1/export/foods/openfoodfacts` retained as a compatibility alias). Adding images later requires
a separate CC BY-SA attribution design. Cache misses share one database-backed outbound quota, and
the reusable HTTP client is closed during application shutdown. No database transaction remains
open while the external request is in flight.

## 5. API surface

REST, JSON, `/api/v1`. The UI consumes only this API — no server-side data access shortcuts, because third-party clients and future mobile apps depend on parity.

Successful responses use named JSON schemas. Expected failures use RFC 9457-compatible
`application/problem+json`; the typed `/healthz` 503 response is the exception. The canonical
OpenAPI artifact, compatibility policy, generated TypeScript boundary, and regeneration commands
are documented in `docs/api-contracts.md`.

```
POST   /auth/register | /auth/login | /auth/logout
GET    /auth/session
POST   /contribution-drafts
GET    /contribution-drafts/{id}?requested_stage=
PATCH  /contribution-drafts/{id}
POST   /contribution-drafts/{id}/submit
GET    /foods/capabilities
GET    /foods/search?q=&locale=&source=&limit=&cursor=
GET    /foods/{source}/{id}
GET    /foods/barcode/{barcode}        -- requires the enabled OFF integration
GET    /export/foods/openfoodfacts      -- compatibility alias for /export/foods/odbl
POST   /foods/custom                    -- authenticated, owner-private, CSRF-protected
GET    /exercises/search?q=&muscle=&equipment=
GET    /exercises/{id}                 -- complete source/license/author attribution
GET    /recipes?limit=&offset=            POST /recipes
GET    /recipes/{id}       PUT /recipes/{id}        DELETE /recipes/{id}
GET    /logs?day=&timezone=           POST /logs
GET    /logs/{id}                     DELETE /logs/{id}
GET    /logs/daily-totals?day=&timezone=
GET    /logs/daily-totals/range?from=&to=&timezone=
GET    /targets                         PUT /targets
GET    /targets/resolve?day=&day_type=
POST   /body-metrics       GET  /body-metrics?from=&to=       DELETE /body-metrics/{id}
GET    /body-metrics/trends?from=&to=
GET    /workouts?from=&to=&limit=&offset=       POST /workouts
GET    /workouts/{id}       PUT /workouts/{id} DELETE /workouts/{id}
POST   /workouts/{id}/sets
PUT    /workouts/{id}/sets/{set_id}            DELETE /workouts/{id}/sets/{set_id}
GET    /workouts/volume?from=&to=&exercise_id=&load_unit=
GET    /workouts/trends?from=&to=
GET    /export/me                       -- full user data, JSON
GET    /export/foods/community          -- CC0 dump preserving source_uri/source_license/contributed_by
GET    /export/foods/odbl               -- separate, attributed, only if integration enabled
GET    /export/exercises                -- separately identified, per-entry license and attribution
```

Every export is a versioned JSON object generated under a repeatable-read, read-only PostgreSQL
snapshot. The server validates and spools one row at a time into a secure bounded-memory temporary
file, closes the database transaction, and streams the file to the client. Database and schema
failures therefore occur before response headers, and slow clients never pin a database connection.
Public exports share a bounded semaphore held through response completion, so at most two 64 MiB
public spool files exist by default. Private exports use a separately reserved one-slot semaphore
and no byte ceiling. Both paths impose configurable response deadlines and deterministically close
their temporary file and release capacity on completion, timeout, or client disconnect.
`/export/me` authenticates the owner from the session, applies the owner predicate independently to
every private table, emits flat resource sections with stable IDs and source snapshots, excludes
authentication secrets, and sends `Cache-Control: no-store`. It has no row ceiling. Public exports
remain independently rate-limited, statement-time-bounded, and capped at 10,000 rows/64 MiB of
exact serialized JSON. Their queries touch exactly one licensed store: `foods_community`, `foods_odbl`, or the
allowlisted wger exercise rows. This keeps application memory bounded without joining private,
CC0, ODbL/DbCL, or CC-BY-SA records into one legal surface.

Search ranking: exact slug > community pack matching user locale > USDA generic > community other locales > ODbL branded. Generic before branded is the single ranking rule that most improves perceived quality.

Body metric list bounds are required inclusive UTC calendar dates. Records use a stable
`id`, `recorded_at`, `metric_type`, `value`, and `unit` JSON shape so `/export/me` can reuse
the same private representation without conversion or interpretation.

Workout sets preserve explicit units: `kg`, `lb`, `bodyweight`, `band`, `machine_units`, and
`rpe_only`. Kilograms, pounds, and machine units require a nonnegative `load_value`; bodyweight and
band sets omit it; and RPE-only sets store a rating from 1 through 10 in that field. Ordered set
positions remain stable after edits and compact after deletion. Volume is available only for `kg`,
`lb`, and `machine_units`, is grouped by exercise and unit, and is never aggregated across
incompatible units. Workout set responses embed the complete exercise attribution record. Workout
and nested set reads and writes are owner-scoped; cross-tenant identifiers are indistinguishable
from missing records.

## 6. Deployment

```yaml
services:
  db:   postgres:16     # named volume
  capacity-preflight: ./api  # validates topology and live PostgreSQL capacity, then exits
  migrate: ./api             # runs Alembic once after preflight, then exits
  api:  ./api                # starts only the FastAPI web role after migration succeeds
  web:  ./web
  ingress: nginx:1.27   # public web entry point; replaces forwarding headers
```

- Every boot validates the versioned global connection-capacity manifest and live PostgreSQL ceiling before running one migration job. Web and worker processes never run migrations on startup.
- USDA and community-pack loading remains an explicit operator action after the schema is current.
- The publication role uses namespaced PgQueuer delivery only to wake the opennosh-owned durable
  ledger. T10 installs the deterministic planner, bounded executor, reducer, and wake-up handler,
  but the production replica count remains zero until the governed forge, evidence, and signed
  receipt adapters land in T2, T3, and T5; see
  [`docs/spikes/t4-pgqueuer.md`](docs/spikes/t4-pgqueuer.md).
- `.env.example` carries every variable with placeholders. No real values in the repo, ever.
- nginx publishes port 3000, the API host port stays loopback-only, and the web/API proxy trust chain uses a unique 32+ character `WEB_PROXY_TOKEN` in production.
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
