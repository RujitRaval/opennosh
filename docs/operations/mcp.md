# MCP preview operations

`opennosh-mcp` is the installable, read-only MCP 1.0.0 stdio server for opennosh public data. The
artifact is preview software. Production discovery remains disabled and shipping it does not enable
publication claims, federation discovery, mission discovery, or embeds.

## Start the server

Use the hosted service:

```sh
opennosh-mcp --target hosted
```

Or select one self-hosted origin at process startup:

```sh
opennosh-mcp --target https://nosh.example
```

`OPENNOSH_MCP_TARGET` supplies the same startup setting when `--target` is absent. A command-line
option takes precedence over the environment. Targets follow the SDK policy: origin-only HTTPS, or
HTTP only for an exact loopback host. User information, paths, queries, fragments, non-HTTP schemes,
and cross-origin redirects are rejected. Do not place credentials in either setting.

Configure an MCP client to launch the `opennosh-mcp` executable over stdio. Stdout is reserved for
protocol messages. Diagnostic logs go to stderr and contain only method, status, latency, and result
counts.

## Tool and result contract

The server exposes exactly these tools:

- `search_foods`
- `get_public_food`
- `get_public_missions`
- `get_public_mission_activity`
- `get_release_manifest`
- `validate_pack`

Remote tools delegate only to the supported asynchronous Python SDK. They cannot choose a request
host, send credentials, mutate state, or access operator functions. `validate_pack` is local and
accepts one in-memory JSON object no larger than 1,048,576 UTF-8 bytes; it does not accept a path or
URL and does not read or write the filesystem.

Each response is one JSON object with `schema_version`, `state`, and `data`; failures also include a
typed `problem`. Treat only `verified` and `stale_verified` remote states as publication proof.
`unavailable` must never be upgraded to a published claim. Validation returns `valid` or `invalid`.
Food data retains its release identity, source, license, attribution, and provenance fields.

## Verification

From a source checkout, run:

```sh
uv run pytest -q api/tests/mcp
make developer-compatibility-check
make package-check
```

The MCP tests include an official-client stdio negotiation, the exact tool allowlist, strict input
schemas, proof-state behavior, error sanitization, pack-size enforcement, and log redaction.

## Rollback

Keep `OPENNOSH_MCP_DISCOVERY_ENABLED=false` in production. To withdraw the preview, leave discovery
disabled, revert the compatibility manifest's MCP status to `disabled` in a reviewed patch release,
and deprecate the affected immutable package version rather than overwriting it. Existing signed
public reads remain available; rollback does not delete records or provenance and does not change
publication worker claims or concurrency.
