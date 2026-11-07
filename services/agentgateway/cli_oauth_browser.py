#!/usr/bin/env python3
"""BROWSER/xdg-open hook for a trusted CLI login. Never prints the login URL."""

from __future__ import annotations

import json
import os
import socket
import sys


def main():
    url = next((arg for arg in sys.argv[1:] if arg.startswith("https://")), None)
    path = os.environ.get("NAUTIONETTE_CLI_CAPTURE_SOCKET")
    if not url or not path or len(url) > 16384:
        raise SystemExit(1)
    with socket.socket(socket.AF_UNIX) as connection:
        connection.settimeout(5)
        connection.connect(path)
        connection.sendall(json.dumps({"url": url}).encode())
        connection.shutdown(socket.SHUT_WR)
        # Keep the hook alive while its Linux peer/process-group identity is
        # checked. Otherwise a fast exit can race SO_PEERCRED validation.
        if connection.recv(1) != b"1":
            raise SystemExit(1)


if __name__ == "__main__":
    main()
