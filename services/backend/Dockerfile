FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1 \
    SEARCH_MODEL_CACHE=/opt/nautionette-search-model \
    SEARCH_MODEL_LOCAL_ONLY=true \
    HF_HUB_DISABLE_TELEMETRY=1 \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends git ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Workspace root: every member's manifest is needed to resolve the lock.
COPY pyproject.toml uv.lock ./
COPY services/docker-broker/pyproject.toml services/docker-broker/
COPY services/workflow-mcp/pyproject.toml services/workflow-mcp/
COPY services/worker/pyproject.toml services/worker/
COPY libs/ libs/
COPY images/pi-base/artcraft/catalog.json /etc/nautionette/artcraft-catalog.json
COPY services/backend/ services/backend/
RUN uv sync --locked --no-dev --package nautionette-backend
# Bake the ~67 MB CPU model into the image; no first-start download or API key.
RUN python -c "from nautionette_backend.search_model import load_encoder; load_encoder('/opt/nautionette-search-model')" \
    && chmod -R a+rX /opt/nautionette-search-model

# Committed workflows travel with the image and are seeded into the volume.
COPY workflows/ /seed/

# Backend, workflow-mcp and worker share one uid: the shared volume has to be
# writable by all three, and a fresh named volume inherits these permissions.
RUN useradd --create-home --uid 10001 app \
    && mkdir -p /data /artifacts /workflows /projects /chat-sessions \
    && chown -R app:app /app /data /artifacts /workflows /projects /chat-sessions \
    && chmod 700 /chat-sessions
USER app
# Verify every cached artifact is usable offline by the actual runtime user.
# Hub tree-cache files can otherwise retain root-only permissions from download.
RUN python -c "from nautionette_backend.search_model import load_encoder; encoder = load_encoder('/opt/nautionette-search-model', local_files_only=True); assert len(next(iter(encoder.query_embed('offline model check')))) == 384"

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8080/healthz').read()"
CMD ["python", "-m", "nautionette_backend.server"]
