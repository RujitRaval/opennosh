# opennosh on npm

This preview package provides a dependency-free JavaScript client for anonymous opennosh reads and
preserves the existing `opennosh` bootstrap command.

```js
import { OpenNoshClient } from "opennosh";

const client = new OpenNoshClient("hosted");
const search = await client.searchFoods({ q: "lentils", limit: 10 });
const first = search.data.items.find(({ source }) => source === "usda" || source === "community");
if (!first) throw new Error("No public food record found");
const food = await client.getPublicFood({
  source: first.source,
  sourceId: first.source_id,
});

console.log(food.data.record.attribution, food.data.release.state);
```

Use an HTTPS origin for a self-hosted server, for example
`new OpenNoshClient("https://nosh.example")`. Plain HTTP is accepted only for exact loopback hosts.
The client sends no cookies, ambient credentials, telemetry, referrer override, or retry. It refuses
redirects, enforces per-route size limits and deadlines, and returns cache validators beside the
typed response as `etag`, `last_modified`, and `cache_control`.

The public methods are `capabilities`, `searchFoods`, `getCommonsSnapshot`, `getPublicFood`,
`listMissions`, `getMissionActivity`, `getReleaseFood`, `getProvenance`, `getReleaseManifest`, and
`downloadPack`. Callers may lower a deadline with `timeoutMs` or pass an `AbortSignal`; they cannot
raise the 10-second read or 30-second download maximum. Errors are `OpenNoshProblem` instances with
stable status, code, request reference, recovery actions, and retry guidance fields.

The package remains preview software under the compatibility manifest. It does not claim external
adoption or general availability.

## Bootstrap a self-hosted checkout

```sh
npx opennosh init my-opennosh
cd my-opennosh
```

The command clones the public repository without overwriting an existing path. It does not install
Docker, change global configuration, collect telemetry, or run services automatically. Continue
with the repository README to review the configuration and start the application.

The application and this bootstrapper are MIT-licensed. Dataset and contributor terms remain
separate; see the repository's `NOTICE.md` and `LICENSES.md`.
