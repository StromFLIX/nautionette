"""Check that friendly schedules survive the round-trip through Temporal.

Run this against a live backend:

    BASE=http://127.0.0.1:18080 APP_TOKEN=... uv run python scripts/check_schedule_roundtrip.py
"""

import os
import sys

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:18080").rstrip("/")
TOKEN = os.environ.get("APP_TOKEN", "")
CASES = [
    (
        "hello_world",
        {
            "frequency": "weekly",
            "at": "08:00",
            "days": ["monday"],
            "timezone": "Europe/Berlin",
        },
    ),
    (
        "url_digest",
        {"frequency": "daily", "at": "17:30", "timezone": "America/New_York"},
    ),
]


def main() -> int:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    ok = True
    with httpx.Client(base_url=BASE, headers=headers, timeout=40) as client:
        for name, definition in CASES:
            response = client.post(
                f"/api/workflows/{name}/schedule", json={**definition, "input": {}}
            )
            response.raise_for_status()
            print("sent    ", name, "->", response.json().get("description"))

        listing = client.get("/api/workflows").json()
        schedules = {w["name"]: w.get("schedule") for w in listing.get("workflows", [])}

        for name, expected in CASES:
            got = schedules.get(name) or {}
            match = all(got.get(key) == value for key, value in expected.items())
            ok = ok and match
            print(
                "readback",
                name,
                "->",
                got.get("description"),
                "expected",
                expected,
                "MATCH" if match else "MISMATCH",
            )

        for name, _ in CASES:
            client.delete(f"/api/workflows/{name}/schedule")

    print("RESULT:", "ok" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
