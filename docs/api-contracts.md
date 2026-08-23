# API and web contracts

opennosh publishes one canonical OpenAPI document and generates TypeScript transport types
from it. This keeps the API and website honest without coupling UI components to generated
files.

## Contract layers

- API success responses use named Pydantic models. Public food response envelopes carry
  `schema_version: "1.0"`.
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

Golden fixtures under `web/tests/fixtures/contracts` cover the current contract and the
previous legacy `{"detail": "..."}` problem shape. Keep N and N-1 fixtures when the contract is
versioned so rolling API and website deployments remain compatible.
