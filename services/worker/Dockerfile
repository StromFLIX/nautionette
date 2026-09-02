FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY services/backend/pyproject.toml services/backend/
COPY services/docker-broker/pyproject.toml services/docker-broker/
COPY services/workflow-mcp/pyproject.toml services/workflow-mcp/
COPY libs/ libs/
COPY services/worker/ services/worker/
RUN uv sync --locked --no-dev --package nautionette-worker

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /workflows /artifacts \
    && chown -R app:app /app /workflows /artifacts
USER app

# Workflow definitions come from the shared volume at /workflows.
STOPSIGNAL SIGTERM
CMD ["python", "-m", "nautionette_worker.main"]
