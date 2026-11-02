#!/bin/sh
set -eu
cp -a /sdk/template /tmp/build
cp -a /sdk/target /tmp/target
printf '%s' "$APP_SOURCE" | base64 -d > /tmp/build/src/app.rs
unset APP_SOURCE
cd /tmp/build
cargo build --frozen --offline --release >&2
printf 'BUILD_READY\n'
# The broker reads the binary while this private tmpfs is still mounted.
sleep 300
