.PHONY: install lint typecheck test build compose-config

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
