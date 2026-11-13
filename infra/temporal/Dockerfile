# The maintained image line. Deployments run the guarded migration before startup.
ARG TEMPORAL_VERSION=1.32.2
FROM temporalio/admin-tools:${TEMPORAL_VERSION} AS tools
FROM temporalio/server:${TEMPORAL_VERSION}
USER root
RUN apk upgrade --no-cache
# The server image no longer includes the CLI used by our private health probe.
COPY --from=tools /usr/local/bin/temporal /usr/local/bin/temporal
COPY dynamicconfig/ /etc/temporal/config/dynamicconfig/
COPY --chmod=755 start.sh /etc/temporal/maintenance-start.sh
USER temporal
ENTRYPOINT ["/etc/temporal/maintenance-start.sh"]
CMD ["/etc/temporal/entrypoint.sh"]
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=10 \
    CMD ["temporal", "--address", "127.0.0.1:7233", "--command-timeout", "4s", "operator", "cluster", "health"]
