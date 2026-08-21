# Changelog

All notable changes to opennosh will be documented in this file.

## [0.22.0.2] - 2026-08-21

### Changed

- Confirm the documented Docker Compose quick start works from a clean clone on an independent
  Ubuntu 24.04 machine; the reproducible procedure and evidence are in
  [`docs/clean-install-verification.md`](docs/clean-install-verification.md).
- Record healthy PostgreSQL, API, web, and ingress services; the current Alembic head; successful LAN
  browser journeys; and persisted account and nutrition data after a full Compose restart.
- Mark the independent-machine launch check complete and retain the expected missing-target console
  response as a low-priority, non-blocking web-quality follow-up.

## [0.22.0.1] - 2026-08-21

### Changed

- Record the verified public PyPI `opennosh 0.22.0.0` and npm `opennosh 0.22.0` releases, including
  install commands, artifact hashes, license contents, source links, and public registry pages.
- Mark both initial-publication checklists complete and align the README, launch plan, product
  decisions, and TODO ledger with the live package state.
- Confirm both registries use active GitHub Actions trusted publishers for future tokenless releases.

## [0.22.0.0] - 2026-08-21

### Added

- Install the Python API and data-management command from the canonical `opennosh` distribution.
- Bootstrap a non-destructive local checkout with `npx opennosh init`, including clear setup handoff instead of automatic service or configuration changes.
- Publish tested PyPI and npm artifacts from `main` or an ancestry-verified release tag using restricted GitHub environments, short-lived OIDC credentials, and automatic provenance.

### Changed

- Derive Python package metadata from the repository `VERSION`, translate its first three components for npm, and verify both public identities in CI.
- Record the first-release authentication flow, registry ownership boundaries, retry behavior, and post-publication evidence checklist without storing credentials.

### Fixed

- Include the food-pack schema and all approved license and notice files in built Python artifacts.
- Read the installed distribution version when the repository-level `VERSION` file is unavailable.

## [0.21.0.2] - 2026-08-21

### Added

- Reach the public project through `opennosh.org` or `www.opennosh.org`, with permanent HTTPS redirects to the GitHub repository until a dedicated site is deployed.
- Contact the project through `support@opennosh.org` using free inbound forwarding without a paid mailbox subscription.
- Preserve a non-secret operations record for domain security, routing, email, and public verification.

### Changed

- Record the registered domain, DNSSEC, WHOIS privacy, registrar safeguards, and remaining unreserved npm and PyPI package names across launch and decision documents.

## [0.21.0.1] - 2026-08-21

### Changed

- Publish the repository with a private vulnerability-reporting path and record the completed v1 implementation epic.
- Record the clean final secret scan plus the checked, unreserved npm, PyPI, and preferred-domain names.
- Replace private-launch blockers in the README, security and contributor guidance, launch plan, product decisions, license-review record, and TODO ledger with the verified public state.

## [0.21.0.0] - 2026-08-21

### Added

- Read one combined notice for opennosh software, community foods, USDA reference data, Open Food Facts records, wger exercises, test fixtures, and private account data without treating them as one license.
- Reach an accessible Licenses and data notices page from the global web footer before or after signing in.
- Preserve the project owner's approval of all six source and license dispositions, including the reviewed commit, approval date, reviewer capacity, and absence of additional required changes.

### Changed

- Include the MIT license, repository license map, and combined notice in API and web runtime images and in Python distribution archives.
- Require repository checks to keep source identifiers, attribution, export terms, contributor sign-off, and release notice surfaces aligned.

### Fixed

- Cover incomplete, missing, and invalid-UTF-8 notice surfaces plus operative web links with regression tests.
- Keep the global notice footer at an accessible body-text size.

## [0.20.0.0] - 2026-08-21

### Added

- Protect health-sensitive nutrition, target, body-metric, and strength interactions with an approved screen-and-state review covering every currently available surface.
- Detect streaks, praise or blame, scores, guilt, food moralising, target judgement, compensatory exercise, automatic coaching, medical interpretation, social comparison, and fasting optimisation in user-facing web copy.
- Preserve the human reviewer, reviewed commit, approved inventory, and future settings, body-entry, and workout-entry follow-ups in a durable review record.

