# Build-only toolchain; never runs application services. Cargo/build scripts have
# no network, credentials, Docker socket or persistent writable mounts at publish.
FROM rust:1.100.0-alpine3.22
RUN apk upgrade --no-cache && apk add --no-cache musl-dev gcc && mkdir -p /sdk /tmp/build
COPY Cargo.toml Cargo.lock /tmp/build/
COPY src /tmp/build/src
ENV CARGO_TARGET_DIR=/tmp/target CARGO_HOME=/usr/local/cargo
WORKDIR /tmp/build
RUN mkdir -p .cargo && cargo vendor --locked /sdk/vendor > .cargo/config.toml && \
    cargo test --frozen --offline && cargo build --frozen --offline --release && \
    rm -rf /tmp/target/debug && \
    mv /tmp/build /sdk/template && mv /tmp/target /sdk/target && \
    chmod -R a+rX /sdk && rm -rf /usr/local/cargo/registry
COPY build.sh /build.sh
USER 65532:65532
WORKDIR /tmp
ENTRYPOINT ["sh", "/build.sh"]
