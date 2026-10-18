from __future__ import annotations

import copy
import json

import httpx
import pytest
from fastapi import HTTPException
from nautionette.artcraft import architecture, asset_name, bundled_release, catalog
from nautionette_backend import artcraft
from nautionette_backend.agent.runner import stream_agent
from nautionette_backend.routers import artcraft as routes


@pytest.fixture(autouse=True)
def clear_release_cache():
    artcraft._releases.clear()
    yield
    artcraft._releases.clear()


def app(app="cadcraft"):
    return next(item for item in artcraft.apps() if item["id"] == app)


@pytest.fixture
def resolved(monkeypatch):
    async def resolve(app, version):
        return {"app": app, "version": version, "arch": architecture(), "sha256": "a" * 64}

    monkeypatch.setattr(artcraft, "resolve", resolve)
    return resolve


def test_defaults_are_all_native_apps_with_authenticated_settings(client, anonymous):
    data = client.get("/api/artcraft/apps").json()
    assert data["architecture"] == architecture()
    assert {item["id"] for item in data["apps"]} == set(catalog())
    assert len(data["apps"]) == 13
    assert all(item["enabled"] and item["bundled"] and item["status"] == "ready" for item in data["apps"])
    assert len(artcraft.for_run()) == 13
    for path in ("/api/artcraft/apps", "/api/artcraft/apps/cadcraft/releases"):
        assert anonymous.get(path).status_code == 401
    assert anonymous.put("/api/artcraft/apps/cadcraft", json={"enabled": False}).status_code == 401
    assert client.put("/api/artcraft/apps/evil", json={"enabled": True}).status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"expected_revision": 0},
        {"enabled": "false"},
        {"enabled": 0},
        {"enabled": None},
        {"version": None},
        {"version": "latest"},
        {"version": "https://evil/1.0.0"},
        {"version": "1.0.0;sh"},
        {"version": "../1.0.0"},
        {"version": 1},
        {"enabled": True, "expected_revision": True},
        {"command": "sh"},
        {"image": "evil"},
    ],
)
def test_config_is_exact_data_not_a_code_source(client, payload):
    assert client.put("/api/artcraft/apps/cadcraft", json=payload).status_code == 422
    assert app()["revision"] == 0


def test_enable_updates_are_revision_guarded_and_snapshots_do_not_change(client):
    before = copy.deepcopy(artcraft.for_run())
    response = client.put("/api/artcraft/apps/cadcraft", json={"enabled": False, "expected_revision": 0})
    assert response.status_code == 200
    assert not response.json()["enabled"]
    assert all(item["app"] != "cadcraft" for item in artcraft.for_run())
    assert any(item["app"] == "cadcraft" for item in before)
    assert (
        client.put("/api/artcraft/apps/cadcraft", json={"enabled": True, "expected_revision": 0}).status_code
        == 409
    )
    assert not app()["enabled"]
    assert (
        client.put("/api/artcraft/apps/cadcraft", json={"enabled": True, "expected_revision": 1}).status_code
        == 200
    )


async def test_verified_updates_are_atomic_keep_active_turns_and_can_restore_bundled(
    db, resolved, monkeypatch
):
    before = artcraft.for_run()
    pending = await artcraft.configure("cadcraft", {"version": "1.0.0", "expected_revision": 0})
    assert app()["status"] == "installing" and app()["version"] == bundled_release("cadcraft")["version"]
    assert artcraft.for_run() == before

    async def install(identifier, spec):
        assert identifier == pending["installation_id"]
        return spec

    monkeypatch.setattr(artcraft.broker, "install_artcraft", install)
    await artcraft.finish_install(pending)
    assert app()["version"] == "1.0.0" and not app()["bundled"]
    assert app()["status"] == "ready" and not app()["error"]
    active = next(item for item in artcraft.for_run() if item["app"] == "cadcraft")
    assert active == pending
    assert next(item for item in before if item["app"] == "cadcraft")["release"] == bundled_release(
        "cadcraft"
    )
    await artcraft.configure("cadcraft", {"version": "bundled", "expected_revision": 2})
    assert app()["bundled"] and active["release"]["version"] == "1.0.0"
    assert next(item for item in artcraft.for_run() if item["app"] == "cadcraft")[
        "release"
    ] == bundled_release("cadcraft")