### Changed

- Explain below-floor calorie targets with neutral copy that names the configured floor and the deliberate confirmation needed to save the entered value.
- Filter API error details through the same health-safety rules before displaying them, with neutral retry copy when a prohibited pattern appears.
- Ignore local `.gstack` artifacts during documentation validation so generated review files cannot interfere with repository checks.

### Fixed

- Inspect concatenated strings and template-interpolated text so routine TypeScript composition cannot bypass prohibited-copy checks.
- Cover canonical “you went over/under” wording, safe API-detail preservation, neutral near-misses, and equal below/above-target DOM treatment with regression tests.

## [0.19.0.0] - 2026-08-20

### Added

- Review 7-, 30-, or 90-day nutrition, body-measurement, and strength history from a responsive authenticated Trends page.
- Switch between calories, macros, body metric and unit pairs, or exercise and load-unit pairs without ever combining incompatible units.
- Read every chart through a visible data table, operate all filters by keyboard, and get neutral empty and sparse-data states without diagnoses or coaching.
- Query bounded, owner-scoped nutrition totals, latest daily body measurements, and daily exercise volume through dedicated trend APIs.

### Changed

- Keep nutrition aligned to the browser's IANA calendar while body and strength records retain their documented UTC date boundaries.
- Aggregate body and workout trends on the server so opening Trends does not download full paginated workout and set histories.

### Fixed

- Ignore stale trend responses after a range change and preserve distinct timestamps for multiple body measurements.
- Cover timezone boundaries, tenant isolation, range limits, unit separation, empty states, accessible controls, desktop and mobile journeys, and PostgreSQL aggregation with API, component, and Playwright tests.

## [0.18.0.0] - 2026-08-20

### Added

- Search the local food catalogue from one accessible dialog while preserving source-aware API ranking, filtering between USDA and community foods, and showing safe source attribution.
- Look up packaged foods by barcode only when Open Food Facts is enabled, recover from missing or unavailable products, and retain the required ODbL and contributor notices.
- Create private custom foods with calories, macros, and optional household portions, then log them by grams or named portion without exposing them through public search or community exports.
- Cover ranked search, stale responses, barcode success and recovery, custom-food validation and isolation, keyboard navigation, session expiry, duplicate submissions, and desktop/mobile journeys with API, component, Playwright, and accessibility tests.

### Changed

- Make the food-entry dialog fully keyboard operable with focus trapping, ARIA tab relationships, arrow-key navigation, live status messages, and responsive scrolling for smaller screens.

### Fixed

- Ignore stale search and barcode responses after users change modes, and prevent rapid repeat actions from creating duplicate custom foods or lookups.
- Restore focus to the Meals heading after an add or delete refresh using a post-render focus request instead of timing-sensitive animation frames.

## [0.17.0.0] - 2026-08-20

### Added

- Use a responsive daily nutrition log to create an account or sign in, choose a date and training/rest target, search local foods, add gram-based meal entries, review daily calorie and macro totals, and confirm deletions.
- Recover from loading, empty, network-error, and expired-session states with clear next actions and neutral language that treats nutrition as a record rather than a scorecard.
- Show community-food contributor credit and use the browser locale when ranking regional foods.
- Cover the complete login, add, totals, and delete journey on desktop and mobile with component tests, Playwright, and automated WCAG 2.2 AA checks.

### Changed

- Route browser requests through same-origin `/api/v1` paths while streaming request and response bodies, preserving separate authentication cookies, forwarding CSRF protection, and rejecting unsafe paths.
- Put nginx in front of the Compose web service so source-address rate limits use a trusted, non-spoofable peer address without exposing the internal web or API services remotely.
- Extend pull-request checks to build and test the web app, run the accessible browser journey, boot the full Compose stack, and verify spoofed forwarding headers cannot split rate-limit buckets.

## [0.16.0.0] - 2026-08-20

### Added

