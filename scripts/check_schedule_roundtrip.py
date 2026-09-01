"""Check that a cron expression survives the round-trip through Temporal.

Temporal rewrites cron strings into structured calendars, so reading a schedule
back is not guaranteed to return what was sent. Run it against a live backend:

    BASE=http://127.0.0.1:18080 APP_TOKEN=... uv run python scripts/check_schedule_roundtrip.py
"""

import os
import sys

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:18080").rstrip("/")
TOKEN = os.environ.get("APP_TOKEN", "")
CASES = [("hello_world", "0 8 * * 1"), ("url_digest", "*/15 9-17 * * *")]


def main() -> int:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    ok = True
    with httpx.Client(base_url=BASE, headers=headers, timeout=40) as client:
        for name, cron in CASES:
            response = client.post(f"/api/workflows/{name}/schedule", json={"cron": cron, "input": {}})
            response.raise_for_status()
            print("sent    ", name, "->", response.json().get("cron"))

        listing = client.get("/api/workflows").json()
        schedules = {w["name"]: w.get("schedule") for w in listing.get("workflows", [])}

        for name, cron in CASES:
            got = (schedules.get(name) or {}).get("cron")
            match = got == cron
            ok = ok and match
            print("readback", name, "->", got, "expected", cron, "MATCH" if match else "MISMATCH")

        for name, _ in CASES:
            client.delete(f"/api/workflows/{name}/schedule")

    print("RESULT:", "ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
