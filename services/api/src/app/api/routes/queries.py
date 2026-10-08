"""Query API routes.

POST /v1/queries — create query
GET /v1/queries/{queryId}/events — SSE stream
GET /v1/queries/{queryId} — final result
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sse_starlette.sse import EventSourceResponse

from app.api.dependencies.auth import get_authenticated_user
from app.auth.oidc import AuthenticatedUser

router = APIRouter()


class CreateQueryRequest(BaseModel):
    """Query creation request — must NOT contain tenant_id (INV-007)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    query: str = Field(min_length=1, max_length=2000)
    as_of: datetime | None = Field(default=None, alias="asOf")

    @field_validator("query")
    @classmethod
    def normalise_query(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Query must contain non-whitespace text")
        return cleaned

    @field_validator("as_of")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("asOf must include a timezone")
        return value


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
    asOf: str | None = None
    answer: str | None = None
    claims: list[dict] = Field(default_factory=list)
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
    from app.domain.query_execution import fail_query_execution
    from app.domain.query_service import create_query_run, enqueue_query_run

    query_run = await create_query_run(
        user_id=user.user_id,
        tenant_id=user.tenant_id,
        query_text=request.query,
        as_of=request.as_of,
    )
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
    from app.domain.query_service import ensure_query_access, stream_events

    await ensure_query_access(query_id, user)
    return EventSourceResponse(stream_events(query_id, user, after_sequence=last_event_id or 0))


@router.get("/queries/{query_id}", response_model=QueryResult)
async def get_query_result(
    query_id: UUID,
    user: AuthenticatedUser = Depends(get_authenticated_user),
) -> QueryResult:
    """Fetch the final query result with citations."""
    from app.domain.query_service import get_result

    return await get_result(query_id, user)  # type: ignore[return-value]