- Download a private, authenticated account export containing the signed-in user's settings, custom foods, recipes, food logs, nutrition targets, body metrics, workouts, and sets without exposing credentials or another tenant's data.
- Download community foods, Open Food Facts records, and wger exercises from separate public endpoints with stable schemas and complete source, license, attribution, and contributor metadata.
- Cover tenant isolation, authentication, license boundaries, schema snapshots, source round-tripping, serialized-size limits, capacity exhaustion, client disconnects, and PostgreSQL-backed export behavior with automated tests.

### Changed

- Generate each export from one read-only, repeatable PostgreSQL snapshot, spool it before sending response headers, and release the database transaction before the client starts downloading.
- Bound public export rows, exact serialized bytes, concurrency, capacity wait time, and response duration while reserving independent capacity for private account exports.
- Document the canonical export endpoints, license separation rules, operator limits, and cache behavior in the README, technical design, data-licensing policy, and environment template.

### Fixed

- Close export iterators and temporary spools on completion, timeout, cancellation, disconnect, or send failure so abandoned downloads cannot retain resources or capacity slots.
- Reject oversized Open Food Facts exports based on actual serialized JSON rather than compressed PostgreSQL storage size.

## [0.15.0.0] - 2026-08-20

### Added

- Look up valid GTIN-8, GTIN-12, GTIN-13, and GTIN-14 barcodes through Open Food Facts when an operator explicitly enables the integration.
- Reuse successful Open Food Facts lookups in food logs and private recipes while keeping imported ODbL/DbCL records isolated from community-contributed foods.
- Export cached Open Food Facts records separately with explicit source, attribution, ODbL, and DbCL notices.
- Cover disabled mode, barcode validation, cache behavior, tenant safety, upstream failures, shared rate limits, response bounds, export limits, and client lifecycle with automated tests.

### Changed

- Bound third-party traffic with cache-first reads, per-client and shared egress rate limits, total request deadlines, an identifying user agent, and strict response-size limits before JSON decoding.
- Request only the Open Food Facts fields opennosh needs, discard image and unknown data, and sanitize untrusted text before persistence or API responses.
- Bound the public Open Food Facts export with its own rate limit, database statement timeout, row limit, and serialized-response size ceiling.

### Fixed

- Treat Open Food Facts throttling, malformed payloads, unsupported content encoding, invalid nutrition, unsafe Unicode, slow streams, and oversized responses as controlled API errors without caching partial data.
- Release database transactions before external network calls and share one lifecycle-managed HTTP client instead of holding database connections or creating a client for every lookup.

## [0.14.0.0] - 2026-08-20

### Added

- Import downloaded wger `exerciseinfo` JSON into an offline, idempotent exercise catalogue while preserving source timestamps and complete per-translation attribution.
- Search attributed wger exercises by name and text with exact muscle and equipment filters, stable pagination, and source-aware detail responses.
- Export the wger catalogue separately with explicit Creative Commons Attribution-ShareAlike 3.0 source, license, author, and ShareAlike notices.
- Cover hostile imports, migration safety, concurrent re-imports, source isolation, search plans, rate limits, timeouts, and export bounds with PostgreSQL-backed tests.

### Changed

- Normalize accepted wger license metadata to the canonical `CC-BY-SA-3.0` URL and reject ambiguous, unsupported, unsafe, or incomplete attribution records.
- Bound public exercise search and export with independent per-IP rate limits, PostgreSQL statement timeouts, indexed query plans, row limits, and a stored-data byte ceiling.

### Fixed

- Prevent stale or concurrent imports from replacing newer exercise records and reject partial paginated exports that could silently create incomplete catalogues.
- Validate legacy nested attribution, JSON element shapes, finite timestamps, safe URLs, and exact wger licensing before applying the catalogue migration.

## [0.13.0.0] - 2026-08-20

### Added

- Record private strength workouts with timezone-aware dates, notes, attributed exercises, reps, and ordered sets using `kg`, `lb`, `bodyweight`, `band`, `machine_units`, or `rpe_only`.
- Create, browse, update, and delete workouts and their sets while keeping surviving set order stable after edits and deletions.
- View exact exercise volume for compatible numeric units while refusing to combine kilograms, pounds, and machine units into a misleading total.
- Cover workout validation, attribution, authentication, CSRF protection, tenant isolation, concurrent appends, UTC boundaries, migration safety, and rollback with PostgreSQL-backed tests.

