# License and dataset notice review

This document is the human approval record for Issue #24. It is an engineering review package, not
legal advice and not a substitute for review by qualified counsel or an authorized legal reviewer.

## Approval record

| Field | Value |
|---|---|
| Status | Approved |
| Reviewer | `@RujitRaval` |
| Reviewer capacity | Project owner |
| Approval date | 2026-08-21 |
| Reviewed commit | `b33f61ea2f1400ccbe3395ca9abc527de818e0c4` |
| Scope | Combined code, dataset, UI, export, contributor, and distribution notices |

All six dispositions were approved with no additional required changes. Public launch remains
blocked until blocker #45 and this approval PR land on `main`.

## Reviewer disposition matrix

| Material and source | Operative terms | Storage and use boundary | Required notices | Reviewer disposition |
|---|---|---|---|---|
| Original opennosh code and documentation | MIT | Root application and documentation, except specifically identified data | Root `LICENSE` in every source distribution; copyright and permission notice retained in substantial copies | Approved |
| Eligible original community food packs | CC0 1.0 Universal | `packs/` source and `foods_community`; never accepts share-alike or proprietary database input | `packs/CC0-1.0.txt`, pack dedication, contributor authority sign-off; visible credit is voluntary and not an added restriction | Approved |
| USDA FoodData Central Foundation and SR Legacy records | CC0 1.0 Universal | `foods_reference`; production bulk data imported by operators and not bundled; small test fixtures identified separately | FoodData Central source retained per record and shown in the UI; suggested USDA source credit in `NOTICE.md` | Approved |
| Open Food Facts database and contents | ODbL 1.0 and DbCL 1.0 | Optional `foods_odbl` cache; disabled by default; no writes to community or private stores; no images; small test fixtures identified separately | Identifying User-Agent; result attribution; ODbL/DbCL URLs and notice in separate export; combined distribution and UI notice | Approved |
| Accepted wger exercise entries | CC BY-SA 3.0 per accepted entry | Separate exercise catalogue; exact allowlist; no wger AGPL code or production bulk catalogue; small test fixtures identified separately | Source, object and derivative URLs, author, license, and translation attribution per entry; ShareAlike notice in separate export; combined distribution and UI notice | Approved |
| Authenticated user foods, logs, recipes, body metrics, targets, and workouts | Private user data; no public dataset license | Owner-scoped stores and authenticated personal export only | Private-export notice; exclusion from every public dataset export | Approved |

Each disposition records `Approved`, `Approved with required changes`, or `Rejected`. Any required
changes must link to blocking GitHub issues and land before the approval status can become final;
this review identified none.

## Notice inventory

### Code and source distribution

- [`LICENSE`](../LICENSE) carries the complete MIT grant for original software and documentation.
- [`NOTICE.md`](../NOTICE.md) is the combined distribution notice and links each operative source
  term without claiming that MIT or CC0 applies to third-party share-alike data.
- [`LICENSES.md`](../LICENSES.md) maps repository paths and runtime datasets to their applicable
  terms.
- The API and web runtime images each include `LICENSE`, `LICENSES.md`, and `NOTICE.md` so the
  release artifacts retain the code and combined data notices.
- Python distribution metadata explicitly includes `LICENSE`, `LICENSES.md`, `NOTICE.md`, and
  `AUTHORS.md` in every wheel or source archive.
- [`packs/LICENSE.md`](../packs/LICENSE.md) and
  [`packs/CC0-1.0.txt`](../packs/CC0-1.0.txt) carry the community-pack dedication and legal code.
- `NOTICE.md` identifies the small USDA, Open Food Facts, and wger test-fixture directories and
  preserves the corresponding source terms instead of treating their data elements as MIT code.

### User interface

- `/notices` presents the combined notice in the web application.
- A global `Licenses & data notices` footer link makes the page reachable before and after sign-in,
  including from nutrition and trends screens.
- Food search and barcode results keep source-specific attribution beside the selected result.
- Exercise API responses preserve per-entry and translation attribution for future workout screens.

### API and exports

- `/api/v1/export/foods/community` identifies the dataset as CC0 1.0 and retains contributor credit.
- `/api/v1/export/foods/odbl` and its compatibility alias identify Open Food Facts, ODbL 1.0, and
  DbCL 1.0 and retain per-row attribution.
- `/api/v1/export/exercises` identifies CC BY-SA 3.0, states ShareAlike, and retains all accepted
  per-entry attribution.
- `/api/v1/export/me` identifies its contents as private owner data, not a public dataset.

### Contributor sign-off

- `CONTRIBUTING.md` and the pull-request template require authority to dedicate eligible original
  food-pack material under CC0 and reject proprietary or restricted database copies.
- Pack validation permits only the documented structured provenance and source-license allowlist.

## Verified engineering controls

- License-separated database tables and public export paths are enforced in the schema and service
  layer.
- Open Food Facts requests use an identifying `opennosh/<version> (<contact>)` User-Agent and omit
  image fields.
- The wger importer accepts only exact CC-BY-SA-3.0 records and preserves attribution fields.
- Repository checks require all combined notice surfaces and key source/license identifiers.
- Container packaging checks require the MIT license, licensing map, and combined notice in both
  runtime images.
- Python packaging metadata declares every code and combined notice file instead of relying on
  build-backend discovery heuristics.
- Automated checks verify fixtures and response envelopes; they do not substitute for this human
  disposition.

## Primary references checked

- [MIT License, Open Source Initiative](https://opensource.org/license/mit)
- [CC0 1.0 Universal legal code, Creative Commons](https://creativecommons.org/publicdomain/zero/1.0/legalcode.en)
- [FoodData Central API guide and CC0 notice](https://fdc.nal.usda.gov/api-guide/)
- [Open Food Facts API licensing guidance](https://openfoodfacts.github.io/documentation/docs/Product-Opener/api/tutorials/license-be-on-the-legal-side/)
- [ODbL 1.0 legal text, Open Data Commons](https://opendatacommons.org/licenses/odbl/1-0/)
- [DbCL 1.0 legal text, Open Data Commons](https://opendatacommons.org/licenses/dbcl/1-0/)
- [wger license summary](https://wger.readthedocs.io/en/stable/#licence)
- [CC BY-SA 3.0 legal code, Creative Commons](https://creativecommons.org/licenses/by-sa/3.0/legalcode)

## Approval statement

The project owner approved all six dispositions at the reviewed commit shown above and identified
no additional required changes. This approval record was added after that exact review candidate;
it does not alter the reviewed notice package or its linked implementation surfaces.
