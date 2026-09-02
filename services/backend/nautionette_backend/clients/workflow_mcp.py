"""REST side of workflow-mcp. The MCP side is what agents use."""

from __future__ import annotations

from typing import Any

from ..config import settings
from .http import internal_headers, shared


class AuthoringClient:
    def __init__(self, base_url: str | None = None) -> None:
        self.base_url = (base_url or settings.workflow_mcp_url).rstrip("/")

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await shared().request(
            method, f"{self.base_url}{path}", headers=internal_headers(), timeout=60, **kwargs
        )
        if response.status_code >= 400:
            detail = response.text[:500]
            raise RuntimeError(
                f"workflow-mcp {method} {path} failed ({response.status_code}): {detail}"
            )
        return response.json()

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/healthz")

    async def list_workflows(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/workflows"))["workflows"]

    async def get_workflow(self, name: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/workflows/{name}")

    async def list_drafts(self) -> list[dict[str, Any]]:
        return (await self._request("GET", "/api/drafts"))["drafts"]

    async def get_draft(self, name: str) -> dict[str, Any]:
        return await self._request("GET", f"/api/drafts/{name}")

    async def validate(self, name: str, code: str) -> dict[str, Any]:
        return await self._request("POST", "/api/validate", json={"name": name, "code": code})

    async def write_draft(self, name: str, code: str, message: str = "") -> dict[str, Any]:
        return await self._request(
            "POST", "/api/drafts", json={"name": name, "code": code, "message": message}
        )

    async def publish(self, name: str) -> dict[str, Any]:
        return await self._request("POST", f"/api/drafts/{name}/publish")

    async def discard(self, name: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/api/drafts/{name}")

    async def delete_workflow(self, name: str) -> dict[str, Any]:
        return await self._request("DELETE", f"/api/workflows/{name}")


authoring = AuthoringClient()
