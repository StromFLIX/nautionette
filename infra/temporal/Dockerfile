ARG TEMPORAL_VERSION=1.29.0
FROM temporalio/auto-setup:${TEMPORAL_VERSION}

# Same reason as the gateway: carry the config in the image, not on the host.
COPY dynamicconfig/ /etc/temporal/config/dynamicconfig/

HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=10 \
	CMD ["temporal", "--address", "127.0.0.1:7233", "--command-timeout", "4s", "operator", "cluster", "health"]
