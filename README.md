# opennosh

Self-hosted nutrition and strength tracking built around food data the community can improve.

> **Build status:** The application foundation is underway. Follow the [v1 implementation epic](https://github.com/RujitRaval/opennosh/issues/3) for the dependency-ordered two-month build plan.

The application is MIT-licensed. Community food packs are dedicated under CC0 1.0 with visible contributor credit, and the repository remains private during the build window.

See [`LICENSES.md`](LICENSES.md) for the repository-wide licensing map.

## Quick start

Docker Compose starts PostgreSQL, the FastAPI service, and the Next.js app:

```bash
cp .env.example .env
docker compose up --build
```

Open the web app at <http://localhost:3000>. The API health endpoint is <http://localhost:8000/healthz>; it returns `200` when PostgreSQL is reachable and a safe `503` degraded response when the database is unavailable.

For native development, install Python 3.11+, uv, Node.js 24 LTS (24.15+; Node 25 is unsupported), npm, and Docker, then run:

```bash
make install
make lint typecheck test build compose-config
```

---

## Product documents

| File | Purpose | Who reads it |
|---|---|---|
| `01-RESEARCH.md` | Competitive landscape, the actual gap, why now | You |
| `02-PRD.md` | Product requirements, MVP scope, explicit non-goals | You + `prd-to-github-issues` |
| `03-TRD.md` | Stack, data model, services, API surface | You + `prd-to-github-issues` |
| `04-DATA-LICENSING.md` | **Read this first.** The ODbL constraint that shapes the architecture | You, before any code |
| `docs/foodpack-spec.md` | The contribution unit. The most important file here | Contributors + implementing agent |
| `06-CONTRIBUTOR-MODEL.md` | How the community layer actually works | You |
| `07-LAUNCH-PLAN.md` | Naming, positioning, launch sequencing | You |
| `08-PRODUCT-DECISIONS.md` | Settled product, licensing, scope, and operating decisions | You + implementing agent |
| `CONTRIBUTING.md` | Contribution workflow, boundaries, and validation commands | Contributors |
| `CONVENTIONS.md` | Health-safety, product, and data constraints | Contributors + implementing agent |
| `SECURITY.md` | Private vulnerability-reporting process | Security reporters + maintainers |
| `AUTHORS.md` | Maintainer and contributor credit | Contributors + users |
| `CHANGELOG.md` | Versioned record of shipped changes | Users + maintainers |
| `TODOS.md` | Deferred launch-readiness work | Maintainers |

---

## How to use this

**Do not hand the whole folder to an agent and say "build it."** That produces a 4,000-line PR nobody can review.

The intended path:

1. Read `04-DATA-LICENSING.md` and `08-PRODUCT-DECISIONS.md` before implementation.
2. Treat `02-PRD.md` and `03-TRD.md` as the settled product and technical inputs.
3. Run the issue-generation pipeline against `02-PRD.md` and `03-TRD.md` to produce a dependency-ordered issue queue.
4. Keep `docs/foodpack-spec.md` in the implementation repository; contributors and the validator both depend on it.
5. `01`, `06`, and `07` are retained only while this repository is private planning space. Remove them from the public implementation tree before launch; they are strategy, not build input.

---

## The one-line pitch

> Every calorie tracker locks your data behind a subscription and can't find your dal. This one runs on your hardware, and the food database is a git repo you can send a PR to.

---

## The thing that will kill this project

Not the code. The food database.

Every prior attempt in this category either (a) leaned entirely on a crowd-sourced database with poor non-Western coverage, or (b) built a food table nobody else could contribute to. If food packs aren't trivially easy to write and merge, this becomes another solo-maintained tracker in a category that already has a dozen. `docs/foodpack-spec.md` is the load-bearing document.
