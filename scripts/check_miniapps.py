"""Authenticated staging smoke. Temporary app only; no production/user data edits.

Uses GitHub environment secrets directly, never prints tokens or response bodies.
A lost publication reply is reconciled by GET, never by repeating the PUT.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


class APIError(RuntimeError):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class API:
    def __init__(self):
        self.url = os.environ["APP_URL"].rstrip("/")
        parsed = urlsplit(self.url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("APP_URL must be direct HTTPS")
        self.token = os.environ["APP_TOKEN"]
        if not self.token:
            raise ValueError("Configure the staging APP_TOKEN secret")

    def call(self, path, method="GET", body=None, expected=200):
        request = urllib.request.Request(  # noqa: S310 - validated HTTPS; redirects disabled
            self.url + path,
            method=method,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.build_opener(NoRedirect()).open(request, timeout=250) as response:
                if response.status != expected:
                    raise RuntimeError(f"Mini-app smoke: unexpected HTTP {response.status}")
                return json.load(response)
        except urllib.error.HTTPError as error:
            if error.code == expected:
                return None
            raise APIError(
                f"Mini-app smoke: {method} {path} returned HTTP {error.code}", error.code
            ) from None
        except (urllib.error.URLError, TimeoutError):
            raise RuntimeError(
                "Mini-app smoke: transport failed; inspect app status, never replay a mutation"
            ) from None


def run(api, app_id):
    path = f"/api/miniapps/{app_id}"
    contract = api.call("/api/miniapps/contract")
    manifest = contract["example"]
    manifest.update(
        {
            "title": "CI mini-app smoke",
            "description": "Disposable Nautionette CI runtime verification",
            "pinned": False,
        }
    )
    validation = api.call("/api/miniapps/validate", "POST", {"manifest": manifest, "compile": True})
    if not validation.get("compiled") or not validation.get("artifact_bytes"):
        raise RuntimeError("Mini-app smoke: offline draft compilation failed")
    created = False
    try:
        created = True  # Cleanup also checks after an ambiguous publication response.
        try:
            api.call(path, "PUT", {"expected_revision": 0, "manifest": manifest})
        except RuntimeError as publication_error:
            if isinstance(publication_error, APIError) and publication_error.status == 409:
                # An explicit create conflict means this ID is NOT ours. Never
                # reconcile, archive or delete the pre-existing app.
                created = False
                raise
            for _ in range(75):
                time.sleep(4)
                try:
                    item = api.call(path)
                except APIError as error:
                    if error.status == 404:
                        raise publication_error from None
                    raise
                if item["active_revision"] == 1:
                    break
                if item["releases"][0]["state"] == "failed":
                    raise RuntimeError("Mini-app smoke: first compilation or health check failed") from None
            else:
                raise RuntimeError("Mini-app smoke: publication not confirmed; inspect status") from None

        def invoke(revision, method="GET", request_id=""):
            return api.call(
                path + "/invoke",
                "POST",
                {
                    "revision": revision,
                    "method": method,
                    "path": "/increment" if method == "POST" else "/state",
                    "request_id": request_id,
                },
            )

        if invoke(1) != {"value": 0}:
            raise RuntimeError("Mini-app smoke: fresh SQLite state is invalid")
        pinned = api.call(path, "PATCH", {"expected_revision": 1, "pinned": True, "pin_revision": 1})
        if pinned["pinned_revision"] != 1:
            raise RuntimeError("Mini-app smoke: release pin was not saved")
        request_id = f"{int(time.time() * 1000)}-{uuid.uuid4().hex}"
        if invoke(1, "POST", request_id) != {"value": 1} or invoke(1, "POST", request_id) != {"value": 1}:
            raise RuntimeError("Mini-app smoke: mutation replay protection failed")
        api.call(path + "/control", "POST", {"action": "restart"})
        if invoke(1) != {"value": 1}:
            raise RuntimeError("Mini-app smoke: state did not survive container replacement")
        failed = dict(manifest)
        failed["backend"] = {
            "source": """use crate::{Context,Request,Result,Value,json};
