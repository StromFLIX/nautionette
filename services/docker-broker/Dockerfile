FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

COPY pyproject.toml uv.lock ./
COPY libs/nautionette/pyproject.toml libs/nautionette/
COPY services/backend/pyproject.toml services/backend/
COPY services/workflow-mcp/pyproject.toml services/workflow-mcp/
COPY services/worker/pyproject.toml services/worker/
COPY services/docker-broker/ services/docker-broker/
RUN uv sync --locked --no-dev --package nautionette-docker-broker

# The broker carries the agent build contexts, so it builds the Pi images once
# at startup and never during a call.
COPY images/ /agent-images/

# Runs as root: it needs the Docker socket. The API surface stays fixed instead.
EXPOSE 9100
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=5 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:9100/healthz').read()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "9100"]
