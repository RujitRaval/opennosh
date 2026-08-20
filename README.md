# opennosh

Self-hosted nutrition and strength tracking built around food data the community can improve.

> **Build status:** The application foundation is underway. Follow the [v1 implementation epic](https://github.com/RujitRaval/opennosh/issues/3) for the dependency-ordered two-month build plan.

The application is MIT-licensed. Community food packs are dedicated under CC0 1.0 with visible contributor credit, and the repository remains private during the build window.

See [`LICENSES.md`](LICENSES.md) for the repository-wide licensing map.

## Quick start

Docker Compose starts PostgreSQL, the FastAPI service, and the Next.js app:

```bash
cp .env.example .env
docker compose up --build
```

Open the web app at <http://localhost:3000>. The API health endpoint is <http://localhost:8000/healthz>; it returns `200` when PostgreSQL is reachable and a safe `503` degraded response when the database is unavailable.

For native development, install Python 3.11+, uv, Node.js 24 LTS (24.15+; Node 25 is unsupported), npm, and Docker, then run:

```bash
make install
make lint typecheck test build compose-config
make foodpack-validate
```

The API container upgrades the PostgreSQL schema to the latest Alembic revision before it starts. For native development, set `DATABASE_URL` and manage the schema directly:

```bash
make db-upgrade
make db-downgrade
```

`db-downgrade` returns a development database to the empty base revision. Do not run it against data you need to keep.

After upgrading the database, validate and load one pack or every pack below a repository root:

```bash
uv run opennosh foods load ./packs --json
```

The loader commits valid entries, reports and skips invalid entries, treats an unchanged pack as a
no-op, and refuses to overwrite a newer pack version.

### Food search API

Search USDA reference foods and CC0 community foods without combining their source records:

```text
GET /api/v1/foods/search?q=apple&locale=en-IN&source=community&limit=20&offset=0
GET /api/v1/foods/community/apple
GET /api/v1/foods/usda/171688
```

Every result uses a source-qualified ID such as `community:apple` or `usda:171688` and returns
the source and license metadata needed for attribution. Results rank exact community slugs first,
then community foods matching the requested locale, USDA generic foods, and community foods from
other locales. The optional `source` filter accepts `community` or `usda`.

Raw queries must contain 2–100 characters; after whitespace normalization they must still contain
at least two characters and a letter or number. A request returns at most 50 rows and accepts
offsets up to 10,000. Public search defaults to 120 requests per source IP per 60 seconds and a
500 ms PostgreSQL statement timeout. Configure those guards with
`FOOD_SEARCH_RATE_LIMIT_ATTEMPTS`, `FOOD_SEARCH_RATE_LIMIT_WINDOW_SECONDS`, and
`FOOD_SEARCH_STATEMENT_TIMEOUT_MS`.

PostgreSQL full-text and trigram indexes back the search. The integration performance gate loads
10,000 representative rows, requires both full-text indexes in the analyzed unified-query plan,
and budgets less than 100 ms of PostgreSQL execution time.

## Authentication

The API provides local account registration, login, session inspection, and logout under `/api/v1/auth`. Passwords are hashed with Argon2id and opaque sessions are stored in PostgreSQL; no third-party identity provider is required.

Set `APP_ENVIRONMENT=production` in production. This enables Secure, host-only session and CSRF cookies. Browser clients must copy the `opennosh_csrf` cookie (or `__Host-opennosh-csrf` in production) into the `X-CSRF-Token` header for authenticated state-changing requests. API handlers must use the session-derived helpers in `opennosh_api.auth.tenant`; request bodies and query parameters must never select a `user_id`.

## Nutrient calculations

The `opennosh_api.nutrition` module validates nutrient maps, canonicalises source values to a per-100-gram internal basis, and converts grams, millilitres, and named household portions into immutable nutrient snapshots. Volume conversion requires an explicit food density; opennosh never guesses that one millilitre equals one gram. Calculations use a fixed 50-significant-digit decimal context, presentation rounding happens only through the API-boundary helper, and its JSON payload uses decimal strings so values do not change in transit.

## Food logging

Authenticated users can create, list, read, and delete tenant-isolated food-log entries under
`/api/v1/logs`. Create requests use the `source` and `source_id` returned by food search, plus an
offset-aware timestamp, a configurable meal-slot label, and a quantity in grams, millilitres, or
a named household portion:

```json
{
  "logged_at": "2026-08-20T18:30:00-04:00",
  "meal_slot": "post workout",
  "food": {"source": "community", "source_id": "dal-rice"},
  "quantity": {"amount": "1.5", "unit": "portion", "portion_name": "1 bowl"}
}
```

`POST /api/v1/logs` and `DELETE /api/v1/logs/{entry_id}` require the authenticated session's
CSRF token in `X-CSRF-Token`. The server resolves the source food, converts the quantity, and
stores the food identity, original quantity, gram mass, and computed nutrients on the log row.
Later source-food corrections therefore never rewrite historical nutrition. To correct an entry,
delete it and create a replacement; there is deliberately no recalculating update endpoint.

Use `GET /api/v1/logs?day=2026-08-20&timezone=America/New_York` for a stable paginated local-day
view and `GET /api/v1/logs/daily-totals?day=2026-08-20&timezone=America/New_York` for exact daily
mass and nutrient totals. The timezone parameter accepts IANA names and overrides the user's saved
`settings_json.timezone`; when neither exists, the API uses UTC. Day boundaries are converted to
UTC after applying the selected timezone, including 23- and 25-hour daylight-saving days. Every
read and mutation derives `user_id` from the session, returns `404` for another user's entry or
custom food, and sends `Cache-Control: no-store`.

## Private recipes

Authenticated recipe CRUD is available under `/api/v1/recipes`. A create or full-replacement
request supplies a name, the finished recipe's yield in grams, and one to 100 source-qualified
ingredients with gram quantities:

```json
{
  "name": "Sunday dal",
  "yield_grams": "1400",
  "ingredients": [
    {"food": {"source": "usda", "source_id": "169090"}, "grams": "200"},
    {"food": {"source": "custom", "source_id": "7d735537-5ddf-4ec4-91ad-1f8153229619"}, "grams": "30"}
  ]
}
```

Ingredient sources may be `usda`, `community`, `openfoodfacts`, or `custom`. List recipes with
`GET /api/v1/recipes?limit=50&offset=0`; `limit` accepts 1–100, `offset` accepts 0–10,000, and the
response includes `has_more`. `POST`, `PUT`, and `DELETE` require the session CSRF token.

`POST` and `PUT` snapshot each ingredient's identity, exact mass, and nutrients. Recipe detail and
totals therefore remain stable if an underlying public food changes or a private custom food is
deleted. The response includes whole-recipe totals and a yield-derived per-100-gram profile. All
recipe reads and writes are owner-scoped, respond with `Cache-Control: no-store`, and keep recipes
out of public food-pack data.

Log a recipe through the ordinary `POST /api/v1/logs` endpoint with
`{"source":"recipe","source_id":"<recipe UUID>"}`. A gram quantity scales the stored profile
directly. A named portion of `"whole recipe"` maps exactly to the stored yield, so an amount of
`"0.25"` logs one quarter of the recipe. The resulting log is itself immutable and remains readable
after the recipe is edited or deleted.

## Calorie and macro targets

Authenticated users manage their own dated target schedule under `/api/v1/targets`. A full
replacement uses the session CSRF token and supplies non-overlapping inclusive date ranges for
`training` and `rest` days:

```json
{
  "items": [
    {
      "day_type": "training",
      "kcal": "2500",
      "protein_g": "180",
      "carb_g": "300",
      "fat_g": "65",
      "active_from": "2026-08-01",
      "active_until": null
    }
  ]
}
```

Use `GET /api/v1/targets` to read the complete schedule and
`GET /api/v1/targets/resolve?day=2026-08-20&day_type=training` to resolve one date
deterministically. Target values are always entered by the user; opennosh never calculates or
prescribes them. The configurable `TARGET_KCAL_FLOOR` defaults to 1200 kcal. A lower user-entered
value is accepted only when that schedule item includes `"confirm_below_floor": true`, and the
confirmation plus the applicable floor are stored with the target. All target responses are
owner-scoped and send `Cache-Control: no-store`.

## Private body metrics

Authenticated users can create, list, and delete their own measurements under
`/api/v1/body-metrics`. Create a record with the session CSRF token:

```json
{
  "recorded_at": "2026-08-20T08:30:00-04:00",
  "metric_type": "body_weight",
  "value": "80.125",
  "unit": "kg"
}
```

Supported pairs are `body_weight` with `kg` or `lb`, `body_fat_percentage` with
`percent`, and `height`, `waist_circumference`, `hip_circumference`,
`chest_circumference`, `neck_circumference`, `upper_arm_circumference`, or
`thigh_circumference` with `cm` or `in`. Values are positive exact decimals with at most
four decimal places.

List an inclusive UTC date range with
`GET /api/v1/body-metrics?from=2026-08-01&to=2026-08-31&limit=100&offset=0`.
Both dates are required, results are newest first, and the response includes `has_more`.
Every query is owner-scoped; deleting another user's ID returns the same `404` as a missing
record. Successful and failed responses send `Cache-Control: no-store`. The stable record
shape (`id`, `recorded_at`, `metric_type`, `value`, and `unit`) is also the representation
reserved for the future authenticated `/export/me` response. opennosh stores and reports
these numbers without streaks, shaming, or automated medical interpretation.

## Private strength workouts

Authenticated users can create and manage their own workouts under `/api/v1/workouts`. A workout
has a timezone-aware `performed_at`, optional notes, and up to 500 ordered sets. Each set refers to
an attributed exercise and records reps plus one of `kg`, `lb`, `bodyweight`, `band`,
`machine_units`, or `rpe_only`. For example:

```json
{
  "performed_at": "2026-08-20T18:00:00-04:00",
  "notes": "Upper body",
  "sets": [
    {
      "exercise_id": "66fef1bf-7bb3-4ccf-bd52-dd661006075b",
      "reps": 8,
      "load_value": "60",
      "load_unit": "kg"
    }
  ]
}
```

Use `GET /api/v1/workouts?from=2026-08-01&to=2026-08-31` for an inclusive UTC-date
range. `POST /api/v1/workouts/{workout_id}/sets`,
`PUT /api/v1/workouts/{workout_id}/sets/{set_id}`, and the corresponding `DELETE` endpoint
append, edit, and remove sets without changing the surviving sets' relative order. Mutations
require the session CSRF token, and all responses send `Cache-Control: no-store`.

Volume is computed only for `kg`, `lb`, and `machine_units`, and remains separated by exercise
and unit. `GET /api/v1/workouts/volume?from=2026-08-01&to=2026-08-31&exercise_id=<id>`
refuses to combine incompatible units; add `&load_unit=kg` or another exact unit to select one.
Bodyweight, band, and RPE-only sets remain useful records but are never converted into an invented
numeric volume.

### USDA reference-food import

The offline importer accepts FoodData Central JSON files, official JSON ZIP archives,
official relational CSV ZIP archives, or extracted CSV directories. It imports only
Foundation and SR Legacy foods into `foods_reference`; branded, FNDDS, and experimental
rows are ignored. Each accepted row retains its FDC ID, USDA source, CC0 license, source
publication timestamp, nutrients per 100 grams, and gram-based household portions.

Download the bulk files from the [FoodData Central dataset page](https://fdc.nal.usda.gov/download-datasets/),
run migrations, then pass one or more archives:

```shell
make db-upgrade
make usda-import USDA_PATHS="downloads/foundation.zip downloads/sr-legacy.zip"
```

The importer uses `DATABASE_URL` by default and writes 500 records per batch. Override
either setting by including `--database-url <url>` or `--batch-size <count>` in
`USDA_PATHS` after the input paths.

The job streams large JSON archives, bounds archive expansion and record collections,
upserts on FDC ID, and prints progress after each database batch. A rerun updates the same
rows instead of duplicating them, while an older USDA release cannot overwrite a newer
one. Malformed or incomplete source records are identified by FDC ID on standard error;
valid records are still written, and the command exits nonzero when any rows were
rejected. Error output retains a bounded sample and reports how many additional issues
were omitted.

---

## Product documents

| File | Purpose | Who reads it |
|---|---|---|
| `01-RESEARCH.md` | Competitive landscape, the actual gap, why now | You |
| `02-PRD.md` | Product requirements, MVP scope, explicit non-goals | You + `prd-to-github-issues` |
| `03-TRD.md` | Stack, data model, services, API surface | You + `prd-to-github-issues` |
| `04-DATA-LICENSING.md` | **Read this first.** The ODbL constraint that shapes the architecture | You, before any code |
| `docs/foodpack-spec.md` | The contribution unit. The most important file here | Contributors + implementing agent |
| `06-CONTRIBUTOR-MODEL.md` | How the community layer actually works | You |
| `07-LAUNCH-PLAN.md` | Naming, positioning, launch sequencing | You |
| `08-PRODUCT-DECISIONS.md` | Settled product, licensing, scope, and operating decisions | You + implementing agent |
| `CONTRIBUTING.md` | Contribution workflow, boundaries, and validation commands | Contributors |
| `CONVENTIONS.md` | Health-safety, product, and data constraints | Contributors + implementing agent |
| `SECURITY.md` | Private vulnerability-reporting process | Security reporters + maintainers |
| `AUTHORS.md` | Maintainer and contributor credit | Contributors + users |
| `CHANGELOG.md` | Versioned record of shipped changes | Users + maintainers |
| `TODOS.md` | Deferred launch-readiness work | Maintainers |

---

## How to use this

**Do not hand the whole folder to an agent and say "build it."** That produces a 4,000-line PR nobody can review.

The intended path:

1. Read `04-DATA-LICENSING.md` and `08-PRODUCT-DECISIONS.md` before implementation.
2. Treat `02-PRD.md` and `03-TRD.md` as the settled product and technical inputs.
3. Run the issue-generation pipeline against `02-PRD.md` and `03-TRD.md` to produce a dependency-ordered issue queue.
4. Keep `docs/foodpack-spec.md` in the implementation repository; contributors and the validator both depend on it.
5. `01`, `06`, and `07` are retained only while this repository is private planning space. Remove them from the public implementation tree before launch; they are strategy, not build input.

---

## The one-line pitch

> Every calorie tracker locks your data behind a subscription and can't find your dal. This one runs on your hardware, and the food database is a git repo you can send a PR to.

---

## The thing that will kill this project

Not the code. The food database.

Every prior attempt in this category either (a) leaned entirely on a crowd-sourced database with poor non-Western coverage, or (b) built a food table nobody else could contribute to. If food packs aren't trivially easy to write and merge, this becomes another solo-maintained tracker in a category that already has a dozen. `docs/foodpack-spec.md` is the load-bearing document.
