from __future__ import annotations

import json
import uuid

import httpx
import pytest
from nautionette_backend import agent_profiles, pi_packages
from nautionette_backend.agent.runner import stream_agent
from nautionette_backend.routers import pi_packages as routes


@pytest.fixture
def artifact(db):
    def create(source="npm:example@1.0.0", status="ready"):
        item = uuid.uuid4().hex
        db.execute(
            "INSERT INTO pi_package_installations (id, source, status, created_at, metadata) "
            "VALUES (?,?,?,?,?)",
            (
                item,
                source,
                status,
                1,
                json.dumps(
                    {
                        "root": "npm/node_modules/example",
                        "resolved": source,
                        "resources": {"prompts": ["prompts"]},
                    }
                ),
            ),
        )
        return item

    return create


def revision(client, artifact_id, **values):
    response = client.post("/api/pi-packages/revisions", json={"installation_id": artifact_id, **values})
    assert response.status_code == 201, response.text
    return response.json()


def test_private_revision_values_never_enter_profiles_chats_or_catalog(client, artifact, db):
    package = artifact()
    first = revision(
        client,
        package,
        configuration={
            "env": {"SERVICE_API_KEY": "secret-one"},
            "files": {"agent/service.json": '{"password":"secret-two"}'},
        },
    )
    assert first["configuration"] == {"env": {"SERVICE_API_KEY": None}, "files": {"agent/service.json": None}}
    saved = client.post(
        "/api/agents", json={"name": "Packages", "config": {"packages": [first["id"]]}}
    ).json()
    chat = client.post("/api/chats", json={"agent_id": saved["id"]}).json()
    assert chat["packages"] == [first["id"]]
    db.execute(
        "UPDATE chats SET queue_paused = 1, package_commands = ? WHERE id = ?",
        ('[{"name":"old"}]', chat["id"]),
    )
    accepted = client.post(
        f"/api/chats/{chat['id']}/messages",
        json={"text": "/review", "message_id": "package-queued", "queue": True},
    )
    assert accepted.status_code == 202
    queued = db.one("SELECT job FROM chat_turns WHERE id = 'package-queued'")["job"]
    assert json.loads(queued)["packages"] == [first["id"]]
    assert "secret-one" not in queued and "secret-two" not in queued
    second = revision(
        client, package, previous_id=first["id"], configuration={"env": {"SERVICE_API_KEY": None}}
    )
    assert pi_packages.for_run([second["id"]])[0]["configuration"] == {
        "env": {"SERVICE_API_KEY": "secret-one"},
        "files": {},
    }
    assert pi_packages.for_run([first["id"]])[0]["configuration"]["files"]
    client.patch(f"/api/agents/{saved['id']}", json={"config": {"packages": [second["id"]]}})
    assert client.get(f"/api/chats/{chat['id']}").json()["chat"]["packages"] == [first["id"]]
    for path in (
        "/api/catalog",
        "/api/agents",
        "/api/chats",
        "/api/pi-packages/installations",
        f"/api/pi-packages/revisions/{first['id']}",
    ):
        text = client.get(path).text
        assert "secret-one" not in text and "secret-two" not in text
    client.patch(f"/api/chats/{chat['id']}", json={"agent_id": saved["id"]})
    current = client.get(f"/api/chats/{chat['id']}").json()["chat"]
    assert current["packages"] == [second["id"]]
    assert current["package_commands"] == []
    assert db.one("SELECT job FROM chat_turns WHERE id = 'package-queued'")["job"] == queued
    client.delete(f"/api/agents/{saved['id']}")
    assert pi_packages.for_run([first["id"]])  # Existing chats retain revisions after profile deletion.


@pytest.mark.parametrize(
    "configuration",
    [
        {"env": {"NODE_OPTIONS": "--require evil.js"}},
        {"env": {"HOME": "/root"}},
        {"env": {"PI_CODING_AGENT_DIR": "/evil"}},
        {"env": {"NAUTIONETTE_TOOLS": "all"}},
        {"files": {"agent/settings.json": "{}"}},
        {"files": {"agent/auth.json": "{}"}},
        {"files": {"../outside.json": "{}"}},
        {"files": {"agent/extensions/evil.ts": "code"}},
        {"env": {"SERVICE_API_KEY": None}},
        {"env": {"SERVICE_API_KEY": 123}},
        {"env": {"SERVICE_API_KEY": "x" * 64001}},
    ],
)
def test_rejects_unsafe_config_without_echoing_values(client, artifact, configuration):
    response = client.post(
        "/api/pi-packages/revisions", json={"installation_id": artifact(), "configuration": configuration}
    )
    assert response.status_code == 422


