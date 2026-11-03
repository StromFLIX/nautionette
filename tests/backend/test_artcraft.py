from __future__ import annotations

import asyncio
import copy
import json
import time

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
    assert app()["status"] == "queued" and app()["version"] == bundled_release("cadcraft")["version"]
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


async def test_queued_updates_and_restart_recovery(db, resolved):
    first = await artcraft.configure("cadcraft", {"version": "1.0.0"})
    await artcraft.configure("wordcraft", {"version": "1.0.0"})
    with pytest.raises(HTTPException) as same:
        await artcraft.configure("cadcraft", {"enabled": False})
    assert same.value.status_code == 409
    assert await artcraft.configure("deckcraft", {"version": "1.0.0"})
    artcraft.recover_installations()
    assert app()["status"] == "queued" and not app()["error"]
    assert all(item["bundled"] for item in artcraft.apps())
    assert artcraft._read("cadcraft")["pending"] == first


def test_route_starts_background_install_but_does_not_claim_success(client, resolved, monkeypatch):
    captured = []

    def spawn(coro, **kwargs):
        captured.append(kwargs["name"])
        coro.close()

    monkeypatch.setattr(routes, "spawn", spawn)
    response = client.put("/api/artcraft/apps/cadcraft", json={"version": "1.0.0"})
    assert response.status_code == 200 and response.json()["status"] == "queued"
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


async def test_bulk_discovery_persists_latest_orders_semantically_and_isolates_outages(db, monkeypatch):
    calls = []

    async def available(name, refresh=False):
        calls.append(name)
        if name == "cadcraft":
            raise HTTPException(502, "private upstream details")
        return [{"release": {**bundled_release(name), "version": "1.10.0"}, "version": "1.10.0"}]

    monkeypatch.setattr(artcraft, "releases", available)
    assert artcraft.begin_check()
    assert not artcraft.begin_check()
    await artcraft.check_updates()
    assert len(calls) == 13
    assert not artcraft.updates()["checking"] and artcraft.updates()["checked_at"]
    assert app("wordcraft")["latest_version"] == "1.10.0" and app("wordcraft")["update_available"]
    assert not app()["update_available"] and "retry" in app()["update_error"]
    assert "private upstream" not in json.dumps(artcraft.apps())
    artcraft._releases.clear()  # a process cache reset must not erase Settings status
    assert app("wordcraft")["latest_version"] == "1.10.0"
    assert artcraft._version_key("1.10.0") > artcraft._version_key("1.9.9")
    assert artcraft._version_key("1.0.0") > artcraft._version_key("1.0.0-beta")
    assert all(item["status"] == "ready" for item in artcraft.apps())  # checking does not opt into updates


async def seed_updates(db, *, automatic=False):
    with db.transaction() as conn:
        state = artcraft.updates(conn)
        state.update(automatic=automatic, checked_at=time.time())
        state["latest"] = {
            name: {
                "release": {**bundled_release(name), "version": "9.0.0"},
                "checked_at": time.time(),
                "error": "",
            }
            for name in catalog()
        }
        artcraft._write_updates(conn, state)


async def test_update_all_is_bounded_idempotent_keeps_disabled_state_and_continues_after_failure(
    db, monkeypatch
):
    await artcraft.configure("cadcraft", {"enabled": False})
    before = copy.deepcopy(artcraft.for_run())
    await seed_updates(db)
    assert len(artcraft.enqueue_updates()) == 13
    assert artcraft.enqueue_updates() == []
    assert all(item["status"] == "queued" for item in artcraft.apps())
    running, maximum, installed = 0, 0, []

    async def install(identifier, spec):
        nonlocal running, maximum
        running += 1
        maximum = max(maximum, running)
        await asyncio.sleep(0.001)
        running -= 1
        installed.append(spec["app"])
        if spec["app"] == "wordcraft":
            raise RuntimeError("private error")
        return spec

    monkeypatch.setattr(artcraft.broker, "install_artcraft", install)
    await asyncio.gather(artcraft.process_queue(), artcraft.process_queue(), artcraft.process_queue())
    assert maximum == 2 and len(installed) == 13 and len(set(installed)) == 13
    assert app()["version"] == "9.0.0" and not app()["enabled"]
    assert app("wordcraft")["bundled"] and "previous version" in app("wordcraft")["error"]
    assert all(item["status"] == "ready" for item in artcraft.apps())
    assert before != artcraft.for_run()
    assert all(item["release"] == bundled_release(item["app"]) for item in before)
    assert artcraft.enqueue_updates() == ["wordcraft"]  # retry only failed updates


async def test_auto_update_opt_in_refresh_applies_all_and_off_switch_is_honored(db, monkeypatch):
    async def available(name, refresh=False):
        return [{"release": {**bundled_release(name), "version": "9.0.0"}, "version": "9.0.0"}]

    async def install(identifier, spec):
        return spec

    monkeypatch.setattr(artcraft, "releases", available)
    monkeypatch.setattr(artcraft.broker, "install_artcraft", install)
    artcraft.configure_updates({"automatic": True, "expected_revision": 0})
    assert artcraft.begin_check()
    await artcraft.check_updates()
    assert all(item["version"] == "9.0.0" for item in artcraft.apps())
    artcraft.configure_updates({"automatic": False, "expected_revision": 1})
    await artcraft.configure("cadcraft", {"version": "bundled"})
    assert artcraft.begin_check()
    await artcraft.check_updates()
    assert app()["bundled"] and app()["update_available"]


