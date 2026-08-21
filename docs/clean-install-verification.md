# Independent clean-install verification

opennosh's documented Docker Compose path was verified on 2026-08-21 from a clean public clone on a
separate machine. This record contains no credentials, private addresses, or copied local configuration.

## Verified source and host

- Repository: `https://github.com/RujitRaval/opennosh.git`
- Branch: `main`
- Commit: `b7686700eefcea4de625c633c879d51ed676f3a7`
- Host: independent x86_64 laptop running Ubuntu 24.04.4 LTS
- Docker Engine: 29.6.1
- Docker Compose: 5.3.1
- Capacity at start: 4 CPUs, 15 GiB memory, and 82 GiB free disk

The host already ran unrelated containers. opennosh used a unique temporary clone and Compose project;
the existing services and their ports were not changed.

## Procedure

The verification used the public setup path without copying a developer `.env` or any private data:

```bash
git clone --branch main --single-branch https://github.com/RujitRaval/opennosh.git
cd opennosh
cp .env.example .env
docker compose config --quiet
docker compose up --build -d
```

The checked-out commit matched `origin/main`, and the clone was clean before `.env` was created.

## Results

- The PostgreSQL, API, web, and ingress services built and started successfully.
- PostgreSQL, API, and web health checks reported healthy; ingress served the application on port 3000.
- `GET /healthz` returned `200` with a connected database.
- `/`, `/notices`, and `/trends` returned `200` from the host.
- The web application also returned `200` from a second laptop over the private LAN.
- Alembic reported both the current revision and repository head as `20260820_0010`.
- Bounded service logs contained no unexpected startup or runtime failures.

GStack browser QA then verified the real user path:

1. Open the sign-in page and create a disposable account.
2. Create a private custom food with a household portion.
3. Add that portion to the daily nutrition log and confirm the calculated totals.
4. Open Trends, change the date range and nutrition measure, and confirm the recorded point.
5. Open the licenses and data notices page.
6. Verify the daily log at a 375 by 812 mobile viewport.

## Restart and persistence

The full Compose project was restarted. All services returned to their healthy states, `GET /healthz`
again returned `200`, and Alembic remained at `20260820_0010`. The same browser session remained
authenticated, and the private custom food, daily log entry, and calculated totals were still present.
This confirms that the documented named PostgreSQL volume preserves application data across a routine
Compose restart.

## Non-blocking observation

An account with no configured nutrition target correctly sees the neutral “No target set” state. The
underlying target-resolution request returns the API's expected `404`, which Chromium records as a
console error. This does not break the page or the clean-install workflow. A P3 web-quality follow-up is
recorded in `TODOS.md` so normal empty states do not obscure real browser errors.

## Cleanup state

After verification, the temporary opennosh containers and networks were stopped without deleting the
named PostgreSQL volume or the clean clone. The evidence can therefore be reproduced or inspected on the
test host without affecting its pre-existing services.