def test_filters_missing_failed_duplicates_and_conflicts(client, artifact):
    for value in ({"unknown": []}, {"extensions": ["../evil.ts"]}, {"prompts": "all"}):
        response = client.post(
            "/api/pi-packages/revisions", json={"installation_id": artifact(), "filters": value}
        )
        assert response.status_code == 422
    assert (
        client.post(
            "/api/pi-packages/revisions", json={"installation_id": artifact(status="failed")}
        ).status_code
        == 409
    )
    first = revision(
        client,
        artifact(),
        filters={"extensions": [], "prompts": ["prompts/review.md"]},
        configuration={"env": {"SERVICE_REGION": "west"}},
    )
    second = revision(client, artifact(), configuration={"env": {"SERVICE_REGION": "east"}})
    for ids in ([first["id"], first["id"]], [first["id"], second["id"]], ["missing"], ["f" * 32], None):
        assert (
            client.post("/api/agents", json={"name": "Invalid", "config": {"packages": ids}}).status_code
            == 422
        )
    assert agent_profiles.global_config()["packages"] == []


async def test_secret_resolution_is_execution_only_and_does_not_mutate_job(
    client, artifact, monkeypatch, live
):
    selected = revision(client, artifact(), configuration={"env": {"SERVICE_API_KEY": "private"}})
    job = {"prompt": "hi", "packages": [selected["id"]]}
    seen = []

    async def run(outgoing, **kwargs):
        seen.append(outgoing)
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(live.broker, "run_agent", run)
    assert [event async for event in stream_agent(job)][0]["ok"]
    assert "package_runtime" not in job and "private" not in json.dumps(job)
    assert seen[0]["package_runtime"][0]["configuration"]["env"]["SERVICE_API_KEY"] == "private"


async def test_install_success_failure_and_restart_recovery(db, artifact, monkeypatch, live):
    async def install(*args):
        return {"root": "package", "resolved": "npm:example@1.0.0"}

    monkeypatch.setattr(live.broker, "install_package", install)
    item = artifact(status="installing")
    await pi_packages.finish_install(item)
    assert pi_packages.installation(item)["status"] == "ready"

    async def fail(*args):
        raise RuntimeError("secret-install-output")

    monkeypatch.setattr(live.broker, "install_package", fail)
    failed = artifact(status="installing")
    await pi_packages.finish_install(failed)
    assert pi_packages.installation(failed)["status"] == "failed"
    assert "secret-install-output" not in pi_packages.installation(failed)["error"]
    interrupted = artifact(status="installing")
    pi_packages.recover_installations()
    assert pi_packages.installation(interrupted)["status"] == "failed"
    assert pi_packages.installation(item)["status"] == "ready"


def test_install_input_and_authentication(client, anonymous):
    for source in (
        "/local/pkg",
        "npm:https://evil/a.tgz",
        "git:ssh://host/repo",
        "https://user:password@github.com/o/r",
    ):
        assert client.post("/api/pi-packages/installations", json={"source": source}).status_code == 422
    for path in ("/api/pi-packages/installations", "/api/pi-packages/search"):
        assert anonymous.get(path).status_code == 401
    assert anonymous.post("/api/pi-packages/revisions", json={}).status_code == 401


def test_search_is_cached_and_filters_catalog_results(client, monkeypatch):
    calls = []

    class HTTP:
        async def get(self, url, **kwargs):
            calls.append((url, kwargs))
            return httpx.Response(
                200,
                request=httpx.Request("GET", url),
                json={
                    "total": 25,
                    "objects": [
                        {"package": {"name": "example", "version": "1.2.3", "keywords": ["pi-package"]}},
                        {"package": {"name": "unrelated", "version": "1", "keywords": []}},
                    ],
                },
            )

    routes._search_cache.clear()
    monkeypatch.setattr(routes, "shared", HTTP)
    first = client.get("/api/pi-packages/search?q=example").json()
    assert first["packages"] == [{"name": "example", "version": "1.2.3", "description": ""}]
    assert first["next_offset"] == 20
    assert client.get("/api/pi-packages/search?q=example").json() == first
    assert len(calls) == 1
    assert calls[0][0] == "https://registry.npmjs.org/-/v1/search"
    assert calls[0][1]["params"]["text"] == "keywords:pi-package example"