def test_update_routes_require_auth_validate_preferences_and_preserve_optimistic_revisions(
    client, anonymous, monkeypatch
):
    captured = []

    def spawn(coro, **kwargs):
        captured.append(kwargs["name"])
        coro.close()

    monkeypatch.setattr(routes, "spawn", spawn)
    assert anonymous.post("/api/artcraft/updates/check").status_code == 401
    assert anonymous.post("/api/artcraft/updates/apply").status_code == 401
    assert (
        anonymous.put("/api/artcraft/updates", json={"automatic": True, "expected_revision": 0}).status_code
        == 401
    )
    assert client.post("/api/artcraft/updates/check").json()["updates"]["checking"]
    assert client.post("/api/artcraft/updates/check").status_code == 200
    assert captured == ["artcraft-release-check"]
    for payload in (
        {},
        {"automatic": "true", "expected_revision": 0},
        {"automatic": True, "expected_revision": True},
        {"automatic": True, "expected_revision": 0, "url": "evil"},
    ):
        assert client.put("/api/artcraft/updates", json=payload).status_code == 422
    assert client.put("/api/artcraft/updates", json={"automatic": True, "expected_revision": 0}).json()[
        "updates"
    ]["automatic"]
    assert (
        client.put("/api/artcraft/updates", json={"automatic": False, "expected_revision": 0}).status_code
        == 409
    )


async def test_discovery_does_not_downgrade_newer_pins_or_queue_failed_checks(db, resolved):
    await seed_updates(db)
    pending = await artcraft.configure("cadcraft", {"version": "10.0.0"})
    with db.transaction() as conn:
        state = artcraft._read("cadcraft", conn)
        state.update(active=pending, pending=None)
        artcraft._write(conn, "cadcraft", state)
        discovery = artcraft.updates(conn)
        discovery["latest"]["wordcraft"]["error"] = "Lookup failed"
        artcraft._write_updates(conn, discovery)
    queued = artcraft.enqueue_updates()
    assert "cadcraft" not in queued and "wordcraft" not in queued and len(queued) == 11


async def test_restart_requeues_the_same_verified_identity_and_resets_discovery_flag(
    db, resolved, monkeypatch
):
    pending = await artcraft.configure("cadcraft", {"version": "1.0.0"})
    with db.transaction() as conn:
        state = artcraft._read("cadcraft", conn)
        state["installing"] = True
        artcraft._write(conn, "cadcraft", state)
    assert artcraft.begin_check()
    artcraft.recover_installations()
    assert not artcraft.updates()["checking"]
    assert app()["status"] == "queued"
    seen = []

    async def install(identifier, spec):
        seen.append(identifier)
        return spec

    monkeypatch.setattr(artcraft.broker, "install_artcraft", install)
    await artcraft.process_queue()
    assert seen == [pending["installation_id"]] and app()["version"] == "1.0.0"


@pytest.mark.parametrize("failure", ["busy", "unavailable", "transport"])
async def test_transient_installer_outages_keep_the_exact_queued_identity_without_busy_loop(
    db, resolved, monkeypatch, failure
):
    pending = await artcraft.configure("cadcraft", {"version": "1.0.0"})
    seen = []

    async def install(identifier, spec):
        seen.append(identifier)
        if len(seen) == 1:
            request = httpx.Request("POST", "http://broker/artcraft/install")
            if failure == "transport":
                raise httpx.ReadTimeout("uncertain reply", request=request)
            response = httpx.Response(
                422 if failure == "busy" else 503,
                json={"detail": "This ArtCraft artifact is already being prepared; retry shortly"},
                request=request,
            )
            raise httpx.HTTPStatusError("unavailable", request=request, response=response)
        return spec

    monkeypatch.setattr(artcraft.broker, "install_artcraft", install)
    await artcraft.process_queue()
    assert seen == [pending["installation_id"]]
    assert app()["status"] == "queued" and "will retry" in app()["error"]
    assert artcraft._read("cadcraft")["pending"] == pending
    await artcraft.process_queue()
    assert seen == [pending["installation_id"]] * 2
    assert app()["version"] == "1.0.0" and not app()["error"]


async def test_maintenance_checks_hourly_and_drains_updates_without_settings_requests(db, monkeypatch):
    calls = []
    with db.transaction() as conn:
        state = artcraft.updates(conn)
        state.update(automatic=True, checked_at=time.time() - artcraft.CHECK_INTERVAL - 1)
        artcraft._write_updates(conn, state)

    async def check():
        calls.append("check")
        assert artcraft.updates()["checking"]

    async def process():
        calls.append("queue")

    async def stop(seconds):
        assert seconds == 30
        raise asyncio.CancelledError

    monkeypatch.setattr(artcraft, "check_updates", check)
    monkeypatch.setattr(artcraft, "process_queue", process)
    monkeypatch.setattr(artcraft.asyncio, "sleep", stop)
    with pytest.raises(asyncio.CancelledError):
        await artcraft.maintenance()
    assert calls == ["check", "queue"]


def test_release_discovery_uses_semantic_order_and_never_promotes_prerelease_tags(client, http):
    http["https://api.github.com/repos/storytold/cadcraft/releases"] = lambda request: httpx.Response(
        200, json=[release_data("1.9.9"), release_data("1.10.0"), release_data("9.0.0-beta")]
    )
    assert client.get("/api/artcraft/apps/cadcraft/releases").json() == {"versions": ["1.10.0", "1.9.9"]}
