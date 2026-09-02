"""A provider's public model catalog, for integrations that publish one."""

from __future__ import annotations

from typing import Any

import httpx


class ModelCatalogClient:
    async def payload(self, url: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.get(url)
            response.raise_for_status()
            return response.json()


model_catalog = ModelCatalogClient()
