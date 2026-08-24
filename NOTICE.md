# opennosh notices

This notice explains which terms apply to the software and data surfaces distributed by opennosh.
It does not replace the operative license texts and does not relicense third-party data.

## Original opennosh software and documentation

Original application code, scripts, tests, configuration, and project documentation are Copyright
© 2026 Rujit Raval and are distributed under the MIT License in [`LICENSE`](LICENSE). Distributions
must retain that copyright and permission notice.

## Third-party typefaces

The Living Commons interface embeds modified WOFF2 subsets derived from Archivo, Source Sans 3,
and IBM Plex Mono. Those font sources and derivatives remain under the SIL Open Font License 1.1;
the operative family-specific notices are retained in
[`web/assets/fonts/v2/licenses/`](web/assets/fonts/v2/licenses/). Generated derivatives use
opennosh-specific family, PostScript, and file names so Reserved Font Names are not reused.
The root MIT license does not relicense these font files.

## Community food packs

Eligible original material submitted under `packs/` is dedicated under CC0 1.0 Universal. The
operative legal code is included in [`packs/CC0-1.0.txt`](packs/CC0-1.0.txt), and the contribution
terms are in [`packs/LICENSE.md`](packs/LICENSE.md).

CC0 does not require attribution. opennosh still preserves visible contributor credit as a community
promise; that credit does not add a legal restriction to reuse.

## USDA FoodData Central

USDA FoodData Central data is published under CC0 1.0 Universal. opennosh retains FoodData Central
as the source on imported records and displays the source in food results. The four starter food
packs bundle 144 selected, source-linked records derived from pinned USDA releases. No complete
production USDA bulk dataset is bundled in this repository; small importer fixtures are described
below.

Suggested source credit: U.S. Department of Agriculture, Agricultural Research Service, FoodData
Central, <https://fdc.nal.usda.gov/>.

## Open Food Facts

The optional Open Food Facts integration is disabled by default. Open Food Facts database rights are
available under ODbL 1.0, and individual database contents are available under DbCL 1.0. opennosh
keeps cached rows in a separate store, displays Open Food Facts attribution with each result, and
exports the cache only through its attributed ODbL/DbCL export. Product images are not requested,
cached, or distributed. No production Open Food Facts cache or bulk dataset is bundled in this
repository; small API fixtures are described below.

- ODbL 1.0: <https://opendatacommons.org/licenses/odbl/1-0/>
- DbCL 1.0: <https://opendatacommons.org/licenses/dbcl/1-0/>
- Open Food Facts reuse guidance:
  <https://openfoodfacts.github.io/documentation/docs/Product-Opener/api/tutorials/license-be-on-the-legal-side/>

## wger exercise data

opennosh accepts only wger exercise entries carrying the exact CC-BY-SA-3.0 identifier and required
per-entry attribution. Attribution and ShareAlike requirements remain attached to API responses and
the separate exercise export. No wger bulk catalogue or AGPL application code is bundled in this
repository; small importer fixtures are described below.

- CC BY-SA 3.0 legal code: <https://creativecommons.org/licenses/by-sa/3.0/legalcode>
- wger license summary: <https://wger.readthedocs.io/en/stable/#licence>

## Test fixtures

Small, non-production fixtures under `api/tests/fixtures/usda/`,
`api/tests/open_food_facts/fixtures/`, and `api/tests/fixtures/wger/` exercise the corresponding
import and API boundaries. To the extent a fixture contains or adapts source data, the USDA CC0,
Open Food Facts ODbL/DbCL, or per-entry wger CC BY-SA terms above continue to apply. The root MIT
license does not relicense those source-data elements.

## Private user data

Authenticated personal exports contain the account owner's private data. They are not public
datasets and are never included in the CC0, ODbL/DbCL, or CC BY-SA public exports.

The engineering boundaries and source-specific requirements are documented in
[`04-DATA-LICENSING.md`](04-DATA-LICENSING.md). The repository-wide file map is in
[`LICENSES.md`](LICENSES.md).
