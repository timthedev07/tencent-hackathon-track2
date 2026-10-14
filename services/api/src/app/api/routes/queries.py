"""Query API routes.

POST /v1/queries — create query
GET /v1/queries/{queryId}/events — SSE stream
GET /v1/queries/{queryId} — final result
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sse_starlette.event import JSONServerSentEvent
from sse_starlette.sse import EventSourceResponse

from app.api.dependencies.auth import get_authenticated_user
from app.auth.oidc import AuthenticatedUser
from app.observability.logging import bind_context

router = APIRouter()


async def _json_query_events(events: AsyncIterator[dict]) -> AsyncIterator[JSONServerSentEvent]:
    """Encode query event payloads as JSON on the SSE wire."""
    async for item in events:
        yield JSONServerSentEvent(
            event=item["event"],
            data=item["data"],
            id=str(item["id"]) if item.get("id") is not None else None,
        )


class CreateQueryRequest(BaseModel):
    """Query creation request — must NOT contain tenant_id (INV-007)."""

    model_config = ConfigDict(extra="forbid")

    query: str = Field(min_length=1, max_length=2000)

    @field_validator("query")
    @classmethod
    def normalise_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Query must contain non-whitespace text")
        return cleaned


class CreateQueryResponse(BaseModel):
    queryId: str
    status: str = "created"


class Citation(BaseModel):
    evidenceId: str
    referenceId: str | None = None
    itemKey: str | None = None
    source: str
    label: str | None = None
    url: str | None = None


class QueryResult(BaseModel):
    queryId: str
    status: str
    answer: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    partialSourceWarnings: list[dict] = Field(default_factory=list)
    errorCode: str | None = None
    errorMessage: str | None = None


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
    from app.domain.query_execution import fail_query_execution
    from app.domain.query_service import create_query_run, enqueue_query_run

    query_run = await create_query_run(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        query_text=request.query,
    )
    bind_context(query_id=query_run["id"])
    try:
        enqueue_query_run(query_run["id"], user.tenant_id, user.user_id)
    except Exception:
        await fail_query_execution(
            query_run["id"],
            user.tenant_id,
            error_code="QUERY_ENQUEUE_FAILED",
            message="The query could not be queued.",
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "QUERY_ENQUEUE_FAILED",
                    "message": "The query could not be queued. Please try again.",
                }
            },
        ) from None
    return CreateQueryResponse(queryId=str(query_run["id"]), status=query_run["status"])


@router.get("/queries/{query_id}/events")
async def stream_query_events(
    query_id: UUID,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    last_event_id: int | None = Header(default=None, alias="Last-Event-ID"),
) -> EventSourceResponse:
    """SSE stream for query progress events."""
    bind_context(query_id=query_id)
    from app.domain.query_service import ensure_query_access, stream_events

    await ensure_query_access(query_id, user)
    return EventSourceResponse(
        _json_query_events(stream_events(query_id, user, after_sequence=last_event_id or 0))
    )


@router.get("/queries/{query_id}", response_model=QueryResult)
async def get_query_result(
    query_id: UUID,
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> QueryResult:
    """Fetch the final query result with citations."""
    from app.domain.query_service import get_result

    return await get_result(query_id, user)  # type: ignore[return-value]
