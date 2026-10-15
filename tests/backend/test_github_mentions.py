"""Signed public webhook -> durable inbox -> normal chat pipeline."""

import copy
import json
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from nautionette_backend import github_mentions, projects
from nautionette_backend.routers import chats

from .test_github_setup import send_webhook


@pytest.fixture
def mention_setup(db, monkeypatch, tmp_path):
    monkeypatch.setattr(projects, "PROJECTS_DIR", tmp_path)
    monkeypatch.setattr(chats, "spawn", lambda coro, **kwargs: coro.close())
    project_id = "a" * 32
    (tmp_path / project_id).mkdir()
    connection_id = projects.save_connection(
        {
            "app_id": "12",
            "installation_id": "34",
            "webhook_secret": "secret",
            "private_key": "key",
            "slug": "nautionette-test",
            "public_url": "https://nautionette.example.com",
            "mention_user_ids": [42],
        }
    )
    db.execute(
        "INSERT INTO projects (id, repository_id, full_name, default_branch, status, connection_id) "
        "VALUES (?,123,'org/repo','main','ready',?)",
        (project_id, connection_id),
    )
    payload = {
        "action": "created",
        "installation": {"id": 34},
        "repository": {"id": 123},
        "sender": {"id": 42, "type": "User"},
        "issue": {"number": 3, "title": "Fix recovery", "body": "Untrusted issue context"},
        "comment": {
            "id": 100,
            "body": "@nautionette-test[bot] Please fix this.",
            "html_url": "https://github.com/org/repo/issues/3#issuecomment-100",
            "user": {"id": 42, "type": "User", "login": "trusted"},
        },
    }
    return projects.app_config(connection_id), payload, project_id


def test_settings_are_authenticated_and_strict(client, anonymous, mention_setup):
    config, _, _ = mention_setup
    path = f"/api/projects/github-app/{config['id']}/mentions"
    assert anonymous.put(path, json={"user_ids": [99]}).status_code == 401
    for value in ([True], [0], [-1], ["42"], None, "42"):
        assert client.put(path, json={"user_ids": value}).status_code == 422
    response = client.put(path, json={"user_ids": [42, 42, 99], "acknowledge": False})
    assert response.status_code == 200
    assert response.json()["mention_user_ids"] == [42, 99]
    assert response.json()["mention_acknowledge"] is False
    assert "private_key" not in response.text and "webhook_secret" not in response.text
    assert client.put(path, json={"user_ids": []}).status_code == 200


@pytest.mark.parametrize(
    "body",
    [
        "@nautionette-test Please fix this.",
        "@nautionette-test[bot] Please fix this.",
        "Please fix, @NAUTIONETTE-TEST!",
    ],
)
def test_typed_mentions_are_accepted_and_recorded(db, anonymous, mention_setup, monkeypatch, body):
    config, payload, _ = mention_setup
    payload["comment"]["body"] = body
    publish = Mock()
    monkeypatch.setattr(github_mentions.bus, "publish", publish)
    result = send_webhook(anonymous, payload, event="issue_comment").json()
    assert result["accepted"]
    assert projects.app_config(config["id"])["webhook"]["event"] == "issue_comment"
    publish.assert_called_once()
    assert publish.call_args.args[0] == "projects.github_event"
    assert publish.call_args.args[1]["result"] == result


@pytest.mark.parametrize(
    "body",
    [
        "@nautionette-test-other fix",
        "@nautionette-test[bot]-other fix",
        "@nautionette-test[other] fix",
        "@nautionette-testing fix",
        "someone@nautionette-test fix",
        "@@nautionette-test fix",
    ],
)
def test_partial_mentions_stay_ineligible(db, anonymous, mention_setup, body):
    config, payload, _ = mention_setup
    payload["comment"]["body"] = body
    assert send_webhook(anonymous, payload, event="issue_comment").json()["ignored"]
    assert not db.query("SELECT * FROM github_mentions")
    # Ignored comments still update receipt diagnostics, without storing comment content.
    assert projects.app_config(config["id"])["webhook"]["event"] == "issue_comment"


