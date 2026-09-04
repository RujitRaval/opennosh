.PHONY: install lint typecheck test package-check contracts-generate contracts-check developer-compatibility-check impact-metrics-check benchmark-contract-check database-capacity-check forge-policy-check trust-gates-check trust-branch-protection-check trust-branch-protection-apply design-system-check font-performance-check benchmark-corpus benchmark-run benchmark-extraction motion-performance-check visual-regression-check web-e2e web-e2e-ui web-e2e-vertical acceptance-config acceptance-up acceptance-down acceptance-ps acceptance-logs acceptance-copy-fixture build compose-config db-upgrade db-downgrade usda-import wger-import foodpack-validate

ACCEPTANCE_PATH_HASH ?= $(shell pwd | cksum | cut -d " " -f 1)
ACCEPTANCE_PROJECT ?= opennosh-acceptance-$(ACCEPTANCE_PATH_HASH)
ACCEPTANCE_PORT_OFFSET ?= $(shell expr $(ACCEPTANCE_PATH_HASH) % 1000)
ACCEPTANCE_WEB_PORT ?= $(shell expr 3100 + $(ACCEPTANCE_PORT_OFFSET))
ACCEPTANCE_ARTIFACT_PORT ?= $(shell expr 4100 + $(ACCEPTANCE_PORT_OFFSET))
ACCEPTANCE_COMPOSE = ACCEPTANCE_WEB_PORT=$(ACCEPTANCE_WEB_PORT) ACCEPTANCE_ARTIFACT_PORT=$(ACCEPTANCE_ARTIFACT_PORT) docker compose --project-name $(ACCEPTANCE_PROJECT) -f compose.yaml -f compose.acceptance.yaml

install:
	uv sync --frozen
	npm --prefix web ci

lint:
	uv run ruff check api benchmarks deploy/render_runtime.py scripts/build_federation_failure_drill_report.py scripts/build_public_fonts.py scripts/check_benchmark_contract.py scripts/check_database_capacity.py scripts/check_developer_compatibility.py scripts/check_developer_starters.py scripts/check_developer_trials.py scripts/check_changed_coverage.py scripts/check_impact_metrics.py scripts/check_trust_gates.py scripts/configure_trust_branch_protection.py tests/test_benchmark*.py tests/test_database_capacity_deployment.py tests/test_developer_compatibility.py tests/test_developer_trials.py tests/test_render_deployment.py tests/test_trust_gates.py api/tests/test_benchmark*.py
	sh -n deploy/render_web_start.sh
	npm --prefix web run lint

typecheck:
	uv run mypy
	uv run mypy --strict benchmarks/performance
	uv run mypy --strict deploy/render_runtime.py
	npm --prefix web run typecheck

test: contracts-check developer-compatibility-check impact-metrics-check foodpack-validate benchmark-contract-check database-capacity-check forge-policy-check trust-gates-check design-system-check
	PYTHONPATH=api:. uv run python scripts/check_developer_trials.py
	PYTHONPATH=api:. uv run pytest
	PYTHONPATH=api:. uv run python -m unittest discover -s tests -v
	python3 scripts/check_docs.py
	npm --prefix web test

package-check:
	python3 scripts/check_packages.py
	uv build --out-dir dist
	python3 scripts/check_python_distribution.py dist
	uv run python scripts/check_installed_python_distribution.py dist/opennosh-$$(cat VERSION)-py3-none-any.whl
	npm --prefix packages/npm ci --ignore-scripts
	npm --prefix packages/npm test
	cd packages/npm && npm pack --pack-destination ../../dist
	NPM_PACKAGE_VERSION=$$(cut -d. -f1-3 VERSION); uv run python scripts/check_developer_starters.py --npm-tarball "dist/opennosh-$${NPM_PACKAGE_VERSION}.tgz" --wheel "dist/opennosh-$$(cat VERSION)-py3-none-any.whl"

