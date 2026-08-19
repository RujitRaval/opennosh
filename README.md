# OpenPlate — spec bundle

Working name. **Check availability before committing** (npm, PyPI, GitHub org, domain). See `07-LAUNCH-PLAN.md` for naming alternatives.

An MIT-licensed, self-hosted nutrition and training tracker whose differentiator is a **community-contributed food database**, structured so that one pull request equals one food pack.

---

## What's in here

| File | Purpose | Who reads it |
|---|---|---|
| `01-RESEARCH.md` | Competitive landscape, the actual gap, why now | You |
| `02-PRD.md` | Product requirements, MVP scope, explicit non-goals | You + `prd-to-github-issues` |
| `03-TRD.md` | Stack, data model, services, API surface | You + `prd-to-github-issues` |
| `04-DATA-LICENSING.md` | **Read this first.** The ODbL constraint that shapes the architecture | You, before any code |
| `05-FOODPACK-SPEC.md` | The contribution unit. The most important file here | Contributors + implementing agent |
| `06-CONTRIBUTOR-MODEL.md` | How the community layer actually works | You |
| `07-LAUNCH-PLAN.md` | Naming, positioning, launch sequencing | You |
| `08-OPEN-QUESTIONS.md` | Decisions only you can make, blocking the build | You |

---

## How to use this

**Do not hand the whole folder to an agent and say "build it."** That produces a 4,000-line PR nobody can review.

The intended path:

1. Read `04-DATA-LICENSING.md` and `08-OPEN-QUESTIONS.md` yourself. Both contain forks that no agent should pick for you.
2. Resolve the blocking questions in `08`. Edit `02-PRD.md` and `03-TRD.md` to reflect your answers.
3. Run your `prd-to-github-issues` skill against `02-PRD.md` and `03-TRD.md`. It expects exactly these two documents and will produce a dependency-ordered issue queue.
4. `05-FOODPACK-SPEC.md` should be committed into the repo itself at `docs/foodpack-spec.md` — contributors need it, and so does the validation issue.
5. `01`, `06`, and `07` stay out of the repo. They're strategy, not build input.

---

## The one-line pitch

> Every calorie tracker locks your data behind a subscription and can't find your dal. This one is MIT, runs on your hardware, and the food database is a git repo you can send a PR to.

---

## The thing that will kill this project

Not the code. The food database.

Every prior attempt in this category either (a) leaned entirely on a crowd-sourced database with poor non-Western coverage, or (b) built a food table nobody else could contribute to. If food packs aren't trivially easy to write and merge, this becomes another solo-maintained tracker in a category that already has a dozen. `05-FOODPACK-SPEC.md` is the load-bearing document.
