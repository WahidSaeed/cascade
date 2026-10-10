# One image for the API, the agent worker, the ingest worker and crm-mock.
# Build from the repository root:  docker build -f backend/Dockerfile -t cascade .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    UV_PROJECT_ENVIRONMENT=/opt/venv PATH=/opt/venv/bin:$PATH \
    CASCADE_MODELS_DIR=/models HF_HUB_OFFLINE=0
COPY --from=ghcr.io/astral-sh/uv:0.5.11 /uv /usr/local/bin/uv

WORKDIR /app
COPY backend/pyproject.toml backend/uv.lock ./
# The dl group (torch) is only needed by the agent worker's local trainer (build with WITH_DL=1).
ARG WITH_DL=0
RUN if [ "$WITH_DL" = "1" ]; then uv sync --frozen --no-dev --no-install-project; \
    else uv sync --frozen --no-dev --no-group dl --no-install-project; fi

COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY demo /demo
ENV CASCADE_DEMO_DIR=/demo

# Bake the light-layer models into the image: no download at cold start (BUILD.md §4.1).
RUN python -m app.models_download && chmod -R a+rX /models
ENV HF_HUB_OFFLINE=1

EXPOSE 8080
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
