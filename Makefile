.PHONY: install lint typecheck test package-check contracts-generate contracts-check benchmark-contract-check benchmark-corpus benchmark-run benchmark-extraction web-e2e build compose-config db-upgrade db-downgrade usda-import wger-import foodpack-validate

install:
	uv sync --frozen
	npm --prefix web ci

lint:
	uv run ruff check api benchmarks scripts/check_benchmark_contract.py tests/test_benchmark*.py api/tests/test_benchmark*.py
	npm --prefix web run lint

typecheck:
	uv run mypy
	uv run mypy --strict benchmarks/performance
	npm --prefix web run typecheck

test: foodpack-validate benchmark-contract-check
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
	cd packages/npm && npm pack --dry-run

contracts-generate:
	PYTHONPATH=api uv run python scripts/export_openapi.py
	npm --prefix web run generate:api-contracts

contracts-check:
	$(MAKE) contracts-generate
	git diff --exit-code -- web/lib/generated
	python3 scripts/check_generated_imports.py
	python3 scripts/check_openapi_compatibility.py

benchmark-contract-check:
	PYTHONPATH=api:. uv run python scripts/check_benchmark_contract.py

benchmark-corpus:
	PYTHONPATH=api:. uv run python -m benchmarks.performance.corpus --profile $${PROFILE:-launch-reference} --output $${OUTPUT:--}

benchmark-run:
	PYTHONPATH=api:. uv run python -m benchmarks.performance.harness $${BENCHMARK_ARGS}

benchmark-extraction:
	PYTHONPATH=api:. uv run python -m benchmarks.performance.extraction $${ARTIFACT_DIRS}

web-e2e:
	npm --prefix web run test:e2e

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
