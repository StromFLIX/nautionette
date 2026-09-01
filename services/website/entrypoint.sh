#!/bin/sh
# The site is static; the one thing it needs from the deployment is where the
# app lives, so the "Open the app" buttons point somewhere real.
set -e
printf '%s' "${APP_URL:-}" > /usr/share/nginx/html/app-url.txt
