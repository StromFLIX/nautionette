"""Private MCP transports; agents use local wrappers that inject the capability."""

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from .. import subagents
from ..security import require_user

router = APIRouter(dependencies=[Depends(require_user)])


class Capability(BaseModel):
    model_config = ConfigDict(extra="forbid")
    grant: Annotated[str, Field(min_length=32, max_length=128)]


class AgentCall(Capability):
    request_id: Annotated[str, Field(min_length=1, max_length=256)]
    agent_id: Annotated[str, Field(min_length=1, max_length=64)]
    text: Annotated[str, Field(min_length=1, max_length=32000)]
    chat_id: str | None = None


class AgentWait(Capability):
    call_id: Annotated[str, Field(min_length=1, max_length=64)]
    timeout_seconds: Annotated[int, Field(ge=0, le=20)] = 20


@router.post("/api/subagents/list")
async def list_callable_agents(body: Capability):
    """Private turn-scoped transport for list_subagents."""
    _, _, job = subagents.caller(body.grant, "list_subagents")
    return {
        "agents": subagents.available(job),
        "limits": {key: subagents.runtime(key) for key in subagents.LIMIT_KEYS},
    }


@router.post("/api/subagents/call")
async def call_subagent(body: AgentCall):
    """Private idempotent transport for call_subagent."""
    return await subagents.start(body.grant, body.request_id, body.agent_id, body.text, body.chat_id)


@router.post("/api/subagents/wait")
async def wait_for_subagent(body: AgentWait):
    """Private bounded long-poll transport for wait_for_subagent."""
    return await subagents.wait(body.grant, body.call_id, body.timeout_seconds)