contracts-generate:
	PYTHONPATH=api uv run python scripts/export_openapi.py
	npm --prefix web run generate:api-contracts
	uv run python scripts/generate_python_sdk_contracts.py
	uv run ruff format api/opennosh_api/sdk/_generated.py

contracts-check:
	$(MAKE) contracts-generate
	git diff --exit-code -- web/lib/generated api/opennosh_api/sdk/_generated.py
	python3 scripts/check_generated_imports.py
	python3 scripts/check_openapi_compatibility.py

developer-compatibility-check:
	PYTHONPATH=api:. uv run python scripts/check_developer_compatibility.py

impact-metrics-check:
	PYTHONPATH=api:. uv run python scripts/check_impact_metrics.py

benchmark-contract-check:
	PYTHONPATH=api:. uv run python scripts/check_benchmark_contract.py

database-capacity-check:
	PYTHONPATH=api:. uv run python scripts/check_database_capacity.py

forge-policy-check:
	PYTHONPATH=api:. uv run python scripts/check_forge_policy.py

trust-gates-check:
	PYTHONPATH=api:. uv run python scripts/check_trust_gates.py validate

trust-branch-protection-check:
	PYTHONPATH=api:. uv run python scripts/configure_trust_branch_protection.py --check

trust-branch-protection-apply:
	PYTHONPATH=api:. uv run python scripts/configure_trust_branch_protection.py --apply

design-system-check:
	npm --prefix web run check:design-system

benchmark-corpus:
	PYTHONPATH=api:. uv run python -m benchmarks.performance.corpus --profile $${PROFILE:-launch-reference} --output $${OUTPUT:--}

font-performance-check:
	uv run --frozen python scripts/build_public_fonts.py
	npm --prefix web run check:font-budgets

benchmark-run:
	PYTHONPATH=api:. uv run python -m benchmarks.performance.harness $${BENCHMARK_ARGS}

benchmark-extraction:
	PYTHONPATH=api:. uv run python -m benchmarks.performance.extraction $${ARTIFACT_DIRS}

motion-performance-check: build
	npm --prefix web run check:motion-budgets
	npm --prefix web run benchmark:motion -- --output test-results/motion-performance.json

visual-regression-check:
	npm --prefix web run check:visual-baselines
	npm --prefix web run test:visual

web-e2e:
	npm --prefix web run test:e2e:ui

web-e2e-ui:
	npm --prefix web run test:e2e:ui

web-e2e-vertical:
	npm --prefix web run check:acceptance-boundaries
	VERTICAL_BASE_URL=http://127.0.0.1:$(ACCEPTANCE_WEB_PORT) VERTICAL_ARTIFACT_ORIGIN_URL=http://127.0.0.1:$(ACCEPTANCE_ARTIFACT_PORT) npm --prefix web run test:e2e:vertical

acceptance-config:
	$(ACCEPTANCE_COMPOSE) config --quiet

acceptance-up:
	$(ACCEPTANCE_COMPOSE) up --build --wait db acceptance-bootstrap publication-worker evidence-worker acceptance-fixture artifact-origin api web

acceptance-down:
	$(ACCEPTANCE_COMPOSE) down --volumes --remove-orphans

acceptance-ps:
	$(ACCEPTANCE_COMPOSE) ps --all --no-trunc

acceptance-logs:
	$(ACCEPTANCE_COMPOSE) logs --no-color

acceptance-copy-fixture:
	$(ACCEPTANCE_COMPOSE) cp api:/app/public-artifact-state/fixture.json web/test-results/vertical-acceptance/fixture.json

build:
	npm --prefix web run build

compose-config:
	docker compose config --quiet

db-upgrade:
	uv run alembic -c api/alembic.ini upgrade head

db-downgrade:
	uv run alembic -c api/alembic.ini downgrade base

usda-import:
	PYTHONPATH=api uv run python -m opennosh_api.importers.usda $(USDA_PATHS)

wger-import:
	PYTHONPATH=api uv run opennosh exercises import-wger $(WGER_PATHS)

foodpack-validate:
	PYTHONPATH=api uv run python -m opennosh_api.foodpacks.validation packs --json
