"""User-facing conversation search, separate from diagnostic chat inspection."""

from fastapi import APIRouter, Depends, Query

from ..chat_search import search
from ..security import require_user

router = APIRouter(prefix="/api/search", dependencies=[Depends(require_user)])


@router.get("/chats")
async def search_chats(
    q: str = Query(min_length=1, max_length=200),
    limit: int = Query(30, ge=1, le=50),
) -> dict:
    """Search titles and full saved conversation text using keyword and semantic ranks."""
    if not q.strip():
        return {"results": [], "semantic": {"state": search.state, "pending": 0}}
    return await search.search(q.strip(), limit)
