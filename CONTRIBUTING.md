# Contributing to OpenPlate

OpenPlate is currently in specification and foundation work. Discuss feature changes in an issue before writing code. Small documentation corrections and reproducible bug fixes can proceed directly.

## Contributor workflow

1. Update local `main` with `git pull --ff-only origin main`.
2. Create a short, descriptive branch from `main`.
3. Make one coherent development change and add or update its tests.
4. Run `python3 -m unittest discover -s tests -v` and `python3 scripts/check_docs.py`.
5. Open a pull request and complete the template. Merge only after all required checks pass.

Food-pack contributors do not need GStack or an `agent/` branch. Maintainer and agent-authored development uses the stricter workflow in `CLAUDE.md`: `agent/<short-description>` branch, GStack `/review`, then GStack `/ship`. Those maintainer pull-request titles use `vMAJOR.MINOR.PATCH.MICRO type: summary`.

## Scope boundaries

Accepted contribution types will eventually include food packs, exercise definitions, translations, and bug fixes with tests. Until the decisions in `08-OPEN-QUESTIONS.md` are resolved, do not submit bulk food data or assume a final application or dataset license.

Do not submit:

- feature pull requests without a prior issue;
- unrelated refactors or dependency churn;
- credentials, personal health data, or private exports;
- data copied from proprietary nutrition applications or databases;
- generated bulk submissions that a maintainer cannot review quickly.

The intended food-pack limit is 100 entries per pull request. The final contributor license sign-off will be added after the dataset-license decision is recorded.

## Review expectations

Food packs are reviewed weekly when maintainer capacity allows. Code pull requests are reviewed when possible; this project is not the maintainer's job. Clear scope, green checks, and a small diff make a timely review more likely.
