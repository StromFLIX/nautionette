"""App metadata, explicit release pins, passive resource sampling and authoring diagnostics."""

import asyncio
import json
import sqlite3
from unittest.mock import AsyncMock

import httpx
import pytest
from nautionette_backend.db import Database

from tests.backend import test_miniapps as fixtures

publication, rid, runtime = fixtures.publication, fixtures.rid, fixtures.runtime


def test_existing_pins_migrate_once_and_keep_their_order_across_restarts(tmp_path):
    path = str(tmp_path / "old.sqlite")
    with sqlite3.connect(path) as old:
        old.executescript("""
            CREATE TABLE mini_apps (
                id TEXT PRIMARY KEY, revision INTEGER NOT NULL DEFAULT 0,
                active_revision INTEGER, agent_id TEXT, title TEXT NOT NULL,
                pinned INTEGER NOT NULL DEFAULT 1, archived INTEGER NOT NULL DEFAULT 0,
                updated_at REAL NOT NULL
            );
            INSERT INTO mini_apps(id,title,revision,active_revision,updated_at)
            VALUES('legacy','Legacy',3,3,42);
        """)
    upgraded = Database(path)
    assert upgraded.query("SELECT pinned_revision,pinned_at,icon,image FROM mini_apps") == [
        {"pinned_revision": 3, "pinned_at": 42, "icon": "apps", "image": ""}
    ]
    upgraded.execute("UPDATE mini_apps SET pinned_at=99,updated_at=100")
    upgraded._conn.close()
    upgraded = Database(path)
    assert upgraded.query("SELECT pinned_at FROM mini_apps")[0]["pinned_at"] == 99
    upgraded._conn.close()


def test_icons_and_release_pins_are_explicit_and_versioned(client):
    body = publication(client)
    body["manifest"].update(icon="analytics", image="data:image/png;base64,aGVsbG8=")
    first = client.put("/api/miniapps/counter", json=body).json()
    assert first["icon"] == "analytics" and first["image"].startswith("data:image/png;")
    assert not first["pinned"] and first["pinned_revision"] is None
    path = "/api/miniapps/counter"
    assert client.patch(path, json={"expected_revision": 1, "pinned": True}).status_code == 409
    assert (
        client.patch(path, json={"expected_revision": 1, "pinned": True, "pin_revision": 2}).status_code
        == 409
    )
    pinned = client.patch(path, json={"expected_revision": 1, "pinned": True, "pin_revision": 1}).json()
    assert pinned["pinned_revision"] == 1 and pinned["pinned_at"] > 0
    body["expected_revision"] = 1
    body["manifest"]["icon"] = "monitor_heart"
    second = client.put(path, json=body).json()
    assert second["icon"] == "monitor_heart"
    assert not second["pinned"] and second["pinned_revision"] is None
    restored = client.post(
        path + "/restore", json={"expected_revision": 2, "revision": 1, "pinned": True}
    ).json()
    assert restored["icon"] == "analytics" and restored["active_revision"] == restored["pinned_revision"] == 3
    client.patch(path, json={"expected_revision": 3, "archived": True})
    assert (
        client.patch(path, json={"expected_revision": 3, "pinned": True, "pin_revision": 3}).status_code
        == 409
    )


@pytest.mark.parametrize(
    "metadata",
    [
        {"icon": "<svg onload=alert(1)>"},
        {"icon": "a" * 49},
        {"image": "https://tracker.test/avatar.png"},
        {"image": "data:image/svg+xml;base64,aGVsbG8="},
        {"image": "data:image/png;base64," + "a" * 32768},
    ],
)
def test_icon_metadata_cannot_load_remote_or_executable_assets(client, metadata):
    body = publication(client)
    body["manifest"].update(metadata)
    assert client.put("/api/miniapps/counter", json=body).status_code == 422


def test_failed_publication_never_replaces_pinned_release(client, runtime):
    method, _ = runtime
    body = publication(client, True)
    body["manifest"]["pinned"] = True
    client.put("/api/miniapps/counter", json=body)
    method.side_effect = httpx.HTTPStatusError(
        "failed",
        request=httpx.Request("POST", "http://broker"),
        response=httpx.Response(422, json={"detail": "compiler error"}),
    )
    body["expected_revision"] = 1
    assert client.put("/api/miniapps/counter", json=body).status_code == 422
    item = client.get("/api/miniapps/counter").json()
    assert item["active_revision"] == item["pinned_revision"] == 1 and item["pinned"]


