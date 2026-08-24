# Changelog

All notable changes to opennosh will be documented in this file.

## [0.38.0.0] - 2026-08-24

### Added

- Add a digest-pinned Playwright visual-regression lane with 29 reviewed baselines across Living
  Commons, food records, contribution, logo colorways, responsive layouts, accessibility states,
  and the unchanged Tracker.
- Add deterministic public proof-state, contribution, food-record, and Tracker fixtures with frozen
  time, local-font readiness, stable identifiers, and explicit motion semantics.

### Changed

- Make visual baseline updates carry hash-verified approval metadata and upload expected, actual, and
  diff renderings from the pinned Linux runtime for review.
- Isolate visual-only cache and fixture behavior from standard unit, localization, and end-to-end
  test lanes.

### Fixed

- Fail continuous integration when approved pixels drift, a new screenshot lacks a committed
  manifest entry, or logo and Tracker baselines change without an explicit reviewed update.
- Prevent Playwright visual specs from entering Vitest discovery and preserve the standard public
  Commons unavailable fixture outside the visual lane.

## [0.37.0.0] - 2026-08-24

### Added

- Add a route-aware interface-language control that preserves the independently selected food
  locale while moving between localized public pages.
- Add a deliberately expanded, non-production pseudo-locale and desktop/mobile browser coverage
  for the public site, localized metadata, and every contribution stage.

### Changed

- Move public navigation, homepage, contribution, food-record, truth-signal, and legal-notice copy
  into one typed catalog with deterministic English fallback.
- Keep English as the only shipped interface language while making future catalogs prove exact
  message, parameter, plural, array-entry, route, and metadata agreement before release.

### Fixed

- Reject hard-coded interface copy in localized public components and fail continuous integration
  when catalog keys, placeholders, plural shapes, hydration, language switching, or expanded text
  drift from the reviewed contract.
- Prevent the pseudo-locale from being negotiated from browser or cookie preferences or enabled in
  production, even when its test flag is set accidentally.

## [0.36.0.0] - 2026-08-24

### Added

- Let anyone open a public food record and see its identity, preparation, selected household
  portion, nutrition, source, release version, license, uncertainty, and provenance together.
- Add Metric and US portion displays while retaining canonical grams, complete nutrient and
  evidence ledgers, an honest record-history surface, correction links, and reusable API access.
- Keep explicitly related food records separate when values or licenses disagree, without
  averaging conflicts into a score or guessing relationships from fuzzy search results.

### Changed

- Server-render complete food-record content so identity, trust, nutrients, source, license, and
  provenance remain available without JavaScript; client code now owns only controls and retry.
- Show missing verification, revision, source, and contributor facts as unavailable instead of
  inventing proof, and keep Tracker navigation generic until record handoff is implemented.
- Align the food-record surface with the current Living Commons navigation, semantic typography,
  motion preferences, and contribution routes introduced after T18 was first prepared.

### Fixed

- Preserve valid BCP 47 food-locale preferences, omit the locale filter when no preference is
  selected, and reset record identity safely across route changes.
- Keep the primary trusted record visible without waiting on optional relationships, reject fuzzy
  same-food inference, and bound server API reads with an honest retryable failure state.
- Reflow the full trust hierarchy at mobile and 200-percent zoom-equivalent widths, preserve
  canonical units, expose accessible focus and contrast states, and honor reduced motion.

## [0.35.0.0] - 2026-08-24

### Changed

- Refresh the approved Living Commons reference so the finalized Explore, Contribute, Commons, and
  Build hubs read as one movement while Tracker remains a clearly separate utility.
- Make the reference trustworthy at every viewport with a fixed verified-record snapshot, an honest
  quiet Commons state, trust-first food records, the complete contribution journey, and six
  surface-correct production wordmarks.
- Record reproducible acceptance evidence, responsive browser checks, design-audit results, and
  exact source hashes for the persistent reference artifact.

## [0.34.0.0] - 2026-08-24

### Added

- Establish the canonical Living Commons design system with scoped public tokens, six approved
  wordmark colorways, and versioned self-hosted Archivo, Source Sans 3, and IBM Plex Mono assets.
- Add automated logo, typography, color, contrast, focus, offline-asset, and route-isolation checks
  to the local, build, and continuous-integration quality gates.

### Changed

- Give the public website semantic type, color, spacing, radius, motion, light, and dark roles while
  preserving the Tracker's independent Trebuchet-based visual contract.
- Load public fonts only when a public route uses them, avoiding automatic preloads on independent
  Tracker routes and removing 119,488 bytes of unused font transfers from a direct Tracker visit.

### Fixed

- Keep every approved opennosh wordmark legible on its intended surface and validate each exact
  foreground-to-background pairing instead of relying on a self-referential asset manifest.
- Resolve public font-variable aliases at the same scope as the generated font definitions so the
  intended display, reading, and data typefaces render reliably in production.

## [0.33.0.0] - 2026-08-24

### Added

