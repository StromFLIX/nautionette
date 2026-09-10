ARG AGENTGATEWAY_VERSION=v1.5.0
FROM cr.agentgateway.dev/agentgateway:${AGENTGATEWAY_VERSION} AS gateway

# The gateway starts stdio targets inside this container. Trixie matches the
# upstream binary's glibc build baseline; Node and Python cover npx and uvx MCPs.
FROM node:24-trixie-slim
COPY --from=ghcr.io/astral-sh/uv:0.12.10 /uv /uvx /usr/local/bin/
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates python3 \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 65532 gateway \
    && useradd --uid 65532 --gid 65532 --home-dir /data/mcp --no-create-home --shell /usr/sbin/nologin gateway \
    && mkdir -p /data/mcp \
    && chown -R 65532:65532 /data

COPY --from=gateway /app/agentgateway /app/agentgateway
COPY --from=busybox:1.37.0-musl /bin/busybox /usr/local/bin/busybox
COPY config/config.yaml /config/config.yaml
COPY --chown=65532:65532 data/ /data/

ENV AGENTGATEWAY_ENV=container HOME=/data/mcp
WORKDIR /data
USER 65532:65532
# Fail the build if the upstream binary or either package runtime cannot run.
RUN /app/agentgateway --version && node --version && npm --version && uvx --version && python3 --version
ENTRYPOINT ["/app/agentgateway"]
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
    CMD ["/usr/local/bin/busybox", "wget", "-q", "-T", "4", "-O", "/dev/null", "http://127.0.0.1:15021/healthz/ready"]
