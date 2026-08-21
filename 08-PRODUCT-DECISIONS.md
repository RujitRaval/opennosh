# 08 — Product decisions

These decisions were made by the project owner on 2026-08-19. They are settled inputs to the PRD, TRD, issue backlog, and implementation.

## Licensing

1. **Application code and project documentation use MIT.** The root `LICENSE` applies to original opennosh software and documentation. `LICENSES.md` maps the exceptions for community packs and third-party data.
2. **Community food packs use CC0 1.0 Universal.** Contributors knowingly dedicate eligible food-pack material under CC0. opennosh still preserves and visibly displays contributor credit through `contributed_by`, pack metadata, and `AUTHORS.md`.
3. **Third-party data keeps its own license.** Open Food Facts remains ODbL-separated. wger exercise entries retain their exact per-entry Creative Commons license and attribution. Neither source is relicensed as CC0.

## v1 product scope

4. **v1 includes nutrition and strength training.** Both are first-release product surfaces, not a deferred expansion.
5. **v1 is multi-user.** Authentication and strict per-user data isolation are foundation work.
6. **Barcode and Open Food Facts integration ships in v1.** It remains opt-in, uses a separate ODbL store and export path, and is not required for the default offline-capable experience.
7. **The exercise catalogue is seeded from wger data.** The initial wger dataset is CC BY-SA 3.0 and current entries can carry per-entry license metadata. Retain the exact license and attribution, keep ShareAlike data identified on export, and copy no AGPL application code.

## Identity and operating limit

8. **The product name is `opennosh`.** Use this exact lowercase form for the product, repository, package slug, commands, and documentation.
9. **The initial build window is capped at two months.** After that window, switch to maintenance and evaluate the contributor thesis rather than allowing the build to expand indefinitely.

## Public-launch validation

The project owner approved the combined data notices on 2026-08-21; the source-by-source disposition
and reviewed commit are recorded in `docs/license-notice-review.md`. On the same date, npm, PyPI,
and preferred-domain availability were checked, the final secret scan was clean, the repository was
made public, GitHub Private Vulnerability Reporting was enabled, and Epic #3 was closed. Package and
domain availability was not reserved by the check. The remaining outreach checklist in
`07-LAUNCH-PLAN.md` does not reopen the decisions above.

## Explicitly omitted from v1

- UI internationalisation; food-pack locale metadata remains supported.
- Meal photos and their storage/privacy surface.
- AI estimation, coaching, or automated insights.
- Fasting-window tracking, for the health-safety reasons in `02-PRD.md` §7.

## Assumption to test

The contributor model assumes people will submit food packs through GitHub. That is not proven. If fewer than ten packs from five outside contributors arrive within sixty days after launch, treat the community thesis as falsified and decide whether to keep opennosh as a personal tool or archive the public contribution effort.
