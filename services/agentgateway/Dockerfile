ARG AGENTGATEWAY_VERSION=v1.5.0
FROM cr.agentgateway.dev/agentgateway:${AGENTGATEWAY_VERSION}

# The config is baked in rather than bind-mounted: the stack then carries its own
# configuration and does not depend on a path existing on the host.
COPY config/config.yaml /config/config.yaml
