# Tracking-free embed preview

opennosh embed protocol 1.0 renders small, server-generated views of already-public food records.
There is no loader, custom element, authentication, mutation, or discovery endpoint in this preview.
Production discovery remains disabled.

## Embed routes

Use the latest verified public record:

```text
https://opennosh.org/embed/v1/foods/{source}/{source_id}
```

Or pin provenance to one exact public release:

```text
https://opennosh.org/embed/v1/releases/{release_version}/foods/{source}/{source_id}/provenance
```

Every successful card visibly includes its source-qualified ID, license, attribution, release,
verification state, and a direct provenance link. Only `verified` and `stale` verified records render.
Missing, malformed, unproved, redirected, oversized, or unavailable records fail closed as a 404 or
503 card and are never upgraded to a publication claim.

## Safe iframe integration

```html
<iframe
  id="opennosh-food"
  title="opennosh food: Rajma masala"
  src="https://opennosh.org/embed/v1/foods/community/rajma-masala"
  sandbox="allow-scripts allow-same-origin allow-popups"
  style="width:100%;height:320px;border:0"
></iframe>
<script>
  const frame = document.querySelector("#opennosh-food");
  addEventListener("message", (event) => {
    if (event.source !== frame.contentWindow) return;
    if (event.origin !== "https://opennosh.org") return;
    const message = event.data;
    if (message?.schema_version !== "1.0") return;
    if (message?.type !== "opennosh.embed.resize") return;
    if (!Number.isInteger(message.height) || message.height < 160 || message.height > 1200) return;
    frame.style.height = `${message.height}px`;
  });
</script>
```

The embed derives the target origin from the browser-supplied parent referrer and sends only
`{schema_version: "1.0", type: "opennosh.embed.resize", height}`. The bounded height is an integer
from 160 through 1,200 pixels. The receiver must still verify both `event.source` and the exact
opennosh origin as shown above.

## Privacy and browser policy

Embed responses set no cookies and the runtime uses no local storage, session storage,
fingerprinting, analytics, beacons, credentials, retries, or third-party requests. The Content
Security Policy denies every source by default, then allows only same-origin styles, scripts, images,
and connections plus data images. Base URIs and forms are disabled. `frame-ancestors` is the sole
deployment-configured exception; `X-Frame-Options` is deliberately omitted so the CSP rule governs
embedding. The card remains keyboard usable, respects reduced-motion preferences, and supports
widths from 280 through 1,200 pixels.

## Verification and rollback

From a source checkout, run:

```sh
cd web
npm run test -- tests/embed-contract.test.ts
npx playwright test tests/e2e/embed.spec.ts --config playwright.ui.config.ts
```

Keep `OPENNOSH_EMBED_DISCOVERY_ENABLED=false` in production. To withdraw the preview, leave
discovery disabled, set the compatibility manifest embed status back to `disabled` in a reviewed
patch release, and configure `OPENNOSH_EMBED_FRAME_ANCESTORS='none'`. Rollback does not delete public
records or provenance and does not change publication claims, concurrency, federation, or missions.
