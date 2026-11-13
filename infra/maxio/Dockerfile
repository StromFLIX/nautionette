ARG MAXIO_VERSION=0.4.2
FROM ghcr.io/coollabsio/maxio:${MAXIO_VERSION}
# Retain the upstream S3 server, entrypoint, data layout and unprivileged user;
# refresh the Debian packages baked into the older published runtime image.
USER root
RUN apt-get update \
    && apt-get upgrade -y --no-install-recommends \
    && rm -rf /var/lib/apt/lists/*
# MaxIO 0.4.2's graceful shutdown listens to ctrl_c (SIGINT), not SIGTERM.
STOPSIGNAL SIGINT
USER maxio:maxio