- Start a food-record proposal at `/en/contribute` and move through evidence, details, duplicate
  checking, provenance, and exact-proposal review without needing Git knowledge.
- Save work immediately on the device, sign in only when handing it over, resume an authenticated
  server draft, and follow a stable server-verified receipt after submission.
- Add owner-scoped contribution-draft APIs with optimistic versions, idempotent field patches and
  submission, capability-based stage repair, duplicate rechecking, and a truthful review receipt.

### Changed

- Preserve original grams, ounces, pounds, or serving units beside canonical grams, keep contributor
  credit and source terms visible, and separate review intake from accepted public data.
- Publish the real contribution entry point in the public navigation and use a responsive
  three-chapter, five-stage layout with error focus, compact progress, and safe-area actions.

### Fixed

- Prevent stage navigation from reloading a server draft over unsynced device edits, and isolate
  those edits from the anonymous device draft.
- Keep both halves of the wordmark visible on Tomato contribution surfaces, avoid mobile actions
  covering form choices, and keep deep-route header context from sending contributors backward.
- Recheck exact-name duplicates on the server, require review of every newly found candidate, and
  prevent repeated device handoffs or submit retries from creating duplicate drafts or receipts.
- Mark every private contribution-draft API response as non-cacheable and provide a recoverable
  error state when a server draft cannot be opened.

## [0.32.0.0] - 2026-08-24

### Added

- Add a signed latest-release pointer and content-addressed release-manifest reader with typed live,
  quiet, stale, partial, illustrative, and unavailable public commons snapshot states.
- Add the database-independent `/api/v1/public/commons-snapshot` endpoint, generated web contract,
  stable domain adapter, exact-content ETags, and read-only Compose artifact mount.
- Add verified hero and footer counts plus accepted-activity events, proof metadata, quiet recovery
  actions, stale age, and visibly non-production illustrative fixtures.

### Changed

- Resolve one immutable snapshot on the server and pass it through the homepage render tree so hero,
  activity, freshness, and footer cannot drift across release boundaries.
- Delay optional decoration until streamed content hydrates and keep unavailable, quiet, stale,
  partial, reduced-motion, data-saver, low-power, and no-JavaScript paths free of speculative motion.

### Fixed

- Never display a verified record count or accepted event when the latest pointer, signed manifest,
  API response, or first published release cannot be trusted.
- Bind cache validators to exact snapshot content and distinguish missing first-run artifacts from
  invalid signed pointers.
- Keep release signing authority offline with Ed25519 public-key verification, reject release
  rollback or equivocation through a durable checkpoint, and bound manifest and snapshot sizes.
- Preserve public conditional-cache headers through the web ingress and use the approved five-minute
  activity and revalidation bucket.

## [0.30.0.0] - 2026-08-23

### Added

- Add a CSS-first Living Commons movement layer whose full content and actions remain present in
  server-rendered HTML before JavaScript or animation is available.
- Add a small preference and device-capability gate, a separately loaded visibility controller,
  offscreen/background pausing, a two-region activity cap, and automatic decoration shutdown when
  frame or main-thread budgets are breached.
- Capture browser Web Vitals locally and expose a same-page custom event for future first-party
  telemetry without sending visitor measurements to an external service.
- Add an emitted-chunk budget audit and six-profile production browser benchmark covering desktop,
  mobile, reduced motion, data saver, low power, and no JavaScript, with CI artifacts for review.

### Changed

- Run ambient orbit, ribbon, and section-settle effects only after the client proves the visitor is
  eligible and the corresponding region is visible.
- Gate release quality on a 12 KB gzip motion ceiling, a 45 KB gzip public design-delta ceiling,
  50 ms long-task and 20 ms p95 frame limits, and the Core Web Vitals good thresholds.

### Fixed

- Prevent reduced-motion, data-saver, and low-power visitors from starting the optional movement
  runtime, while preserving the same readable, actionable public experience.

## [0.29.0.0] - 2026-08-23

### Added

- Let people move through four stable, localized task hubs: Explore, Contribute, Commons, and
  Build, each with a clear purpose, breadcrumb trail, and next action.
- Add one typed public navigation registry for routes, interface-language fallback, feature-gated
  child tools, and independent food-locale query state.
- Add desktop and mobile browser coverage for deep links, active-page context, unsupported
  languages, Tracker isolation, and deterministic menu focus.

### Changed

- Treat Tracker, interface language, and account-oriented controls as utilities instead of public
  platform hubs, while keeping the full four-hub trunk visible at every release stage.
- Give unfinished hub capabilities an honest quiet state and reveal child destinations only when
  their release flags are enabled.

### Fixed

- Keep long hub titles inside small mobile viewports, make public navigation targets touch-safe,
  and preserve readable wordmark contrast on the dark Build surface.
- Route the mobile homepage context action to Explore instead of a section that does not exist on
  the homepage.

## [0.28.0.0] - 2026-08-23

### Added

