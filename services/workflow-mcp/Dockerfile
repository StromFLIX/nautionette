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
COPY services/worker/pyproject.toml services/worker/
COPY libs/ libs/
COPY services/workflow-mcp/ services/workflow-mcp/
RUN uv sync --locked --no-dev --package nautionette-workflow-mcp

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /workflows \
    && chown -R app:app /app /workflows
USER app

# agentgateway fronts this as an MCP target; the backend uses the REST side.
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=5 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/healthz').read()"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
