# Changelog

All notable changes to opennosh will be documented in this file.

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