### Changed

- Keep every workout operation private with session-derived ownership and `Cache-Control: no-store` responses, including indistinguishable missing and cross-tenant nested resources.
- Aggregate date-range workout volume inside PostgreSQL, returning only unit-grouped totals instead of loading a user's workout history into API memory.
- Constrain workout notes, timestamps, set positions, reps, loads, and unit-value combinations in PostgreSQL while preserving valid legacy rows.

### Fixed

- Compact deleted set positions in a deterministic order so immediate uniqueness checks cannot fail because of database row-update order.
- Reject null RPE values, boolean rep counts, and numeric epoch timestamps at the applicable API and database boundaries.

## [0.12.0.0] - 2026-08-20

### Added

- Record body weight, body-fat percentage, height, and circumference measurements with explicit compatible units and exact decimal values.
- Browse private measurements across inclusive UTC date ranges with stable newest-first pagination, and delete individual records without exposing whether another user's record exists.
- Cover body metric validation, tenant isolation, CSRF protection, timestamp boundaries, migration safety, rollback, and export-compatible response shapes with automated tests.

### Changed

- Keep every body metric operation private with authenticated ownership checks and `Cache-Control: no-store` responses.
- Return one canonical UTC representation from create and list operations so future personal-data exports can reuse the same stable shape.
- Constrain stored metric types, units, values, compatible type-unit pairs, and finite timestamps in PostgreSQL while preserving valid legacy rows.

### Fixed

- Reject the Python timestamp extrema that asyncpg reserves for PostgreSQL infinity sentinels, preventing accepted API inputs from failing during database writes.

## [0.11.0.0] - 2026-08-20

### Added

- Create owner-set calorie, protein, carbohydrate, and fat targets for training and rest days across explicit active date ranges.
- Resolve the one applicable target for a requested date and day type with deterministic boundary behavior.
- Cover target validation, tenant isolation, safety confirmation, concurrent replacement, date resolution, and migration rollback with automated tests.

### Changed

- Keep every target operation private with authenticated ownership checks, CSRF protection on replacement, and `Cache-Control: no-store` responses.
- Require explicit confirmation for calorie targets below the configured neutral safety floor, and re-review unconfirmed schedules when that floor increases.
- Replace each owner's complete target schedule atomically while preventing overlapping ranges for the same day type.

### Fixed

- Preserve legacy target rows during migration without treating them as safety-confirmed, so they cannot resolve until the owner reviews and resaves them.

## [0.10.0.0] - 2026-08-20

### Added

- Create, list, read, update, and delete private recipes made from USDA, CC0 community, Open Food Facts, or private custom foods.
- Log recipe servings in grams or as deterministic fractions of the whole recipe yield while preserving an immutable nutrition snapshot in food history.
- Cover recipe composition, tenant isolation, source mutation and deletion, exact decimal yields, large batches, pagination, concurrency, migrations, and all supported ingredient stores with automated tests.

### Changed

- Store ingredient identity, exact mass, display name, ordering, and computed nutrients inside each recipe so later source-food changes cannot rewrite the recipe.
- Keep every recipe endpoint private with authenticated ownership checks, CSRF protection on mutations, `Cache-Control: no-store`, and no path into public contributor food packs.
- Resolve recipe ingredients in bounded source queries and read each recipe with its ingredient snapshots from one database statement.

### Fixed

- Serialize concurrent recipe edits with a row lock so two updates cannot interleave parent and ingredient versions.
- Preserve exact sub-milligram recipe yields and noncanonical valid UUID inputs, and allow whole-recipe portions for batches larger than 10 kilograms.

## [0.9.0.0] - 2026-08-20

### Added