def test_configuration_is_encrypted_and_missing_or_corrupt_keys_fail_closed(
    client, artifact, db, monkeypatch, tmp_path
):
    from types import SimpleNamespace

    from nautionette_backend import pi_package_secrets

    monkeypatch.setattr(pi_package_secrets, "settings", SimpleNamespace(data_dir=tmp_path))
    selected = revision(client, artifact(), configuration={"env": {"SERVICE_API_KEY": "database-secret"}})
    stored = db.one("SELECT configuration FROM pi_package_revisions WHERE id = ?", (selected["id"],))[
        "configuration"
    ]
    assert "database-secret" not in stored and "SERVICE_API_KEY" not in stored
    key = tmp_path / "pi-packages.key"
    assert key.stat().st_mode & 0o777 == 0o600
    assert (
        pi_packages.for_run([selected["id"]])[0]["configuration"]["env"]["SERVICE_API_KEY"]
        == "database-secret"
    )
    key.unlink()
    response = client.get(f"/api/pi-packages/revisions/{selected['id']}")
    assert response.status_code == 503 and "database-secret" not in response.text
    assert not key.exists()  # Missing decryption keys must not be silently replaced.
    key.write_text("damaged")
    assert client.get(f"/api/pi-packages/revisions/{selected['id']}").status_code == 503


def test_concurrent_configuration_writers_keep_one_key(monkeypatch, tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from types import SimpleNamespace

    from nautionette_backend import pi_package_secrets

    monkeypatch.setattr(pi_package_secrets, "settings", SimpleNamespace(data_dir=tmp_path))
    with ThreadPoolExecutor(max_workers=8) as pool:
        sealed = list(pool.map(pi_package_secrets.seal, [{"env": {"VALUE": str(i)}} for i in range(20)]))
    assert [pi_package_secrets.unseal(value)["env"]["VALUE"] for value in sealed] == [
        str(i) for i in range(20)
    ]
    assert [path.name for path in tmp_path.iterdir()] == ["pi-packages.key"]


def test_library_defaults_are_selectable_without_agents_and_do_not_adopt_private_config(client, artifact):
    item = artifact()
    private = revision(client, item, configuration={"env": {"PRIVATE_KEY": "agent-only"}})
    library = client.get("/api/pi-packages/installations").json()["installations"][0]
    default_id = library["default_revision_id"]
    assert default_id != private["id"]
    assert pi_packages.for_run([default_id])[0]["configuration"] == {"env": {}, "files": {}}
    assert client.get("/api/pi-packages/installations").json()["installations"][0] == library
    chat = client.post("/api/chats", json={"packages": [default_id]}).json()
    assert chat["packages"] == [default_id]
    assert chat["agent_id"] is None
    assert client.patch(f"/api/chats/{chat['id']}", json={"packages": []}).json()["packages"] == []


def test_library_configuration_is_atomic_private_and_does_not_reselect_snapshots(client, artifact, db):
    item = artifact()
    default_id = pi_packages.library_installation(item)["default_revision_id"]
    agent = client.post("/api/agents", json={"name": "Selected", "config": {"packages": [default_id]}}).json()
    chat = client.post("/api/chats", json={"agent_id": agent["id"]}).json()
    db.execute("UPDATE chats SET queue_paused = 1 WHERE id = ?", (chat["id"],))
    client.post(
        f"/api/chats/{chat['id']}/messages", json={"text": "Review", "message_id": "pinned", "queue": True}
    )
    job = db.one("SELECT job FROM chat_turns WHERE id = 'pinned'")["job"]
    path = f"/api/pi-packages/installations/{item}/configuration"
    payload = {"previous_id": default_id, "configuration": {"env": {"SERVICE_KEY": "secret"}}}
    saved = client.patch(path, json=payload)
    assert saved.status_code == 200
    assert "secret" not in saved.text
    updated = saved.json()["id"]
    assert pi_packages.library_installation(item)["default_revision_id"] == updated
    assert client.patch(path, json=payload).status_code == 409
    assert pi_packages.library_installation(item)["default_revision_id"] == updated
    assert client.get(f"/api/agents/{agent['id']}").json()["config"]["packages"] == [default_id]
    assert client.get(f"/api/chats/{chat['id']}").json()["chat"]["packages"] == [default_id]
    assert db.one("SELECT job FROM chat_turns WHERE id = 'pinned'")["job"] == job
    assert (
        client.patch(
            path, json={"previous_id": updated, "configuration": {"env": {"HOME": "bad"}}}
        ).status_code
        == 422
    )
    assert pi_packages.library_installation(item)["default_revision_id"] == updated
    assert pi_packages.for_run([updated])[0]["configuration"]["env"]["SERVICE_KEY"] == "secret"


def test_library_config_rejects_unrelated_revisions_and_requires_auth(client, anonymous, artifact):
    first, second = artifact(), artifact()
    previous = revision(client, first, configuration={"env": {"KEY": "private"}})
    assert (
        client.post(
            "/api/pi-packages/revisions",
            json={
                "installation_id": second,
                "previous_id": previous["id"],
                "configuration": {"env": {"KEY": None}},
            },
        ).status_code
        == 422
    )
    assert (
        anonymous.patch(f"/api/pi-packages/installations/{first}/configuration", json={}).status_code == 401
    )
    assert pi_packages.library_installation(artifact(status="failed"))["default_revision_id"] is None
