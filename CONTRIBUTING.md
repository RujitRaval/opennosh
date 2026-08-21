# Contributing to opennosh

opennosh has a scoped v1 implementation and is open for contributions. Discuss feature changes in an issue before writing code. Small documentation corrections and reproducible bug fixes can proceed directly.

## Contributor workflow

1. Update local `main` with `git pull --ff-only origin main`.
2. Create a short, descriptive branch from `main`.
3. Make one coherent development change and add or update its tests.
4. Install locked dependencies with `make install`, install Chromium once with `npx --prefix web playwright install chromium`, then run `make lint typecheck test web-e2e build compose-config`.
5. Open a pull request and complete the template. Merge only after all required checks pass.

Food-pack changes must also run `make foodpack-validate`. Blocking errors make that command fail;
review warnings remain machine-readable but do not change its exit status.

Food-pack contributors do not need GStack or an `agent/` branch. Maintainer and agent-authored development uses the stricter workflow in `CLAUDE.md`: `agent/<short-description>` branch, GStack `/review`, then GStack `/ship`. Those maintainer pull-request titles use `vMAJOR.MINOR.PATCH.MICRO type: summary`.

Exercise importer changes must run the offline fixture tests and PostgreSQL integration suite. Tests
must never call the live wger service. Keep the v1 license allowlist exact: only unambiguous
`CC-BY-SA-3.0` records may enter the catalogue, and source, derivative, author, license, and
translation attribution must remain intact. Add hostile fixtures for any newly accepted input shape,
including markup and unsafe URL cases. Import wger data only; do not copy AGPL application code.

Open Food Facts changes must use local HTTP fixtures and the PostgreSQL integration suite; tests
must never call the live service. Keep barcode access disabled by default, request only the explicit
non-image field allowlist, write fetched records only to `foods_odbl`, and preserve the ODbL 1.0
database and DbCL 1.0 contents notices in lookup and separate export responses.

## Scope boundaries

Accepted contribution types include food packs, translations, and bug fixes with tests. Exercise-catalogue changes must follow the per-entry license and attribution boundary in `04-DATA-LICENSING.md`; do not submit bulk exercise data without a prior issue.

Do not submit:

- feature pull requests without a prior issue;
- unrelated refactors or dependency churn;
- credentials, personal health data, or private exports;
- data copied from proprietary nutrition applications or databases;
- generated bulk submissions that a maintainer cannot review quickly.

Food packs are limited to 100 entries per pull request. By submitting a pack, you confirm that you have authority to dedicate the eligible original material under CC0 1.0 and that it was not copied from a proprietary application or restricted database. Third-party source fields must use the structured allowlist in `docs/foodpack-spec.md`; unknown or restrictive source licenses are rejected. The pull-request template records this sign-off.

CC0 does not legally require attribution, but opennosh preserves contributor names in current pack metadata, the product UI, and `AUTHORS.md`. Contributors may request removal from those current credit surfaces without withdrawing the CC0 dedication. Existing Git commits, releases, and pull-request records ordinarily remain in repository history.

## Review expectations

Food packs are reviewed weekly when maintainer capacity allows. Code pull requests are reviewed when possible; this project is not the maintainer's job. Clear scope, green checks, and a small diff make a timely review more likely.
