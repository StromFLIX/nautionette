from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from nautionette import chat_sessions as shared
from nautionette_docker_broker import chat_agents, chat_sessions


@pytest.fixture
def sessions(tmp_path, monkeypatch):
    monkeypatch.setattr(shared, "SESSIONS_DIR", tmp_path)
    monkeypatch.setattr(chat_sessions, "SESSIONS_DIR", tmp_path)
    chown = Mock()
    monkeypatch.setattr(chat_sessions.os, "chown", chown)
    monkeypatch.setattr(chat_agents, "containers", lambda chat_id: [])
    mount = {"Destination": str(tmp_path), "Type": "volume", "Name": "private-chat-sessions"}
    docker = SimpleNamespace(
        api=SimpleNamespace(_version="1.45"),
        containers=SimpleNamespace(get=lambda name: SimpleNamespace(attrs={"Mounts": [mount]})),
    )
    monkeypatch.setattr(chat_sessions.daemon, "client", lambda: docker)
    return tmp_path, docker, mount, chown


def test_mounts_only_the_exact_chat_subpath_and_releases_writer_lock(sessions):
    root, _, _, chown = sessions
    with chat_sessions.claim("chat-one") as mounts:
        assert len(mounts) == 1
        assert mounts[0]["Source"] == "private-chat-sessions"
        assert mounts[0]["Target"] == "/chat-session"
        assert mounts[0]["VolumeOptions"] == {"Subpath": "chat-one"}
        assert (root / "chat-one").stat().st_mode & 0o777 == 0o700
        chown.assert_called_once_with(root / "chat-one", 10001, 10001)
        with pytest.raises(RuntimeError, match="already in use"), chat_sessions.claim("chat-one"):
            pass
        with chat_sessions.claim("chat-two"):
            pass
    # Exception paths must release the same inode as normal completion.
    with pytest.raises(RuntimeError, match="test failure"), chat_sessions.claim("chat-one"):
        raise RuntimeError("test failure")
    with chat_sessions.claim("chat-one"):
        pass


def test_workflow_agents_do_not_get_chat_storage(sessions):
    with chat_sessions.claim("") as mounts:
        assert mounts == []


def test_stale_container_and_old_engine_fail_closed(sessions, monkeypatch):
    _, docker, _, _ = sessions
    monkeypatch.setattr(chat_agents, "containers", lambda chat_id: [object()])
    with pytest.raises(RuntimeError, match="previous chat agent"), chat_sessions.claim("stale"):
        pass
    docker.api._version = "1.44"
    with pytest.raises(RuntimeError, match="Engine 26"), chat_sessions.claim("old-engine"):
        pass


def test_oversized_state_wrong_mount_and_symlinks_are_rejected(sessions, monkeypatch):
    root, _, mount, _ = sessions
    directory = root / "large"
    directory.mkdir()
    (directory / "session.jsonl").write_bytes(b"large")
    monkeypatch.setattr(chat_sessions, "MAX_SESSION_BYTES", 5)
    with pytest.raises(RuntimeError, match="storage limit"), chat_sessions.claim("large"):
        pass
    monkeypatch.setattr(chat_sessions, "MAX_SESSION_BYTES", 1000)
    (directory / "bad").symlink_to(root / "other-chat")
    with pytest.raises(ValueError, match="Symlinks"), chat_sessions.claim("large"):
        pass
    mount["Type"] = "bind"
    with pytest.raises(ValueError, match="named Docker volume"), chat_sessions.claim("wrong-mount"):
        pass
