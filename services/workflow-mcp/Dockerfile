FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1
WORKDIR /app

RUN useradd --create-home --uid 10004 mcp
COPY --chown=mcp:mcp . /app
USER mcp

# Exposes the deterministic workflow authoring tools over MCP. agentgateway
# fronts this as a target; nothing calls it directly.
EXPOSE 8000
CMD ["python", "-c", "import time; print('workflow-mcp: not implemented yet', flush=True); time.sleep(86400)"]
