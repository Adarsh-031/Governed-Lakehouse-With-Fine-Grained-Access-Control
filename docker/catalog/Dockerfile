FROM ghcr.io/astral-sh/uv:0.13.1 AS uv
FROM python:3.11.14-slim-bookworm
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_PYTHON_DOWNLOADS=never UV_LINK_MODE=copy PYTHONDONTWRITEBYTECODE=1
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-editable --python /usr/local/bin/python
COPY docker/catalog/*.py ./
COPY ingest ./ingest
COPY audit/__init__.py audit/collector.py audit/normalize.py audit/writer.py ./audit/
COPY policies/version.json ./policies/version.json
COPY scripts/source_data.py ./scripts/source_data.py
USER 10001:10001
ENTRYPOINT ["/app/.venv/bin/python"]
