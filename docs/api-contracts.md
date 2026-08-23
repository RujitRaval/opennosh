# API and web contracts

opennosh publishes one canonical OpenAPI document and generates TypeScript transport types
from it. This keeps the API and website honest without coupling UI components to generated
files.

## Contract layers

- API success responses use named Pydantic models. The food-search envelope carries
  `schema_version: "2.0"` for its cursor and snapshot fields; the other public food envelopes
  remain on `schema_version: "1.0"`.
- Expected application failures use RFC 9457-compatible `application/problem+json` with a
  stable problem code, schema version, request reference, and typed recovery extensions. The
  `/healthz` probe is the deliberate exception: its `503` response remains the typed operational
  health-state JSON consumed by deployment monitors.
- HTTP exception details are public API copy. Route authors must use reviewed, user-safe text;
  unexpected exceptions always receive a neutral detail and never expose exception strings.
- `web/lib/generated` is reproducible output. Do not edit it by hand.
- Only `web/lib/api` and its facade may import generated transport types. Adapters map those
  types into the stable handwritten models consumed by React components.
- Browser network failures have no HTTP status. Unknown or malformed problem documents become
  safe unexpected outcomes and retain a request reference when one is available.

## Regeneration

Run:

```sh
make contracts-generate
```

The command exports the canonical OpenAPI JSON, runs the pinned generator, and writes a manifest
containing the contract version, generator version, and input SHA-256 digest.

Run all contract gates with:

```sh
make contracts-check
```

CI rejects dirty regeneration, generated imports outside the transport boundary, and breaking
changes without an OpenAPI contract major-version increase.

## Compatibility fixtures

Golden fixtures under `web/tests/fixtures/contracts` cover the current and N-1 food-search
success contracts plus the previous legacy `{"detail": "..."}` problem shape. The web adapter
maps a v1 offset response into null cursor metadata while v2 exposes `next_cursor`,
`snapshot_id`, and `snapshot_expires_at`. Keep N and N-1 fixtures when a contract is versioned
so rolling API and website deployments remain compatible.

## Food-search cursor contract

The first page omits `cursor`. A response with `has_more: true` includes an opaque signed
`next_cursor` that must be replayed with the same normalized query, locale, source filter, and
page size. The token binds cursor and ranking versions, a retained projection snapshot, a SHA-256
fingerprint of the normalized search inputs, the last deterministic rank/tie position, page size,
and expiry. It never contains raw search text.

The current signing key signs new tokens while the current and previous keys verify them. Invalid,
altered, malformed, and oversized tokens return `search_cursor_invalid` with HTTP 400. Expired
snapshots, mismatched inputs, changed ranking policy, and retired keys return
`search_cursor_restart` with HTTP 409 and a `restart_search` action pointing to the current first
page. An unsupported signed cursor schema or ranking version also requires restart; a missing version
is invalid. The API does not silently cross projection snapshots within a pagination journey. Its
production entrypoint disables raw access logging so query and cursor parameters are not logged.
