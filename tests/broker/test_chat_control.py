import json
import threading
from types import SimpleNamespace
from unittest.mock import Mock

from nautionette_docker_broker import agent_run


def test_control_targets_only_the_exact_chat_and_turn(monkeypatch):
    container = SimpleNamespace(kill=Mock(), exec_run=Mock(return_value=SimpleNamespace(
        exit_code=0, output=b'{"ok":true}',
    )))
    containers = SimpleNamespace(list=Mock(return_value=[container]))
    monkeypatch.setattr(agent_run.daemon, 'client', lambda: SimpleNamespace(containers=containers))
    command = {'id': 'input', 'type': 'steer', 'text': 'Next instruction'}
    assert agent_run.control('chat-a', 'turn-a', command)
    containers.list.assert_called_with(filters={'label': ['nautionette.chat=chat-a', 'nautionette.turn=turn-a']})
    assert json.loads(container.exec_run.call_args.args[0][-1]) == command
    assert agent_run.control('chat-a', 'turn-a', {'id': 'stop', 'type': 'stop'})
    container.kill.assert_called_once()


def test_stop_also_cancels_a_turn_waiting_for_its_image(monkeypatch):
    stopped = threading.Event()
    monkeypatch.setattr(agent_run, '_stopped', {('chat', 'turn'): stopped})
    monkeypatch.setattr(agent_run.daemon, 'client', lambda: SimpleNamespace(
        containers=SimpleNamespace(list=lambda **kwargs: []),
    ))
    monkeypatch.setattr(agent_run.images, 'start_build', lambda: None)
    assert agent_run.control('chat', 'turn', {'id': 'stop', 'type': 'stop'})
    assert stopped.is_set()
    assert list(agent_run._await_image('missing', stopped)) == []
    assert not agent_run.control('other', 'turn', {'id': 'stop', 'type': 'stop'})