ARG AGENTGATEWAY_VERSION=v1.5.0
FROM cr.agentgateway.dev/agentgateway:${AGENTGATEWAY_VERSION}

COPY --from=busybox:1.37.0-musl /bin/busybox /usr/local/bin/busybox

# The config is baked in rather than bind-mounted: the stack then carries its own
# configuration and does not depend on a path existing on the host.
COPY config/config.yaml /config/config.yaml

# A fresh named volume inherits this directory's ownership. The upstream image
# runs as uid 65532 and needs to create its SQLite database here.
COPY --chown=65532:65532 data/ /data/

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=5 \
	CMD ["/usr/local/bin/busybox", "wget", "-q", "-T", "4", "-O", "/dev/null", "http://127.0.0.1:15021/healthz/ready"]