pub fn health(_: &mut Context)->Result<()> {Err("intentional CI health failure".into())}
pub fn handle(_: &mut Context,_:&Request)->Result<Value>{Ok(json!({}))}"""
        }
        api.call(path, "PUT", {"expected_revision": 1, "manifest": failed}, expected=422)
        if api.call(path)["active_revision"] != 1 or invoke(1) != {"value": 1}:
            raise RuntimeError("Mini-app smoke: failed candidate replaced working code or data")
        restored = api.call(path + "/restore", "POST", {"expected_revision": 2, "revision": 1})
        if restored["active_revision"] != 3 or invoke(3) != {"value": 1}:
            raise RuntimeError("Mini-app smoke: code restore lost current data")
        api.call(path, "PATCH", {"expected_revision": 3, "archived": True})
        api.call(path + "/invoke", "POST", {"revision": 3, "path": "/state"}, expected=409)
        api.call(path, "PATCH", {"expected_revision": 3, "archived": False})
        if invoke(3) != {"value": 1}:
            raise RuntimeError("Mini-app smoke: unarchive lost state")
        diagnostics = api.call(path + "/diagnostics")
        if not diagnostics["operations"] or not diagnostics["mutations"]:
            raise RuntimeError("Mini-app smoke: request diagnostics missing")
        overview = api.call("/api/miniapps/overview")
        if not any(row["app"]["id"] == app_id for row in overview["apps"]):
            raise RuntimeError("Mini-app smoke: runtime overview omitted the active app")
        metrics = api.call(path + "/status")["runtime"]
        print(
            json.dumps(
                {
                    "miniapp_smoke": "passed",
                    "memory_bytes": metrics["memory_bytes"],
                    "image_bytes": metrics["image_bytes"],
                    "data_bytes": metrics["data_bytes"],
                }
            )
        )
    finally:
        if created:
            # Never enumerate or alter another app; this ID is unique to this run.
            unwinding = sys.exc_info()[0] is not None
            try:
                item = api.call(path)
                api.call(path, "PATCH", {"expected_revision": item["revision"], "archived": True})
                api.call(path + f"?expected_revision={item['revision']}", "DELETE")
            except APIError as error:
                if error.status != 404 and not unwinding:
                    raise
                if error.status != 404:
                    print(f"Mini-app cleanup unconfirmed: {app_id}", file=sys.stderr)
            except RuntimeError:
                if not unwinding:
                    raise
                print(f"Mini-app cleanup unconfirmed: {app_id}", file=sys.stderr)


def unpack_mcp(value):
    for _ in range(8):
        if isinstance(value, str):
            value = json.loads(value)
        elif isinstance(value, dict) and value.get("isError"):
            raise RuntimeError("Mini-app smoke: workflow MCP call failed")
        elif isinstance(value, dict) and "structuredContent" in value:
            value = value["structuredContent"]
        elif isinstance(value, dict) and "result" in value:
            value = value["result"]
        elif isinstance(value, dict) and "content" in value:
            value = json.loads("\n".join(c["text"] for c in value["content"] if c["type"] == "text"))
        else:
            return value
    raise RuntimeError("Mini-app smoke: invalid workflow MCP result")


def workflow_smoke(api):
    """A deterministic disposable workflow: no model, notification or release side effects."""
    suffix = uuid.uuid4().hex
    name = "miniapp_smoke_" + suffix
    path = "/api/miniapps/ci-workflow-" + suffix[:24]
    workflow_path = "/api/workflows/" + name
    api.call(workflow_path, expected=404)  # Never overwrite an existing workflow.
    code = """from temporalio import workflow
MANIFEST = {"schema":1,"name":"__NAME__","title":"Disposable mini-app workflow smoke",
 "inputs":{"type":"object","properties":{"version":{"type":"string"}},"required":["version"]},
 "outputs":{"type":"object"},"timeout_minutes":2}
