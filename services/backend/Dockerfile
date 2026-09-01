FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

# Workspace root: every member's manifest is needed to resolve the lock.
COPY pyproject.toml uv.lock ./
COPY services/docker-broker/pyproject.toml services/docker-broker/
COPY services/workflow-mcp/pyproject.toml services/workflow-mcp/
COPY services/worker/pyproject.toml services/worker/
COPY libs/ libs/
COPY services/backend/ services/backend/
RUN uv sync --locked --no-dev --package nautionette-backend

# Committed workflows travel with the image and are seeded into the volume.
COPY workflows/ /seed/

# Backend, workflow-mcp and worker share one uid: the shared volume has to be
# writable by all three, and a fresh named volume inherits these permissions.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data /artifacts /workflows \
    && chown -R app:app /app /data /artifacts /workflows
USER app

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--proxy-headers"]