def test_draft_validation_and_offline_compilation_do_not_publish(client, runtime, db):
    method, _ = runtime
    body = {"manifest": publication(client, True)["manifest"]}
    reply = client.post("/api/miniapps/validate", json=body)
    assert reply.status_code == 200 and reply.json()["valid"] and not reply.json()["compiled"]
    method.assert_not_called()
    method.return_value = None
    method.side_effect = AsyncMock(
        return_value={"valid": True, "artifact_bytes": 123, "sdk_fingerprint": "sdk"}
    )
    reply = client.post("/api/miniapps/validate", json={**body, "compile": True})
    assert reply.json()["compiled"] and reply.json()["artifact_bytes"] == 123
    assert method.call_args.args == (
        "source-check",
        "check",
        {"source": body["manifest"]["backend"]["source"]},
    )
    assert not db.query("SELECT * FROM mini_apps")
    assert not db.query("SELECT * FROM mini_app_releases")
    body["manifest"]["agent_id"] = "missing"
    assert client.post("/api/miniapps/validate", json=body).status_code == 422


def test_compile_errors_are_returned_without_creating_a_release(client, runtime):
    method, _ = runtime
    method.side_effect = httpx.HTTPStatusError(
        "failed",
        request=httpx.Request("POST", "http://broker"),
        response=httpx.Response(422, json={"detail": "error[E0308]: mismatched types in app.rs"}),
    )
    response = client.post(
        "/api/miniapps/validate", json={"manifest": publication(client, True)["manifest"], "compile": True}
    )
    assert response.status_code == 422 and "E0308" in response.text
    assert client.get("/api/miniapps").json()["apps"] == []


def test_overview_retains_unavailable_and_building_apps_without_waking_them(client, runtime, db):
    method, _ = runtime
    for name in ["working", "offline", "pending"]:
        client.put("/api/miniapps/" + name, json=publication(client, True))
    client.put("/api/miniapps/static", json=publication(client))
    db.execute("UPDATE mini_app_releases SET state='building' WHERE app_id='pending'")
    method.reset_mock()

    async def sample(app_id, action="", payload=None):
        assert action == "" and payload is None
        if app_id == "offline":
            raise TimeoutError("no status")
        return {"status": "suspended", "memory_bytes": 0, "data_bytes": 123, "image_bytes": 456}

    method.side_effect = sample
    overview = client.get("/api/miniapps/overview").json()
    states = {row["app"]["id"]: row["runtime"]["status"] for row in overview["apps"]}
    assert states == {
        "working": "suspended",
        "offline": "unavailable",
        "pending": "building",
        "static": "frontend-only",
    }
    assert overview["incomplete"] and overview["totals"]["data_bytes"] == 123
    assert method.call_count == 2


def test_overview_samples_at_most_four_runtimes_concurrently(client, runtime):
    method, _ = runtime
    for name in ["one", "two", "three", "four", "five", "six"]:
        client.put("/api/miniapps/" + name, json=publication(client, True))
    active = maximum = 0

    async def sample(*args):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        return {"status": "suspended"}

    method.side_effect = sample
    assert client.get("/api/miniapps/overview").status_code == 200
    assert maximum == 4


def test_diagnostics_are_bounded_private_and_do_not_replay_operations(client, runtime, db):
    method, _ = runtime
    client.put("/api/miniapps/counter", json=publication(client, True))
    for _ in range(103):
        assert (
            client.post(
                "/api/miniapps/counter/invoke",
                json={"revision": 1, "path": "/state", "body": {"secret": "private-payload"}},
            ).status_code
            == 200
        )
    request_id = rid()
    method.side_effect = TimeoutError("private-token")
    assert (
        client.post(
            "/api/miniapps/counter/invoke",
            json={"revision": 1, "path": "/write", "method": "POST", "request_id": request_id},
        ).status_code
        == 503
    )
    method.reset_mock()
    diagnostics = client.get("/api/miniapps/counter/diagnostics").json()
    assert len(diagnostics["operations"]) == 100
    assert diagnostics["operations"][0]["operation"] == "POST /write"
    assert diagnostics["operations"][0]["outcome"] == "uncertain"
    assert diagnostics["mutations"][0]["request_id"] == request_id
    assert diagnostics["mutations"][0]["state"] == "uncertain"
    method.assert_not_called()
    encoded = json.dumps(diagnostics)
    assert "private-payload" not in encoded and "private-token" not in encoded and "response" not in encoded
    assert "request_hash" not in encoded
    db.execute("UPDATE mini_apps SET archived=1 WHERE id='counter'")
    method.side_effect = AsyncMock(return_value={})
    assert client.delete("/api/miniapps/counter?expected_revision=1").status_code == 200
    assert not db.query("SELECT * FROM mini_app_events")


@pytest.mark.parametrize(
    "path,method",
    [
        ("/api/miniapps/overview", "get"),
        ("/api/miniapps/a/diagnostics", "get"),
        ("/api/miniapps/validate", "post"),
    ],
)
def test_development_endpoints_require_user_auth(anonymous, path, method):
    assert getattr(anonymous, method)(path).status_code == 401
