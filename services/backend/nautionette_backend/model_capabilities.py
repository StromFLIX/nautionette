"""Image support belongs to the serving integration, model and selected wire API.

Keep Copilot API selection in sync with the agent's model-api.ts fallback. Chat
jobs pin this selection so capability reporting and execution cannot diverge.
Provider declarations are not a successful end-to-end vision probe.
"""

from __future__ import annotations

import re
from typing import Any

COMPLETIONS = "openai-completions"
RESPONSES = "openai-responses"
ENDPOINTS = {
    COMPLETIONS: {"/chat/completions", "/v1/chat/completions"},
    RESPONSES: {"/responses", "/v1/responses"},
}


def model_capabilities(model_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
    copilot = model_id.startswith("copilot/")
    version = re.match(r"^copilot/gpt-(\d+)(?:[.\-]|$)", model_id)
    api = RESPONSES if version and int(version[1]) >= 5 else COMPLETIONS
    endpoints = metadata.get("supported_endpoints")
    advertised = isinstance(endpoints, list) and bool(endpoints)
    route_support: bool | None = None if copilot else True
    if advertised:
        if copilot:
            api = next(
                (
                    candidate
                    for candidate in (RESPONSES, COMPLETIONS)
                    if any(endpoint in endpoints for endpoint in ENDPOINTS[candidate])
                ),
                api,
            )
        route_support = any(endpoint in endpoints for endpoint in ENDPOINTS[api])

    vision = metadata.get("supports_images")
    if vision is False:
        support, reason = False, "The provider marks this model as text-only."
    elif route_support is False:
        support, reason = False, "The provider does not advertise a compatible image API route."
    elif vision is True and route_support is True:
        support, reason = True, "The provider advertises image input for the selected API route (not probed)."
    else:
        support, reason = None, "Image support is unverified: model or API capability metadata is missing."
    return {
        "api": api,
        "api_source": "advertised" if advertised and route_support else "fallback",
        "supports_images": support,
        "image_support_reason": reason,
    }
