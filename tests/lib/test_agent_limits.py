import pytest
from nautionette.agent_limits import chat_timeout_exempt


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({}, True),
        ({"timeout_exempt": False}, False),
        ({"timeout_exempt": None}, False),
        ({"timeout_exempt": "true"}, False),
        ({"timeout_exempt": 1}, False),
        ({"mode": "workflow"}, False),
        ({"mode": None}, False),
        ({"chat_id": ""}, False),
        ({"turn_id": ""}, False),
    ],
)
def test_only_explicit_interactive_chat_exemptions_bypass_limits(overrides, expected):
    job = {"chat_id": "chat", "turn_id": "turn", "mode": "interactive", "timeout_exempt": True}
    assert chat_timeout_exempt({**job, **overrides}) is expected
    assert chat_timeout_exempt({}) is False
