"""The UI capability report and agent API fallback use the same routing cases."""

import json
from pathlib import Path

import pytest
from nautionette_backend.model_capabilities import model_capabilities

CASES = json.loads((Path(__file__).parents[1] / "agent/model_api_cases.json").read_text())


@pytest.mark.parametrize("case", CASES)
def test_api_and_effective_images(case):
    result = model_capabilities(
        case["model"], {"supported_endpoints": case["endpoints"], "supports_images": True}
    )
    assert result["api"] == case["api"]
    assert result["supports_images"] is case["images"]
    assert result["image_support_reason"]


@pytest.mark.parametrize("case", CASES)
def test_text_only_is_never_upgraded_by_an_image_capable_api(case):
    result = model_capabilities(
        case["model"], {"supported_endpoints": case["endpoints"], "supports_images": False}
    )
    assert result["supports_images"] is False
    assert "text-only" in result["image_support_reason"]


@pytest.mark.parametrize(
    "endpoints,expected", [(["/responses"], None), (["/v1/messages"], False), (None, None)]
)
def test_unknown_vision_is_not_certified_by_api_metadata(endpoints, expected):
    result = model_capabilities("copilot/claude", {"supported_endpoints": endpoints})
    assert result["supports_images"] is expected


def test_non_copilot_advertised_incompatible_route_is_not_certified():
    result = model_capabilities(
        "local/vision", {"supported_endpoints": ["/responses"], "supports_images": True}
    )
    assert result["api"] == "openai-completions"
    assert result["supports_images"] is False
    assert "API route" in result["image_support_reason"]
