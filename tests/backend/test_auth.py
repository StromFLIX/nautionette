"""Who may call what."""

from __future__ import annotations

from ..conftest import APP_TOKEN, INTERNAL_TOKEN


def test_healthz_needs_no_token(anonymous):
    response = anonymous.get("/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": "test"}


def test_api_refuses_an_anonymous_caller(anonymous):
    assert anonymous.get("/api/chats").status_code == 401


def test_api_refuses_the_wrong_token(anonymous):
    assert anonymous.get("/api/chats", headers={"Authorization": "Bearer nope"}).status_code == 401


def test_the_token_may_arrive_three_ways(anonymous):
    assert anonymous.get("/api/chats", headers={"Authorization": f"Bearer {APP_TOKEN}"}).status_code == 200
    assert anonymous.get("/api/chats", headers={"X-Auth-Token": APP_TOKEN}).status_code == 200
    assert anonymous.get("/api/chats", params={"token": APP_TOKEN}).status_code == 200


def test_bearer_is_case_insensitive(anonymous):
    assert anonymous.get("/api/chats", headers={"Authorization": f"BEARER {APP_TOKEN}"}).status_code == 200


def test_internal_routes_want_the_internal_token(anonymous):
    assert anonymous.get("/internal/runs").status_code == 401
    assert anonymous.get("/internal/runs", headers={"X-Auth-Token": APP_TOKEN}).status_code == 401
    assert anonymous.get("/internal/runs", headers={"X-Internal-Token": INTERNAL_TOKEN}).status_code == 200


def test_unknown_api_path_is_never_proxied_to_the_frontend(client):
    response = client.get("/api/nothing-here")
    assert response.status_code == 404
    assert response.json() == {"detail": "not found"}
    assert client.get("/internal/nothing-here").status_code == 404
