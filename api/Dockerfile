FROM ghcr.io/astral-sh/uv:0.11.8 AS uv

FROM python:3.11-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock VERSION ./
COPY README.md AUTHORS.md ./
COPY LICENSE LICENSES.md NOTICE.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY api ./api
COPY schemas ./schemas
COPY config ./config
COPY deploy/render_runtime.py ./deploy/render_runtime.py
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 opennosh \
    && chown -R opennosh:opennosh /app
USER opennosh

EXPOSE 8000
# Search inputs are private enough that raw query strings must not enter access logs.
CMD ["opennosh-web"]
