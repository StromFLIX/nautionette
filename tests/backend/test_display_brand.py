"""Display identity changes must not migrate saved configuration or API identity."""

from nautionette.brand import NAME
from nautionette_backend.agent.prompts import CHAT_SYSTEM_PROMPT, WORKFLOW_AUTHOR_PROMPT
from nautionette_backend.backend_mcp import INSTRUCTIONS


def test_api_and_agent_instructions_use_the_canonical_display_name(client):
    schema = client.get("/api/openapi.json").json()
    assert schema["info"]["title"] == NAME
    assert f"agent inside {NAME}," in CHAT_SYSTEM_PROMPT
    assert f"workflow file for {NAME}." in WORKFLOW_AUTHOR_PROMPT
    assert f"Operate and diagnose {NAME} directly." in INSTRUCTIONS
    # Embedded example dictionaries must remain Python, not f-string substitutions.
    assert '{"notify": False, "reason": "..."}' in WORKFLOW_AUTHOR_PROMPT
    assert "workflows_write_workflow" in CHAT_SYSTEM_PROMPT


def test_existing_git_identity_is_data_not_display_branding(client, db):
    db.set_setting("git_automation_name", "Nautionette")
    db.set_setting("git_automation_email", "old-bot@example.test")
    response = client.get("/api/settings").json()
    assert response["defaults"]["git_automation_name"] == NAME
    assert response["settings"]["git_automation_name"] == "Nautionette"
    assert response["settings"]["git_automation_email"] == "old-bot@example.test"
    assert response["defaults"]["git_automation_email"] == "nautionette@users.noreply.github.com"
    client.put("/api/settings", json={"history_chars": 2000})
    assert client.get("/api/settings").json()["settings"]["git_automation_name"] == "Nautionette"