- Log USDA, CC0 community, Open Food Facts, and private custom foods in grams, millilitres, or named portions through authenticated create, list, read, and delete endpoints.
- Preserve each logged food's public identity, display name, original quantity, exact gram mass, and computed nutrient snapshot so later source edits never rewrite nutrition history.
- View stable paginated log entries and exact daily nutrient totals using saved or requested IANA timezones, including 23- and 25-hour daylight-saving days.
- Cover every log endpoint with PostgreSQL integration tests for tenant isolation, CSRF, invalid quantities, all four food stores, immutable snapshots, pagination, timezone boundaries, and migration rollback.

### Changed

- Keep successful and failed food-log responses private with `Cache-Control: no-store`, and return stable validation errors for unsupported calendar and timestamp edges.
- Preserve valid USDA food-specific energy factors through quantity conversion and stored-snapshot reads.
- Extend log storage with exact quantity and immutable food-identity fields while preserving legacy external IDs during migration and exact sub-milligram values during rollback.

### Fixed

- Compute entry count, gram mass, and nutrient totals from one PostgreSQL statement so concurrent log changes cannot mix two database snapshots in one response.

## [0.8.0.0] - 2026-08-20

### Added

- Search USDA and CC0 community foods through unified, source-aware API endpoints with source-qualified IDs and complete attribution metadata.
- Fetch source-specific food details, including canonical nutrients and household portions.
- Add PostgreSQL full-text and trigram indexes plus an analyzed-plan performance gate against a representative 10,000-row dataset.

### Changed

- Rank exact community slugs first, followed by requested-locale community foods, USDA foods, and other community locales.
- Bound public search with strict query validation, stable pagination, per-IP rate limiting, and a transaction-local statement timeout.
- Verify the unified food-search schema revision during the container migration boot check, so continuous integration catches stale database state before merge.

## [0.7.0.0] - 2026-08-20

### Added

- Load one validated CC0 food pack or every pack below a repository root with `opennosh foods load`, including stable human-readable and JSON reports.
- Preserve canonical per-100-gram nutrition, portions, contributor credit, provenance, source URI, and source-license metadata for every accepted community food.
- Keep valid entries when a pack contains invalid ones, while reporting each rejected entry with its precise validation issue.
- Add PostgreSQL integration coverage for unchanged reloads, versioned updates, stale-pack protection, partial failures, metadata export, and concurrent retries.

### Changed

- Make food-pack loading idempotent and concurrency-safe with per-pack transaction locks, version ordering, conflict-safe inserts, and bounded transaction retries.
- Index community foods by pack ID and verify the new Alembic head during the container migration boot check.
- Package the API command entry point and food-pack schema in the production container, with documented loader usage.

## [0.6.0.0] - 2026-08-20

### Added

- Validate contributor food packs with a versioned Draft 2020-12 JSON Schema and one reusable implementation shared by local commands, continuous integration, and runtime callers.
- Emit stable machine-readable errors and non-blocking warnings for schema violations, provenance and licensing rules, nutrient plausibility, missing portions, short source notes, slug collisions, and near-duplicate profiles.
- Load editable YAML packs safely with deterministic discovery and explicit limits for files, bytes, nesting, entries, packs, and aggregate repository work.
- Provide valid and hostile fixtures plus boundary, loader, security, CLI, and cross-pack tests for every documented validation rule.

### Changed

- Reject aliases, duplicate or non-string YAML keys, symbolic links, invalid UTF-8, unsafe numeric values, and oversized runtime structures before expensive validation work begins.
- Document the food-pack validation command, contributor contract, CI gate, runtime API, and repository-level GStack workflow routing.

## [0.5.0.0] - 2026-08-19

### Added

- Import official USDA Foundation and SR Legacy bulk data from JSON files, JSON ZIP archives, relational CSV ZIP archives, or extracted CSV directories without requiring network access during the job.
- Preserve each accepted food's FDC ID, publication timestamp, USDA source, CC0 license, per-100-gram nutrients, category, and gram-based household portions in the reference-food store.
- Stream and validate source records in bounded batches, report malformed rows by source location and FDC ID, and retain valid rows for database import.
- Provide a documented `make usda-import` command with fixture-driven parser tests and real PostgreSQL coverage for repeat imports and release ordering.

### Changed