@workflow.defn(name="__NAME__")
class MiniappWorkflowSmoke:
 @workflow.run
 async def run(self, params: dict) -> dict:
  return {"version":params["version"],"notify":False,"reason":"Disposable staging smoke"}
""".replace("__NAME__", name)
    app_created = False
    try:
        deployed = api.call(workflow_path + "/deploy", "POST", {"code": code})
        if not deployed.get("ready"):
            raise RuntimeError("Mini-app smoke: disposable workflow worker is not ready")
        manifest = api.call("/api/miniapps/contract")["example"]
        manifest["title"] = "CI scoped workflow smoke"
        manifest["backend"]["source"] = """use crate::{Context,Request,Result,Value};
pub fn health(_: &mut Context)->Result<()> {Ok(())}
pub fn handle(ctx:&mut Context, req:&Request)->Result<Value> {
 if req.method=="POST" && req.path=="/start" {
  return ctx.mcp("workflows_run_workflow",
   serde_json::json!({"name":"__NAME__","input":{"version":"minor"}}),&req.request_id);
 }
 if req.method=="GET" && req.path=="/runs" {
  return ctx.mcp("workflows_list_runs",serde_json::json!({"workflow":"__NAME__","limit":5}),"");
 }
 Err("Unknown operation".into())
}""".replace("__NAME__", name)
        manifest["backend"]["permissions"] = {
            "mcp": [
                {"tool": "workflows_run_workflow", "arguments": {"name": name}},
                {"tool": "workflows_list_runs", "arguments": {"workflow": name}},
            ]
        }
        # Inspect a conflict before claiming ownership of the temporary app.
        api.call(path, expected=404)
        app_created = True
        try:
            api.call(path, "PUT", {"expected_revision": 0, "manifest": manifest})
        except APIError as error:
            if error.status == 409:
                app_created = False
            raise
        request = {
            "revision": 1,
            "path": "/start",
            "method": "POST",
            "request_id": f"{int(time.time() * 1000)}-{uuid.uuid4().hex}",
        }
        result = unpack_mcp(api.call(path + "/invoke", "POST", request))
        if unpack_mcp(api.call(path + "/invoke", "POST", request)) != result:
            raise RuntimeError("Mini-app smoke: workflow start was not deduplicated")
        workflow_id = result["workflow_id"]
        for _ in range(30):
            info = api.call("/api/runs/" + workflow_id)
            row, live = info.get("run") or {}, info.get("temporal") or {}
            if (live.get("status") or row.get("status", "")).lower() == "completed":
                output = row.get("result") or live.get("result") or {}
                if output.get("version") != "minor":
                    raise RuntimeError("Mini-app smoke: workflow input/result mismatch")
                break
            time.sleep(2)
        else:
            raise RuntimeError("Mini-app smoke: workflow completion was not confirmed")
        history = unpack_mcp(api.call(path + "/invoke", "POST", {"revision": 1, "path": "/runs"}))
        if len(history.get("runs", [])) != 1 or history["runs"][0]["workflow_id"] != workflow_id:
            raise RuntimeError("Mini-app smoke: scoped read-only history or start deduplication failed")
        print(json.dumps({"miniapp_workflow_smoke": "passed", "status": "completed"}))
    finally:
        # UUID ownership was checked before creation; never inspect/delete user resources.
        try:
            if app_created:
                item = api.call(path)
                api.call(path, "PATCH", {"expected_revision": item["revision"], "archived": True})
                api.call(path + f"?expected_revision={item['revision']}", "DELETE")
        finally:
            api.call(workflow_path, "DELETE")


if __name__ == "__main__":
    # This script is invoked only by the staging job, never the release/promotion job.
    api = API()
    run(api, "ci-smoke-" + uuid.uuid4().hex[:24])
    workflow_smoke(api)
