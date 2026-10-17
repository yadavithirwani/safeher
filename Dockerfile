# SafeHer API for Cloud Run. Build context: the repository root (see .gcloudignore).
# The reference database (data/safeher.duckdb, ~145 MB, read-only) is baked into the image: no database
# server to pay for. Rebuild the data locally (python -m safeher.data build) and redeploy to update it.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

# Dependencies first, so code-only changes reuse this layer.
COPY backend/pyproject.toml backend/pyproject.toml
RUN mkdir -p backend/safeher && touch backend/safeher/__init__.py \
 && pip install -e ./backend \
 && rm -rf backend/safeher

COPY config ./config
COPY app/web ./app/web
COPY backend/safeher ./backend/safeher
COPY data/safeher.duckdb ./data/safeher.duckdb

WORKDIR /app/backend
# Cloud Run sets PORT. One worker: a 1 GiB instance holds one process (~600 MB at peak).
CMD exec uvicorn safeher.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1 --no-access-log --proxy-headers --forwarded-allow-ips='*'
