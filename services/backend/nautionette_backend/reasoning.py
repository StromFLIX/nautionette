"""Provider-advertised reasoning controls, not guesses based on model names.

Copilot exposes capabilities.supports.reasoning_effort; OpenRouter exposes
reasoning.supported_efforts. A reasoning boolean alone does not define levels.
None is a provider default, never an instruction to disable reasoning.
"""

from typing import Any

EFFORTS = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def reasoning_metadata(item: dict[str, Any], integration_type: str) -> dict[str, Any]:
    capabilities = item.get("capabilities")
    supports = capabilities.get("supports", {}) if isinstance(capabilities, dict) else {}
    supports = supports if isinstance(supports, dict) else {}
    reasoning = item.get("reasoning")
    reasoning = reasoning if isinstance(reasoning, dict) else {}
    advertised = supports.get("reasoning_effort", reasoning.get("supported_efforts"))
    efforts = [effort for effort in EFFORTS if isinstance(advertised, list) and effort in advertised]
    supported = bool(efforts) if isinstance(advertised, list) else None
    if supported is None:
        if type(supports.get("reasoning_effort")) is bool:
            supported = supports["reasoning_effort"]
        elif reasoning:
            supported = True
    return {
        "supports_reasoning": supported,
        "reasoning_efforts": efforts,
        "reasoning_format": "openrouter" if integration_type == "openrouter" else "openai",
    }


def validate_effort(value: Any, model: dict[str, Any]) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or value not in EFFORTS:
        raise ValueError("reasoning_effort must be null (provider default) or a supported effort level")
    if value not in model.get("reasoning_efforts", []):
        raise ValueError("This reasoning effort is not advertised for the selected model/API route")
    return value
