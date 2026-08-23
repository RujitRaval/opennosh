# Query-plan artifacts

The harness writes versioned PostgreSQL JSON plans here inside each result bundle. Plans must come
from the production `FOOD_SEARCH_SQL` statement with `ANALYZE` and `BUFFERS` enabled. Each plan's
path and SHA-256 digest are recorded in both `result.json` and `artifact-manifest.json`.

Do not commit machine-specific passing plans as a universal baseline. Commit a plan only when it is
needed to explain a reviewed regression or gate change, and store its matching environment and
contract digests beside it.
