import runpy


def test_artifact_readiness_diagnostics_exclude_arbitrary_errors_and_credentials(repo_root):
    diagnostic = runpy.run_path(str(repo_root / "scripts/check_artcraft_deployment.py"))["diagnostic"]
    result = diagnostic(
        {
            "components": [
                {
                    "name": "broker",
                    "status": "degraded",
                    "detail": {
                        "image_status": "failed",
                        "error": "private-token: all predefined address pools have been fully subnetted",
                        "workers": {"status": "ready"},
                    },
                }
            ],
            "agent_sets": [{"ready": False}],
        }
    )
    assert result["error_categories"] == ["network_address_pool"]
    assert result["workers_ready"] and not result["agent_sets_ready"]
    assert "private-token" not in str(result)
    assert diagnostic({})["broker_status"] == "unknown"
