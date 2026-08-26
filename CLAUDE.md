# opennosh agent instructions

## Development workflow

- Treat `main` as protected. Never implement directly on it.
- Start every development step from current `main` on an `agent/<description>` branch.
- Keep each branch to one coherent outcome and open a pull request back to `main`.
- Run the repository checks before review.
- Run GStack `/review` when implementation is complete, resolve all blocking findings, then run GStack `/ship` to commit, push, and create or update the pull request.
- Do not merge a pull request with failing required checks.
- Treat `08-PRODUCT-DECISIONS.md` as settled product scope. Do not silently reopen or contradict those decisions.

## Testing

Run the canonical repository gates:

```bash
make install
make lint
make typecheck
make test
make web-e2e
make build
make compose-config
make package-check
make contracts-check
make trust-gates-check
npm --prefix web run test:coverage
```

Use `docs/testing.md` for PostgreSQL integration setup, changed-line API coverage reproduction,
release confidence, scheduled drills, and main-branch protection checks.

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill.

Key routing rules:
- Product ideas/brainstorming → invoke `/office-hours`
- Strategy/scope → invoke `/plan-ceo-review`
- Architecture → invoke `/plan-eng-review`
- Design system/plan review → invoke `/design-consultation` or `/plan-design-review`
- Full review pipeline → invoke `/autoplan`
- Bugs/errors → invoke `/investigate`
- QA/testing site behavior → invoke `/qa` or `/qa-only`
- Code review/diff check → invoke `/review`
- Visual polish → invoke `/design-review`
- Ship/deploy/PR → invoke `/ship` or `/land-and-deploy`
- Save progress → invoke `/context-save`
- Resume context → invoke `/context-restore`
- Author a backlog-ready spec/issue → invoke `/spec`
