"""Staging-only, deterministic control/delivery smoke; no model or external effects."""

import json
import time
import uuid
from datetime import UTC, datetime, timedelta

from scripts.check_miniapps import API


def check(api):
    if api.call("/api/system").get("environment") != "staging":
        raise RuntimeError("Workflow lifecycle smoke refuses non-staging environments")
    name = "lifecycle_smoke_" + uuid.uuid4().hex
    path = "/api/workflows/" + name
    if any(item["name"] == name for item in api.call("/api/workflows")["workflows"]):
        raise RuntimeError("Disposable workflow name already exists; refusing to overwrite")
    code = """from temporalio import workflow
MANIFEST = {"schema":1,"name":"__NAME__","title":"Disposable workflow lifecycle smoke",
 "inputs":{"type":"object","properties":{"silent":{"type":"boolean"}},"additionalProperties":False},
 "outputs":{"type":"object"},"timeout_minutes":2}
@workflow.defn(name="__NAME__")
class LifecycleSmoke:
 @workflow.run
 async def run(self, params: dict) -> dict:
  return {"summary":"Lifecycle delivery verified", "notify":not params.get("silent",False)}
""".replace("__NAME__", name)
    created = False
    chat_id = None
    try:
        created = True  # Reconcile/cleanup only this UUID after an ambiguous reply.
        deployed = api.call(path + "/deploy", "POST", {"code": code})
        if not deployed.get("ready"):
            raise RuntimeError("Disposable workflow worker not ready")
        # Never schedule near the test window; even an unintended start is pure code.
        at = (datetime.now(UTC) + timedelta(hours=12)).strftime("%H:%M")
        definition = {"frequency": "daily", "at": at, "timezone": "UTC", "input": {"silent": True}}
        api.call(path + "/schedule", "POST", definition)
        api.call(path + "/settings", "PATCH", {"disabled": True})
        disabled = api.call(path)
        if not disabled["settings"]["disabled"] or not disabled["schedule"]["paused"]:
            raise RuntimeError("Disabled workflow still has an active schedule")
        api.call(path + "/run", "POST", {}, expected=409)
        api.call(path + "/schedule", "POST", definition, expected=409)
        api.call(path + "/settings", "PATCH", {"disabled": False})
        enabled = api.call(path)
        if enabled["schedule"]["paused"] or any(
            enabled["schedule"].get(key) != value for key, value in definition.items()
        ):
            raise RuntimeError("Re-enabling changed the schedule recurrence or inputs")
        api.call(path + "/schedule", "DELETE")
        # A lost start reply is never retried. The disposable workflow is pure code.
        run_id = api.call(path + "/run", "POST", {})["workflow_id"]
        for _ in range(60):
            info = api.call("/api/runs/" + run_id)
            status = (info.get("run") or {}).get("status")
            current = api.call(path)
            chat_id = current["settings"].get("chat_id")
            if status == "completed" and chat_id:
                snapshot = api.call("/api/chats/" + chat_id)
                replies = [
                    m
                    for m in snapshot["messages"]
                    if m.get("meta", {}).get("run", {}).get("workflow_id") == run_id
                ]
                if len(replies) == 1:
                    origin = replies[0]["meta"]["origin"]
                    if origin.get("relation") != "result" or origin.get("workflow_id") != run_id:
                        raise RuntimeError("Delivered workflow provenance mismatch")
                    break
            if status in {"failed", "timed_out", "terminated", "canceled"}:
                raise RuntimeError("Disposable workflow did not complete successfully")
            time.sleep(2)
        else:
            raise RuntimeError("Workflow result was not delivered to its chat")
        print(
            json.dumps(
                {
                    "workflow_lifecycle_smoke": "passed",
                    "schedule_control": "verified",
                    "chat_delivery": "verified",
                }
            )
        )
    finally:
        if created:
            # Never enumerate/delete arbitrary chats or edit existing workflows.
            if not chat_id:
                chat_id = api.call(path)["settings"].get("chat_id")
            if chat_id:
                api.call("/api/chats/" + chat_id, "DELETE")
            api.call(path, "DELETE")


if __name__ == "__main__":
    check(API())
