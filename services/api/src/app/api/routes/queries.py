"""Query API routes.

POST /v1/queries — create query
GET /v1/queries/{queryId}/events — SSE stream
GET /v1/queries/{queryId} — final result
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from app.api.dependencies.auth import get_authenticated_user
from app.auth.oidc import AuthenticatedUser

router = APIRouter()


class CreateQueryRequest(BaseModel):
    """Query creation request — must NOT contain tenant_id (INV-007)."""

    query: str = Field(min_length=1, max_length=2000)


class CreateQueryResponse(BaseModel):
    queryId: str
    status: str = "created"


class Citation(BaseModel):
    evidenceId: str
    source: str
    label: str | None = None
    url: str | None = None


class QueryResult(BaseModel):
    queryId: str
    status: str
    answer: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    partialSourceWarnings: list[dict] = Field(default_factory=list)


@router.post("/queries", response_model=CreateQueryResponse, status_code=status.HTTP_201_CREATED)
async def create_query(
    request: CreateQueryRequest,
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> CreateQueryResponse:
    """Create a new query.

    INV-007: The request must not contain tenant_id, source_user_id,
    provider_identity, or permission claims.
    """
    # Import here to avoid circular dependency during scaffold phase
    from app.domain.query_service import create_query_run

    query_run = await create_query_run(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        query_text=request.query,
    )
    return CreateQueryResponse(queryId=str(query_run["id"]), status="created")


@router.get("/queries/{query_id}/events")
async def stream_query_events(
    query_id: UUID,
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> EventSourceResponse:
    """SSE stream for query progress events."""
    from app.domain.query_service import stream_events

    return EventSourceResponse(stream_events(query_id, user))


@router.get("/queries/{query_id}", response_model=QueryResult)
async def get_query_result(
    query_id: UUID,
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> QueryResult:
    """Fetch the final query result with citations."""
    from app.domain.query_service import get_result

    return await get_result(query_id, user)  # type: ignore[return-value]