@pytest.mark.parametrize(
    "change",
    [
        "outsider",
        "bot",
        "sender",
        "edited",
        "no-mention",
        "partial-mention",
        "pull-request",
        "wrong-installation",
        "wrong-repository",
        "not-ready",
        "disabled",
        "suspended",
        "malformed",
    ],
)
def test_ineligible_comments_never_create_work(db, anonymous, mention_setup, change):
    config, payload, _ = mention_setup
    if change == "outsider":
        payload["comment"]["user"]["id"] = payload["sender"]["id"] = 99
    elif change == "bot":
        payload["comment"]["user"]["type"] = "Bot"
    elif change == "sender":
        payload["sender"]["id"] = 99
    elif change == "edited":
        payload["action"] = "edited"
    elif change == "no-mention":
        payload["comment"]["body"] = "Please do something"
    elif change == "partial-mention":
        payload["comment"]["body"] = "@nautionette-test[bot]-other"
    elif change == "pull-request":
        payload["issue"]["pull_request"] = {}
    elif change == "wrong-installation":
        payload["installation"]["id"] = 88
    elif change == "wrong-repository":
        payload["repository"]["id"] = 88
    elif change == "not-ready":
        db.execute("UPDATE projects SET status = 'archived'")
    elif change == "disabled":
        github_mentions.configure(config["id"], [], True)
    elif change == "suspended":
        projects.save_connection(config | {"installation_status": "suspended"})
    elif change == "malformed":
        payload["comment"] = "not an object"
    assert send_webhook(anonymous, payload, event="issue_comment").json()["ignored"]
    assert not db.query("SELECT * FROM github_mentions")
    assert not db.list_chats()


@pytest.mark.asyncio
async def test_mentions_create_one_visible_chat_and_queue_followups(
    db, anonymous, mention_setup, monkeypatch
):
    config, payload, project_id = mention_setup
    ack = AsyncMock()
    monkeypatch.setattr(github_mentions, "acknowledge", ack)
    launch = Mock()
    monkeypatch.setattr(chats, "launch_next", launch)
    assert send_webhook(anonymous, payload, event="issue_comment").json()["accepted"]
    assert send_webhook(anonymous, payload, event="issue_comment", delivery="again").json()["duplicate"]
    assert not db.list_chats()  # Request only persists; no agent/API work on webhook path.
    await github_mentions.consume_once()
    chat = db.list_chats()[0]
    assert chat["title"] == "org/repo #3: Fix recovery"
    assert chat["project_ids"] == [project_id]
    first = db.list_messages(chat["id"])[0]
    assert "Untrusted issue context" in first["content"]
    assert "Please fix this" in first["content"]
    job = json.loads(db.one("SELECT job FROM chat_turns WHERE id = ?", (first["id"],))["job"])
    assert job["project_ids"] == [project_id]
    assert "Selected writable per-chat Git worktrees" in job["system_prompt"]
    ack.assert_awaited_once()
    payload["comment"]["id"] = 101
    payload["comment"]["body"] = "@nautionette-test[bot] Also add tests."
    assert send_webhook(anonymous, payload, event="issue_comment", delivery="followup").json()["accepted"]
    await github_mentions.consume_once()
    assert len(db.list_chats()) == 1
    messages = db.list_messages(chat["id"])
    assert len(messages) == 2
    assert "Also add tests" in messages[1]["content"]
    assert launch.call_count == 1  # first turn is running; the follow-up uses the queue
    turns = db.query("SELECT state FROM chat_turns ORDER BY rowid")
    assert [turn["state"] for turn in turns] == ["running", "queued"]
    await github_mentions.consume_once()
    assert len(db.list_messages(chat["id"])) == 2
    assert ack.await_count == 2
    assert all(
        row["state"] == "done" and row["payload"] == "{}" for row in db.query("SELECT * FROM github_mentions")
    )


@pytest.mark.asyncio
async def test_failed_ack_retries_without_duplicate_chat_or_turn(db, anonymous, mention_setup, monkeypatch):
    _, payload, _ = mention_setup
    ack = AsyncMock(side_effect=HTTPException(502, "GitHub returned 403"))
    monkeypatch.setattr(github_mentions, "acknowledge", ack)
    monkeypatch.setattr(chats, "launch_next", Mock())
    send_webhook(anonymous, payload, event="issue_comment")
    await github_mentions.consume_once()
    assert len(db.list_chats()) == 1
    assert not db.list_messages(db.list_chats()[0]["id"])
    row = db.one("SELECT * FROM github_mentions")
    assert row["attempts"] == 1 and row["error"]
    assert projects.app_status()["mention_inbox"] == {"pending": 1, "retrying": 1}
    ack.side_effect = None
    db.execute("UPDATE github_mentions SET retry_at = 0")
    await github_mentions.consume_once()
    assert len(db.list_chats()) == 1
    assert len(db.list_messages(db.list_chats()[0]["id"])) == 1


@pytest.mark.asyncio
async def test_submit_retry_is_idempotent_and_ack_not_repeated(db, anonymous, mention_setup, monkeypatch):
    _, payload, _ = mention_setup
    ack = AsyncMock()
    monkeypatch.setattr(github_mentions, "acknowledge", ack)
    monkeypatch.setattr(chats, "launch_next", Mock())
    original = chats.submit_message

    async def failed_after_submission(*args, **kwargs):
        await original(*args, **kwargs)
        raise RuntimeError("Lost response after durable submission")

    monkeypatch.setattr(chats, "submit_message", failed_after_submission)
    send_webhook(anonymous, payload, event="issue_comment")
    await github_mentions.consume_once()
    assert db.one("SELECT state FROM github_mentions")["state"] == "acknowledged"
    monkeypatch.setattr(chats, "submit_message", original)
    db.execute("UPDATE github_mentions SET retry_at = 0")
    await github_mentions.consume_once()
    assert len(db.list_messages(db.list_chats()[0]["id"])) == 1
    ack.assert_awaited_once()


