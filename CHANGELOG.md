# Changelog

All notable changes to opennosh will be documented in this file.

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
