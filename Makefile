.PHONY: install lint typecheck test build compose-config db-upgrade db-downgrade usda-import

install:
	uv sync --frozen
	npm --prefix web ci

lint:
	uv run ruff check api
	npm --prefix web run lint

typecheck:
	uv run mypy
	npm --prefix web run typecheck

test:
	uv run pytest
	python3 -m unittest discover -s tests -v
	python3 scripts/check_docs.py
	npm --prefix web test

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