- Make USDA imports idempotent and concurrency-safe while preventing an older dataset release from replacing newer reference data.
- Accept bounded authoritative energy values and food-specific Atwater factors while retaining strict validation for ordinary nutrition data.
- Reject ambiguous or oversized archives, conflicting nutrient and portion values, unsupported units, and unbounded record collections before they can corrupt or exhaust the import process.

## [0.4.0.0] - 2026-08-19

### Added

- Represent source nutrition on a validated per-100-gram or per-100-millilitre basis and canonicalise it to one immutable per-100-gram profile before persistence.
- Convert grams, millilitres, and case-insensitive named household portions into deterministic nutrient snapshots without guessing food density.
- Reject missing macros, invalid nutrient codes, impossible physical values, non-finite numbers, unsafe numeric magnitudes, and inconsistent macro-derived energy.
- Produce explicitly rounded API payloads as exact decimal strings while retaining full internal precision for stored calculations.

### Changed

- Run nutrition arithmetic under a fixed 50-significant-digit decimal context so process-wide precision settings cannot change stored or computed results.
- Add property, boundary, schema, immutability, overflow, and wire-format tests covering every new nutrient calculation path.

## [0.3.0.0] - 2026-08-19

### Added

- Add local account registration, login, session inspection, and CSRF-protected logout endpoints under `/api/v1/auth`.
- Store passwords with Argon2id and opaque browser sessions as hashes, with secure `__Host-` cookies enabled in production.
- Add atomic per-IP and per-account authentication throttling with bounded retention and recovery after each rate-limit window.
- Provide reusable tenant-isolation helpers that derive ownership exclusively from the authenticated session and deny cross-account reads, updates, and deletes.

### Changed

- Extend the PostgreSQL schema with authenticated sessions and authentication rate-limit state, including a tested Alembic upgrade and downgrade.
- Document the development and production session-cookie behavior and environment configuration.

## [0.2.0.0] - 2026-08-19

### Added

- Create the complete nutrition and strength schema on a clean PostgreSQL 16 database, with a tested development rollback to the empty Alembic base revision.
- Keep USDA reference foods, CC0 community foods, Open Food Facts data, and private custom foods in distinct stores with their required provenance, licensing, and attribution fields.
- Protect every user-owned record with a required indexed owner reference, including database-enforced same-owner recipe ingredients and workout sets.
- Model recipes, food logs, body metrics, exercises, workouts, workout sets, and nutrition targets with validated units and value constraints.

### Changed

- Apply pending database migrations before the API starts and verify the full migration boot path in continuous integration.
- Provide native development commands for upgrading or downgrading the database schema.

## [0.1.0.0] - 2026-08-19

### Added

- Run a working local FastAPI, PostgreSQL, and responsive Next.js application stack with one Docker Compose command.
- Use the database-aware health endpoint to distinguish a healthy service from a safe degraded response, with bounded probes, operator logging, and a documented OpenAPI contract.
- Configure development database credentials while Docker images, Compose dependencies, and service health checks handle local orchestration.
- Develop reproducibly from locked Python and TypeScript dependencies, with commands and tests covering API success, failure, timeout, lifecycle, settings, and web rendering paths.
- Trust every pull request to run API linting, strict typing, unit and PostgreSQL integration tests, web linting, typing, tests, production builds, Compose validation, documentation, and whitespace checks.

## [0.0.1.0] - 2026-08-19

### Added

- Added the MIT repository license, CC0 food-pack dedication and legal code, contributor credits, and a repository-wide licensing map.
- Added repository-wide validation that rejects retired project-name variants in user-facing documentation and metadata.

### Changed

- Renamed the product, package, command, and documentation identity to exact lowercase `opennosh`.
- Finalized v1 as multi-user nutrition plus strength tracking with opt-in Open Food Facts barcode lookup and an attributed wger exercise catalogue.
- Set a hard two-month initial build window and documented tenant isolation, attribution safety, and source-license boundaries.

## [0.0.0.1] - 2026-08-19

### Added

- Added repository governance, automated document validation, and the branch-to-pull-request development workflow.
- Clarified unresolved license recommendations, the canonical food-pack specification path, and the current health-support resource policy.