@pytest.mark.parametrize("failure", ["error", "mismatched-release"])
async def test_failed_update_never_switches_working_version_or_leaks_logs(db, resolved, monkeypatch, failure):
    pending = await artcraft.configure("cadcraft", {"version": "1.0.0"})

    async def install(*args):
        if failure == "error":
            raise RuntimeError("secret-diagnostic")
        return {**pending["release"], "version": "9.9.9"}

    monkeypatch.setattr(artcraft.broker, "install_artcraft", install)
    await artcraft.finish_install(pending)
    assert app()["bundled"] and app()["status"] == "ready"
    assert "previous version is unchanged" in app()["error"]
    assert "secret-diagnostic" not in json.dumps(artcraft.apps())


async def test_bounded_updates_and_restart_recovery(db, resolved):
    await artcraft.configure("cadcraft", {"version": "1.0.0"})
    await artcraft.configure("wordcraft", {"version": "1.0.0"})
    with pytest.raises(HTTPException) as same:
        await artcraft.configure("cadcraft", {"enabled": False})
    assert same.value.status_code == 409
    with pytest.raises(HTTPException) as full:
        await artcraft.configure("deckcraft", {"version": "1.0.0"})
    assert full.value.status_code == 409
    artcraft.recover_installations()
    assert app()["status"] == "ready" and "interrupted" in app()["error"]
    assert all(item["bundled"] for item in artcraft.apps())
    assert await artcraft.configure("cadcraft", {"version": "1.0.0"})


def test_route_starts_background_install_but_does_not_claim_success(client, resolved, monkeypatch):
    captured = []

    def spawn(coro, **kwargs):
        captured.append(kwargs["name"])
        coro.close()

    monkeypatch.setattr(routes, "spawn", spawn)
    response = client.put("/api/artcraft/apps/cadcraft", json={"version": "1.0.0"})
    assert response.status_code == 200 and response.json()["status"] == "installing"
    assert response.json()["pending_version"] == "1.0.0" and response.json()["bundled"]
    assert len(captured) == 1


async def test_turn_plumbing_overrides_user_supplied_runtime_and_preserves_job(db, monkeypatch, resolved):
    await artcraft.configure("cadcraft", {"enabled": False})
    job = {"prompt": "hello", "artcraft": [{"app": "evil"}]}
    seen = []

    async def run(outgoing, **kwargs):
        seen.append(outgoing)
        yield {"type": "result", "ok": True}

    monkeypatch.setattr(artcraft.broker, "run_agent", run)
    assert [event async for event in stream_agent(job)][0]["ok"]
    assert len(seen[0]["artcraft"]) == 12
    assert all(item["app"] not in {"cadcraft", "evil"} for item in seen[0]["artcraft"])
    assert job["artcraft"] == [{"app": "evil"}]


def release_data(version="1.0.0", **kwargs):
    return {
        "tag_name": f"v{version}",
        "assets": [
            {
                "name": asset_name("cadcraft", version, architecture()),
                "digest": "sha256:" + "f" * 64,
            }
        ],
        **kwargs,
    }


def test_discovery_uses_fixed_upstream_ignores_unsigned_prereleases_and_caches(client, http):
    calls = []

    def upstream(request):
        calls.append(str(request.url))
        return httpx.Response(
            200,
            json=[
                release_data(),
                release_data("2.0.0", prerelease=True),
                release_data("3.0.0", draft=True),
                {"tag_name": "v4.0.0", "assets": []},
            ],
        )

    http["https://api.github.com/repos/storytold/cadcraft/releases"] = upstream
    path = "/api/artcraft/apps/cadcraft/releases"
    assert client.get(path).json() == {"versions": ["1.0.0"]}
    assert client.get(path).json() == {"versions": ["1.0.0"]}
    assert len(calls) == 1 and calls[0].endswith("?per_page=20")
    assert client.get(path + "?refresh=true").status_code == 200 and len(calls) == 2


async def test_old_exact_release_lookup_rejects_missing_digest_or_wrong_tag(db, http):
    base = "https://api.github.com/repos/storytold/cadcraft/releases"
    http[base + "/tags/v0.1.0"] = lambda request: httpx.Response(200, json=release_data("0.1.0"))
    http[base] = lambda request: httpx.Response(200, json=[])
    assert (await artcraft.resolve("cadcraft", "0.1.0"))["version"] == "0.1.0"
    http[base + "/tags/v0.1.0"] = lambda request: httpx.Response(200, json=release_data("0.2.0"))
    with pytest.raises(HTTPException) as failure:
        await artcraft.resolve("cadcraft", "0.1.0")
    assert failure.value.status_code == 422


def test_release_outages_are_retryable_and_do_not_change_settings(client, http):
    response = client.get("/api/artcraft/apps/cadcraft/releases")
    assert response.status_code == 502
    assert app()["revision"] == 0
