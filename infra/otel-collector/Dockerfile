FROM otel/opentelemetry-collector-contrib:0.164.0
COPY --from=busybox:1.37.0-musl /bin/busybox /usr/local/bin/busybox
COPY config.yaml /etc/otelcol-contrib/config.yaml
