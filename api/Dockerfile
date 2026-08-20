FROM ghcr.io/astral-sh/uv:0.11.8 AS uv

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock VERSION ./
RUN uv sync --frozen --no-dev --no-install-project

COPY api ./api
COPY schemas ./schemas
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 opennosh \
    && chown -R opennosh:opennosh /app
USER opennosh

EXPOSE 8000
CMD ["sh", "-c", "alembic -c api/alembic.ini upgrade head && exec uvicorn opennosh_api.main:app --app-dir api --host 0.0.0.0 --port 8000"]
