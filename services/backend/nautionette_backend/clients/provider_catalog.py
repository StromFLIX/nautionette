"""A provider's public model catalog, for integrations that publish one."""

from __future__ import annotations

from typing import Any

from .http import shared


class ModelCatalogClient:
    async def payload(self, url: str) -> dict[str, Any]:
        response = await shared().get(url, timeout=25)
        response.raise_for_status()
        return response.json()


model_catalog = ModelCatalogClient()
