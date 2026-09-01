ARG TEMPORAL_VERSION=1.29.0
FROM temporalio/auto-setup:${TEMPORAL_VERSION}

# Same reason as the gateway: carry the config in the image, not on the host.
COPY dynamicconfig/ /etc/temporal/config/dynamicconfig/
