# "Plain Docker" pattern from https://pi.dev/docs/latest/containerization
FROM node:24-bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends bash ca-certificates git ripgrep \
  && rm -rf /var/lib/apt/lists/*

RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent@0.84.4

# Model traffic goes to agentgateway's OpenAI-compatible endpoint, so no
# provider key ever enters this container.
ENV PI_OFFLINE=1 \
    PI_SKIP_VERSION_CHECK=1 \
    PI_TELEMETRY=0 \
    PI_CODING_AGENT_DIR=/root/.pi/agent

# The whole contract of a run: AGENT_JOB in, NDJSON out, then the container exits.
COPY agent-run.mjs /usr/local/lib/nautionette/agent-run.mjs
RUN printf '#!/bin/sh\nexec node /usr/local/lib/nautionette/agent-run.mjs "$@"\n' \
      > /usr/local/bin/agent-run \
  && chmod +x /usr/local/bin/agent-run

# Agent sets extend this image: extensions under /root/.pi/agent/extensions,
# auto-discovered by pi, plus whatever packages that set needs.
WORKDIR /workspace
ENTRYPOINT ["agent-run"]
