# "Plain Docker" pattern from https://pi.dev/docs/latest/containerization
FROM node:24-bookworm-slim

# Tooling shared by every agent set. Keep Python and browser installations outside
# /root: the broker runs project agents as an unprivileged user with HOME=/workspace.
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /usr/local/bin/
ENV UV_PYTHON_INSTALL_DIR=/opt/python \
    UV_PYTHON_BIN_DIR=/usr/local/bin \
    PLAYWRIGHT_BROWSERS_PATH=/opt/playwright-browsers

RUN apt-get update \
  && apt-get install -y --no-install-recommends \
      bash ca-certificates curl git ripgrep build-essential pkg-config \
  && rm -rf /var/lib/apt/lists/*

# The workspace targets Python 3.13. --default also exposes python and python3.
RUN uv python install --default 3.13 \
  && uv cache clean

# Match services/frontend/package-lock.json. Preinstall both Chromium and its OS
# libraries/fonts so browser tests need no privileged apt install at runtime.
ARG PLAYWRIGHT_VERSION=1.63.0
RUN npm install -g --ignore-scripts playwright@${PLAYWRIGHT_VERSION} \
  && playwright install --with-deps chromium \
  && chmod -R a+rX /opt/python /opt/playwright-browsers \
  && npm cache clean --force \
  && rm -rf /var/lib/apt/lists/*

# Pi updates must not invalidate the much larger Chromium/OS dependency layer.
RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.84.4 \
  && npm cache clean --force

# Model traffic goes to agentgateway's OpenAI-compatible endpoint, so no
# provider key ever enters this container.
ENV PI_OFFLINE=1 \
    PI_SKIP_VERSION_CHECK=1 \
    PI_TELEMETRY=0 \
    PI_CODING_AGENT_DIR=/root/.pi/agent

# The whole contract of a run: AGENT_JOB in, NDJSON out, then the container exits.
COPY agent-run.mjs /usr/local/lib/nautionette/agent-run.mjs
COPY project-git.mjs /usr/local/lib/nautionette/project-git.mjs
COPY context-usage.mjs /usr/local/lib/nautionette/context-usage.mjs
COPY chat-control.mjs /usr/local/lib/nautionette/chat-control.mjs
COPY internet.ts /root/.pi/agent/extensions/nautionette-internet.ts
RUN printf '#!/bin/sh\nexec node /usr/local/lib/nautionette/agent-run.mjs "$@"\n' \
      > /usr/local/bin/agent-run \
  && chmod +x /usr/local/bin/agent-run
RUN chmod 755 /root

# Agent sets extend this image: extensions under /root/.pi/agent/extensions,
# auto-discovered by pi, plus whatever packages that set needs.
WORKDIR /workspace
ENTRYPOINT ["agent-run"]