- Give every web, worker, migration, and administration process an explicit PostgreSQL connection
  budget, least-privilege credential boundary, bounded wait time, statement deadline, and unique
  operational identity.
- Add independent packaged commands for the web API, publication, evidence, projection,
  reconciliation, scheduling, and one-shot database migrations, with inactive future workers
  failing closed until their queue drivers are installed.
- Return a typed, retryable `503 database_capacity_exhausted` response when the web pool is full
  and expose protected role-attributed pool utilization and acquisition-latency metrics.

### Changed

- Gate deployment startup on a versioned global capacity manifest, the complete deployed role
  topology, and the live PostgreSQL `max_connections` value before running one migration job and
  starting the web role.
- Run representative database benchmarks with the active role budget and preserve reserved
  migration, administration, monitoring, recovery, and failover headroom in release gates.
- Validate every production console command and the bundled capacity manifest in built and
  installed Python distributions.

## [0.27.0.0] - 2026-08-23

### Added

- Provide a versioned, reproducible performance benchmark for 10,000, 100,000, and 1,000,000-food
  catalogues with representative read, write, boundary, cache, and concurrency workloads.
- Produce machine-verifiable benchmark evidence, query plans, resource measurements, browser-edge
  timings, and integrity manifests that can be validated independently before a release.
- Add deterministic corpus generation, guarded PostgreSQL seeding, extraction-ready miss history,
  and documented commands for running smoke, realistic, and release-scale profiles.

### Changed

- Enforce the benchmark contract, schemas, semantic result validation, and reproducibility tests in
  repository quality checks so incomplete or self-attested performance evidence cannot pass CI.

## [0.26.0.0] - 2026-08-23

### Added

- Add retained, immutable food-search projection snapshots with deterministic rank and tie-break
  positions for stable pagination across catalogue updates.
- Add opaque HMAC-SHA256 search cursors bound to the projection snapshot, normalized query and
  filters, ranking policy, page size, expiry, and an N/N-1 signing-key ring.
- Publish typed invalid-cursor and restart-search problem responses, generated cursor transport
  types, and current/previous web contract adapters.

### Changed

- Replace public food-search offsets with signed keyset cursors and return snapshot identity,
  expiry, and the next cursor in the v2 search success contract.
- Reject malformed, altered, mismatched, oversized, expired, or retired-key cursors before query
  execution and provide a safe first-page recovery link when a search must restart.

## [0.25.0.0] - 2026-08-23

### Added

- Publish a canonical, versioned OpenAPI contract with typed food success envelopes and RFC 9457
  problem details, field guidance, recovery actions, retry timing, and request references.
- Generate pinned TypeScript transport types behind stable per-domain web adapters, including
  current and previous contract fixtures for rolling deployments.
- Add safe public and tracker route error boundaries that preserve useful recovery messages without
  exposing unexpected exception details.

### Changed

- Return expected API and same-origin proxy failures as cache-safe `application/problem+json`
  responses with server-generated correlation IDs.
- Enforce deterministic regeneration, generated-import isolation, and unversioned breaking-change
  detection in CI.

## [0.24.0.0] - 2026-08-23

### Added

- Launch the localized Living Commons homepage with task-first public navigation, an honest empty
  activity state, and accessible mobile behavior.
- Establish the production design system with self-hosted fonts, six contrast-safe logo colorways,
  route-scoped assets, and documented brand guidance.
- Record the finalized opennosh movement-platform plan, delivery gates, trust model, and implementation
  sequence.

### Changed

- Move the private nutrition application to the canonical `/tracker` surface and isolate its layout,
  styles, fonts, and navigation from the public website.
- Redirect legacy public, trends, and notices routes to their canonical localized or tracker
  destinations.

## [0.23.0.2] - 2026-08-22

### For contributors

- Keep the trends browser test synchronized with the selected date range even when duplicate initial requests arrive.

## [0.23.0.1] - 2026-08-22

### Changed

- Record the controlled CI rejection proof for an invalid food pack and mark every pre-announcement
  launch check complete.

## [0.23.0.0] - 2026-08-21

### Added

- Install four starter food packs with 165 entries for Gujarati home cooking, North Indian staples,
  common vegetarian proteins, and supplements and powders.
- Trace 144 entries to pinned USDA FoodData Central releases and disclose the source ingredients,
  cooked yields, and calculations behind 21 recipe-derived entries.
- Rebuild every committed entry deterministically from local copies of the official source releases.

### Changed

- Show visible contributor credit and document pack counts, source checksums, validation results,
  representative nutrient checks, limitations, and the public-domain data boundary.

## [0.22.0.3] - 2026-08-21

### Added

- See the core opennosh journey immediately in the README: search the food catalogue, log a serving,
  and watch the daily calorie and macro totals update.
- Open an accessible final-state image when the single-play animation has finished or motion is not
  useful.

### Changed

- Mark the README product-walkthrough launch check complete with an optimized animation, descriptive
  alternative text, and a linked still image.

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
