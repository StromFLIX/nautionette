#!/bin/sh
set -eu
# Copy as the publisher UID; never preserve root ownership from the image.
cp -R /sdk/template /tmp/build
cp -R /sdk/target /tmp/target
export CARGO_HOME=/tmp/cargo
printf '%s' "$APP_SOURCE" | base64 -d > /tmp/build/src/app.rs
unset APP_SOURCE
cd /tmp/build
cargo build --frozen --offline --release >&2
printf 'BUILD_READY\n'
# The broker reads the binary while this private tmpfs is still mounted.
sleep 300