@pytest.mark.asyncio
async def test_partial_acceptance_is_repaired_and_acknowledgements_can_be_disabled(
    db, anonymous, mention_setup, monkeypatch
):
    config, payload, _ = mention_setup
    github_mentions.configure(config["id"], [42], False)
    ack = AsyncMock()
    monkeypatch.setattr(github_mentions, "acknowledge", ack)
    monkeypatch.setattr(chats, "launch_next", Mock())
    submit = chats.submit_message

    async def crash_before_job_persisted(chat_id, data):
        db.accept_chat_message(chat_id, data["text"], data["message_id"], data["project_ids"], queue=True)
        raise RuntimeError("Process died before persisting the job")

    monkeypatch.setattr(chats, "submit_message", crash_before_job_persisted)
    send_webhook(anonymous, payload, event="issue_comment")
    await github_mentions.consume_once()
    turn = db.one("SELECT * FROM chat_turns")
    assert turn["job"] is None
    monkeypatch.setattr(chats, "submit_message", submit)
    db.execute("UPDATE github_mentions SET retry_at = 0")
    await github_mentions.consume_once()
    turn = db.one("SELECT * FROM chat_turns")
    assert json.loads(turn["job"])["project_ids"] == ["a" * 32]
    assert "Selected writable per-chat Git worktrees" in json.loads(turn["job"])["system_prompt"]
    assert len(db.list_chats()) == 1
    assert len(db.list_messages(turn["chat_id"])) == 1
    ack.assert_not_awaited()
    assert db.one("SELECT state FROM github_mentions")["state"] == "done"


@pytest.mark.asyncio
async def test_revoke_access_before_processing(db, anonymous, mention_setup, monkeypatch):
    config, payload, _ = mention_setup
    ack = AsyncMock()
    monkeypatch.setattr(github_mentions, "acknowledge", ack)
    send_webhook(anonymous, payload, event="issue_comment")
    github_mentions.configure(config["id"], [], True)
    await github_mentions.consume_once()
    assert not db.list_chats()
    ack.assert_not_awaited()
    assert db.one("SELECT state FROM github_mentions")["state"] == "ignored"


@pytest.mark.asyncio
async def test_ack_uses_scoped_token_chat_link_and_reconciles_existing_reply(mention_setup, monkeypatch):
    config, payload, project_id = mention_setup
    monkeypatch.setattr(projects, "app_jwt", lambda config: "jwt")
    remote = AsyncMock(side_effect=[{"token": "issue-token"}, [], {"id": 999}])
    monkeypatch.setattr(projects, "github", remote)
    project = projects.db.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    await github_mentions.acknowledge(config, project, payload, "chat123")
    token_request = remote.await_args_list[0]
    assert token_request.kwargs["json"] == {"repository_ids": [123], "permissions": {"issues": "write"}}
    body = remote.await_args_list[2].kwargs["json"]["body"]
    assert "https://nautionette.example.com/chats/chat123" in body
    remote.reset_mock()
    remote.side_effect = [
        {"token": "issue-token"},
        [{"body": body, "user": {"login": "nautionette-test[bot]"}}],
    ]
    await github_mentions.acknowledge(config, project, payload, "chat123")
    assert remote.await_count == 2  # no duplicate POST


@pytest.mark.asyncio
async def test_separate_issues_are_not_blocked_by_retry(db, anonymous, mention_setup, monkeypatch):
    _, payload, _ = mention_setup
    monkeypatch.setattr(github_mentions, "acknowledge", AsyncMock())
    monkeypatch.setattr(chats, "launch_next", Mock())
    send_webhook(anonymous, payload, event="issue_comment")
    db.execute("UPDATE github_mentions SET retry_at = 99999999999")
    followup = copy.deepcopy(payload)
    followup["comment"]["id"] = 101
    send_webhook(anonymous, followup, event="issue_comment", delivery="followup")
    other = copy.deepcopy(payload)
    other["issue"]["number"] = 4
    other["comment"]["id"] = 102
    send_webhook(anonymous, other, event="issue_comment", delivery="other")
    await github_mentions.consume_once()
    assert len(db.list_chats()) == 1
    assert "#4" in db.list_chats()[0]["title"]
    assert len(db.query("SELECT * FROM github_mentions WHERE state = 'pending'")) == 2
