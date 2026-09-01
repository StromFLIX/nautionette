# "Plain Docker" pattern from https://pi.dev/docs/latest/containerization
FROM node:24-bookworm-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends bash ca-certificates git ripgrep \
  && rm -rf /var/lib/apt/lists/*

RUN npm install -g --ignore-scripts @earendil-works/pi-coding-agent

# Model traffic goes to agentgateway's OpenAI-compatible endpoint, so no
# provider key ever enters this container.

# Agent sets extend this image: extensions under /root/.pi/agent/extensions,
# loaded with `pi -e <path>`, plus whatever packages that set needs.
WORKDIR /workspace
ENTRYPOINT ["pi"]
