# Agent instructions

## Skill routing

When the user's request matches an available skill, invoke it via the Skill tool. When in doubt,
invoke the skill.

Key routing rules:

- Product ideas or brainstorming: invoke `/office-hours`.
- Strategy or scope: invoke `/plan-ceo-review`.
- Architecture: invoke `/plan-eng-review`.
- Design system or plan review: invoke `/design-consultation` or `/plan-design-review`.
- Full review pipeline: invoke `/autoplan`.
- Bugs or errors: invoke `/investigate`.
- QA or site behavior testing: invoke `/qa` or `/qa-only`.
- Code or diff review: invoke `/review`.
- Visual polish: invoke `/design-review`.
- Shipping, deployment, or pull requests: invoke `/ship` or `/land-and-deploy`.
- Save progress: invoke `/context-save`.
- Resume context: invoke `/context-restore`.
- Author a backlog-ready spec or issue: invoke `/spec`.

## Deploy Configuration (configured by /setup-deploy)

- Platform: Render Blueprint
- Production URL: https://opennosh.org
- Deploy workflow: Render auto-deploy after GitHub checks pass on `main`
- Deploy status command: `curl -fsS https://opennosh.org/api/v1/healthz`
- Merge method: squash
- Project type: web app and private API
- Post-deploy health check: https://opennosh.org/api/v1/healthz

### Custom deploy hooks

- Pre-merge: `make test && make web-e2e && make build`
- Deploy trigger: automatic after `main` checks pass
- Deploy status: Render Blueprint status, then poll the production health endpoint
- Health check: `curl -fsS https://opennosh.org/api/v1/healthz`
