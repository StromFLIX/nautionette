ARG BASE_IMAGE=nautionette/pi-base:dev
FROM ${BASE_IMAGE}

# One agent set for now. A second set is a second directory like this one:
# same base, different extensions and packages.
COPY extensions/ /root/.pi/agent/extensions/

# The system prompt an agent set brings with it.
COPY AGENTS.md /workspace-defaults/AGENTS.md
