#!/usr/bin/env bash
# Run from services/frontend so npx uses the lockfile's Playwright version.
# Only dependency installation is retried, never application tests. The calling
# Actions step must set timeout-minutes to bound persistent mirror/DNS outages.
set -euo pipefail

case "${1:-}" in
  install|install-deps) ;;
  *) echo 'Usage: install_playwright_ci.sh {install|install-deps} [Playwright arguments...]' >&2; exit 2 ;;
esac

# Hosted Ubuntu runners prefer azure.archive.ubuntu.com via this mirror list.
# A persistent DNS failure there can stall every package for minutes, even with
# fallback entries present. Keep the runner's signed Ubuntu sources and suites,
# but use the official HTTPS mirrors directly. Never change a developer's host.
mirror_list="${APT_MIRROR_LIST:-/etc/apt/apt-mirrors.txt}"
if [[ "${GITHUB_ACTIONS:-false}" == true && -f "$mirror_list" ]]; then
  printf '%s\n' 'https://archive.ubuntu.com/ubuntu/' 'https://security.ubuntu.com/ubuntu/' |
    sudo tee "$mirror_list" >/dev/null
fi

for attempt in 1 2 3; do
  if npx --no-install playwright "$@"; then
    exit 0
  else
    status=$?
  fi
  if (( attempt == 3 )); then
    echo "Playwright dependency installation failed after ${attempt} attempts." >&2
    exit "$status"
  fi
  echo "Playwright dependency installation failed; retrying (attempt $((attempt + 1))/3)." >&2
  sleep "$((attempt * 15))"
done
