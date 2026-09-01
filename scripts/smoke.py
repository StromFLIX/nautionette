"""End-to-end check of a running Nautionette instance.

Walks the promise on the front page: talk, promote, approve, run, schedule.

    BASE=http://app... APP_TOKEN=... uv run --with httpx python scripts/smoke.py
"""

import json
import os
import sys
import time

import httpx

BASE = os.environ.get("BASE", "http://127.0.0.1:18080").rstrip("/")
TOKEN = os.environ.get("APP_TOKEN", "")
WEBSITE = os.environ.get("WEBSITE", "")

results: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    results.append((label, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {label}" + (f"  [{detail}]" if detail else ""))
    return ok


def sample_for(inputs: dict, key: str) -> object:
    """A plausible value for a required field, taken from its declared type."""
    spec = (inputs.get("properties") or {}).get(key) or {}
    kind = spec.get("type", "string")
    if kind == "integer":
        return 1
    if kind == "number":
        return 1.0
    if kind == "boolean":
        return True
    if kind == "array":
        return []
    if kind == "object":
        return {}
    if "url" in key.lower():
        return "https://example.com"
    return "smoke test"


def sse_text(response: httpx.Response) -> tuple[str, str | None]:
    """Collect the assistant text and any error from a chat stream."""
    parts: list[str] = []
    error = None
    for line in response.iter_lines():
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        if event.get("type") == "delta":
            parts.append(event.get("text", ""))
        elif event.get("type") == "error":
            error = event.get("message")
        elif event.get("type") == "done":
            parts = parts or [event["message"]["content"]]
    return "".join(parts).strip(), error


def main() -> int:
    headers = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}
    client = httpx.Client(base_url=BASE, headers=headers, timeout=httpx.Timeout(420, connect=15))

    if WEBSITE:
        check("website serves the landing page", httpx.get(WEBSITE, timeout=20).status_code == 200)

    check("backend is healthy", client.get("/healthz").json()["status"] == "ok")

    if TOKEN:
        anonymous = httpx.get(f"{BASE}/api/system", timeout=20)
        check("auth is enforced", anonymous.status_code == 401, f"got {anonymous.status_code}")

    system = client.get("/api/system").json()
    down = [c["name"] for c in system["components"] if c["status"] != "ok"]
    check("every component is up", not down, ",".join(down))
    check("an agent set is ready", any(a["ready"] for a in system.get("agent_sets", [])))

    chat = client.post("/api/chats", json={"title": "Smoke test"}).json()
    with client.stream(
        "POST",
        f"/api/chats/{chat['id']}/messages",
        json={"text": "Every Monday, read https://example.com and summarise it in two bullets."},
    ) as response:
        answer, error = sse_text(response)
    check("the agent answers in chat", bool(answer) and not error, error or answer[:60])

    draft = client.post(f"/api/chats/{chat['id']}/promote").json()
    name = draft["name"]
    check("promotion validates", draft["validation"]["valid"], str(draft["validation"]["errors"]))
    check("the agent wrote the workflow", draft.get("origin") == "agent", draft.get("origin", "?"))
    check("the diff is reviewable", bool(draft.get("diff")))

    approved = client.post(f"/api/drafts/{name}/approve").json()
    restarted = (approved.get("worker_restart") or {}).get("restarted") or []
    check("approval publishes", approved.get("published") is True)
    check("the worker restarts", bool(restarted), ",".join(restarted))

    time.sleep(30)

    inputs = (draft.get("manifest") or {}).get("inputs") or {}
    required = inputs.get("required") or []
    if required:
        rejected = client.post(f"/api/workflows/{name}/run", json={"input": {}})
        check("missing input is refused", rejected.status_code == 400, f"got {rejected.status_code}")

    payload = {key: sample_for(inputs, key) for key in required}
    started = client.post(f"/api/workflows/{name}/run", json={"input": payload}).json()
    status, result = "?", None
    for _ in range(20):
        time.sleep(10)
        run = client.get(f"/api/runs/{started['workflow_id']}").json()
        status = (run.get("temporal") or {}).get("status", "?")
        if status not in {"RUNNING", "UNKNOWN", "?"}:
            result = (run.get("temporal") or {}).get("result") or (run.get("run") or {}).get("result")
            break
    check("the workflow completes", status == "COMPLETED", f"{status} {json.dumps(result)[:120]}")

    client.post(f"/api/workflows/{name}/schedule", json={"cron": "0 8 * * 1", "input": {}})
    listing = client.get("/api/workflows").json()["workflows"]
    schedule = next((w.get("schedule") for w in listing if w["name"] == name), None)
    check("the schedule reads back", (schedule or {}).get("cron") == "0 8 * * 1", str(schedule))

    client.request("DELETE", f"/api/workflows/{name}/schedule")
    client.request("DELETE", f"/api/workflows/{name}")
    client.request("DELETE", f"/api/chats/{chat['id']}")
    client.close()

    failed = [label for label, ok, _ in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
