from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from .. import github_setup, projects
from ..security import require_user

router = APIRouter(dependencies=[Depends(require_user)])


class GitHubApp(BaseModel):
    app_id: str = Field(pattern=r"^[0-9]+$")
    installation_id: str = Field(pattern=r"^[0-9]+$")
    private_key: str = Field(default="", max_length=20000)


class Repository(BaseModel):
    full_name: str = Field(max_length=250)


@router.get("/api/projects/github-app")
async def project_app_status():
    return github_setup.status()


@router.put("/api/projects/github-app")
async def configure_project_app(payload: GitHubApp):
    return await projects.configure(payload.app_id, payload.installation_id, payload.private_key)


@router.get("/api/projects/repositories")
async def project_repositories(page: int = Query(default=1, ge=1, le=1000)):
    return await projects.repositories(page)


@router.get("/api/projects")
async def project_list():
    return {"projects": projects.list_projects()}


@router.post("/api/projects", status_code=202)
async def add_project(payload: Repository):
    return await projects.add_repository(payload.full_name)


@router.delete("/api/projects/{project_id}")
async def archive_project(project_id: str):
    projects.archive(project_id)
    return {"ok": True}
