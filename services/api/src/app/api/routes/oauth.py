"""OAuth callback routes for source identity linking.

GET  /v1/oauth/atlassian/start   — returns authorisation URL (requires Bearer token)
GET  /v1/oauth/atlassian/callback — handles provider redirect and persists tokens
GET  /v1/oauth/slack/start
GET  /v1/oauth/slack/callback
GET  /v1/oauth/google/start
GET  /v1/oauth/google/callback
GET  /v1/oauth/connections — list the current user's connected sources
GET  /v1/oauth/connections/previews — show recent authorised imported items
GET  /v1/oauth/connections/backfill-status — show current manual import progress
POST /v1/oauth/connections/{provider}/backfill — queue a manual source import

The OAuth flow works as follows:
1. Frontend calls /start with a Bearer token → gets an authoriseUrl
2. Browser navigates to authoriseUrl → user grants access on provider site
3. Provider redirects browser to /callback?code=...&state=...
   - state is a short-lived signed binding for the tenant and user
4. API exchanges code for tokens, persists to DB
5. API redirects browser back to frontend /settings?status=connected&source=...
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies.auth import get_authenticated_user
from app.auth.identities import find_identity_link
from app.auth.oauth_state import create_oauth_state, verify_oauth_state
from app.auth.oidc import AuthenticatedUser
from app.domain.types import Source
from app.observability.logging import get_logger
from app.persistence.models import (
    BackfillRun,
    IdentityLink,
    ObjectVersion,
    SourceConnection,
    SourceObject,
)
from app.persistence.session import get_session
from app.settings import get_settings

router = APIRouter()
logger = get_logger(__name__)
_PREVIEW_LIMIT = 3
_PREVIEW_SNIPPET_LENGTH = 160


class OAuthStartResponse(BaseModel):
    authoriseUrl: str


class ConnectionStatus(BaseModel):
    source: str
    connected: bool
    display_name: str | None = None


class ConnectionsResponse(BaseModel):
    connections: list[ConnectionStatus]


class BackfillRunStatus(BaseModel):
    id: str
    source: str
    status: str
    discoveredCount: int
    enqueuedCount: int
    processedCount: int
    failedCount: int
    errorMessage: str | None = None
    startedAt: str | None = None
    completedAt: str | None = None


class ManualBackfillResponse(BaseModel):
    queuedSources: list[str]
    message: str
    runs: list[BackfillRunStatus]


class BackfillStatusResponse(BaseModel):
    runs: list[BackfillRunStatus]


class BackfillPreviewItem(BaseModel):
    source: str
    title: str
    url: str | None = None
    updatedAt: str | None = None
    snippet: str | None = None


class AtlassianBackfillPreview(BaseModel):
    pages: list[BackfillPreviewItem]
    tickets: list[BackfillPreviewItem]


class BackfillPreviewResponse(BaseModel):
    atlassian: AtlassianBackfillPreview
    slack: list[BackfillPreviewItem]
    googleDrive: list[BackfillPreviewItem]


# ── Connection status ────────────────────────────────────────────────


@router.get("/connections", response_model=ConnectionsResponse)
async def list_connections(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: AsyncSession = Depends(get_session),
) -> ConnectionsResponse:
    """List the current user's connection status for all sources."""
    sources = [Source.CONFLUENCE, Source.JIRA, Source.SLACK, Source.GOOGLE_DRIVE]
    result_list: list[ConnectionStatus] = []

    for source in sources:
        # Check if user has a verified identity link for this source
        link = await find_identity_link(session, user.user_id, source)
        display_name = None
        if link is not None:
            stmt = select(SourceConnection).where(
                SourceConnection.id == link.source_connection_id
            )
            conn_result = await session.execute(stmt)
            conn = conn_result.scalar_one_or_none()
            if conn is not None:
                display_name = conn.display_name

        result_list.append(
            ConnectionStatus(
                source=source.value,
                connected=link is not None,
                display_name=display_name,
            )
        )

    return ConnectionsResponse(connections=result_list)


@router.get("/connections/previews", response_model=BackfillPreviewResponse)
async def get_backfill_previews(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: AsyncSession = Depends(get_session),
) -> BackfillPreviewResponse:
    """Return recent imported items after fresh provider authorisation checks."""
    pages = await _authorised_preview_items(session, user, Source.CONFLUENCE, "page")
    tickets = await _authorised_preview_items(session, user, Source.JIRA, "issue")
    slack = await _authorised_preview_items(session, user, Source.SLACK, "message")
    drive = await _authorised_preview_items(session, user, Source.GOOGLE_DRIVE)
    return BackfillPreviewResponse(
        atlassian=AtlassianBackfillPreview(pages=pages, tickets=tickets),
        slack=slack,
        googleDrive=drive,
    )


@router.get("/connections/backfill-status", response_model=BackfillStatusResponse)
async def get_backfill_status(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: AsyncSession = Depends(get_session),
) -> BackfillStatusResponse:
    """Return each authorised source's latest import requested by this user."""
    rows = await session.execute(
        select(BackfillRun, SourceConnection.source)
        .join(SourceConnection, SourceConnection.id == BackfillRun.source_connection_id)
        .join(IdentityLink, IdentityLink.source_connection_id == SourceConnection.id)
        .where(
            BackfillRun.tenant_id == user.tenant_id,
            BackfillRun.requested_by_user_id == user.user_id,
            IdentityLink.user_id == user.user_id,
            IdentityLink.status == "verified",
        )
        .order_by(BackfillRun.created_at.desc())
    )
    latest: dict[str, BackfillRunStatus] = {}
    for run, source in rows.all():
        source_value = str(source)
        if source_value in latest:
            continue
        latest[source_value] = _backfill_run_status(run, source_value)
    return BackfillStatusResponse(runs=list(latest.values()))


@router.post(
    "/connections/{provider}/backfill",
    response_model=ManualBackfillResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_manual_backfill(
    provider: str,
    user: AuthenticatedUser = Depends(get_authenticated_user),
    session: AsyncSession = Depends(get_session),
) -> ManualBackfillResponse:
    """Queue an operator-requested import using the caller's delegated identity."""
    provider_sources = {
        "atlassian": (Source.CONFLUENCE, Source.JIRA),
        "slack": (Source.SLACK,),
        "google_drive": (Source.GOOGLE_DRIVE,),
    }
    sources = provider_sources.get(provider)
    if sources is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "SOURCE_NOT_FOUND", "message": "Source not found."}},
        )

    rows = await session.execute(
        select(SourceConnection)
        .join(IdentityLink, IdentityLink.source_connection_id == SourceConnection.id)
        .where(
            SourceConnection.tenant_id == user.tenant_id,
            SourceConnection.status == "connected",
            SourceConnection.source.in_([source.value for source in sources]),
            IdentityLink.user_id == user.user_id,
            IdentityLink.status == "verified",
        )
        .order_by(SourceConnection.source)
    )
    connections = list(rows.scalars().unique())
    if not connections:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "error": {
                    "code": "SOURCE_NOT_CONNECTED",
                    "message": "Reconnect this source before starting an import.",
                }
            },
        )

    runs = [
        BackfillRun(
            tenant_id=user.tenant_id,
            source_connection_id=connection.id,
            requested_by_user_id=user.user_id,
        )
        for connection in connections
    ]
    session.add_all(runs)
    await session.commit()

    from worker.tasks.backfill import run_backfill

    try:
        for connection, run in zip(connections, runs, strict=True):
            run_backfill.delay(  # type: ignore[union-attr]
                str(connection.id), str(user.tenant_id), str(user.user_id), str(run.id)
            )
    except Exception as exc:
        logger.exception("Manual backfill could not be queued", provider=provider)
        for run in runs:
            run.status = "failed"
            run.error_message = "The import worker is unavailable. Try again shortly."
        await session.commit()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": {
                    "code": "BACKFILL_UNAVAILABLE",
                    "message": "The import worker is unavailable. Try again shortly.",
                }
            },
        ) from exc

    queued_sources = [str(connection.source) for connection in connections]
    return ManualBackfillResponse(
        queuedSources=queued_sources,
        message="Import queued. Progress will update here while Relay indexes the source.",
        runs=[
            _backfill_run_status(run, str(connection.source))
            for connection, run in zip(connections, runs, strict=True)
        ],
    )


def _backfill_run_status(run: BackfillRun, source: str) -> BackfillRunStatus:
    return BackfillRunStatus(
        id=str(run.id),
        source=source,
        status=run.status,
        discoveredCount=run.discovered_count,
        enqueuedCount=run.enqueued_count,
        processedCount=run.processed_count,
        failedCount=run.failed_count,
        errorMessage=_friendly_backfill_error(run.error_message),
        startedAt=run.started_at.isoformat() if run.started_at else None,
        completedAt=run.completed_at.isoformat() if run.completed_at else None,
    )


def _friendly_backfill_error(value: str | None) -> str | None:
    if value == "SOURCE_CREDENTIAL_EXPIRED":
        return "Authorisation expired. Reconnect this source, then sync again."
    return value


async def _authorised_preview_items(
    session: AsyncSession,
    user: AuthenticatedUser,
    source: Source,
    object_type: str | None = None,
) -> list[BackfillPreviewItem]:
    """Load recent metadata and reveal it only after a live access check."""
    conditions = [
        SourceObject.tenant_id == user.tenant_id,
        SourceObject.source == source.value,
        SourceObject.is_queryable.is_(True),
        SourceObject.is_deleted.is_(False),
        SourceObject.current_version_id == ObjectVersion.id,
        ObjectVersion.ingestion_status == "active",
        ObjectVersion.retired_at.is_(None),
    ]
    if object_type is not None:
        conditions.append(SourceObject.object_type == object_type)
    if source == Source.GOOGLE_DRIVE:
        conditions.append(SourceObject.object_type != "application/vnd.google-apps.folder")

    rows = (
        await session.execute(
            select(
                SourceObject.id,
                SourceObject.canonical_url,
                ObjectVersion.normalised_title,
                ObjectVersion.normalised_content,
                ObjectVersion.valid_from,
                ObjectVersion.observed_at,
            )
            .join(ObjectVersion, SourceObject.current_version_id == ObjectVersion.id)
            .where(*conditions)
            .order_by(func.coalesce(ObjectVersion.valid_from, ObjectVersion.observed_at).desc())
            .limit(_PREVIEW_LIMIT)
        )
    ).all()
    if not rows:
        return []

    from app.authorisation.live_check import check_live_access_batch

    decisions = await check_live_access_batch(
        session,
        user,
        [(row.id, source) for row in rows],
    )
    items: list[BackfillPreviewItem] = []
    for row in rows:
        decision = decisions.get(row.id)
        if decision is None or decision.outcome.value != "allow":
            continue
        title = row.normalised_title or _preview_title(source, row.normalised_content)
        updated_at = row.valid_from or row.observed_at
        items.append(
            BackfillPreviewItem(
                source=source.value,
                title=title,
                url=row.canonical_url,
                updatedAt=updated_at.isoformat() if updated_at else None,
                snippet=(
                    _preview_snippet(row.normalised_content)
                    if source == Source.SLACK
                    else None
                ),
            )
        )
        if len(items) == _PREVIEW_LIMIT:
            break
    return items


def _preview_title(source: Source, content: str) -> str:
    if source == Source.SLACK:
        return "Slack message"
    preview = _preview_snippet(content)
    return preview or "Untitled item"


def _preview_snippet(content: str) -> str:
    compact = " ".join(content.split())
    if len(compact) <= _PREVIEW_SNIPPET_LENGTH:
        return compact
    return f"{compact[:_PREVIEW_SNIPPET_LENGTH - 3].rstrip()}…"


# ── Atlassian ────────────────────────────────────────────────────────


@router.get("/atlassian/start", response_model=OAuthStartResponse)
async def start_atlassian_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    authorization: str | None = Header(default=None),
) -> OAuthStartResponse:
    """Initiate Atlassian OAuth flow for source identity linking."""
    from app.connectors.confluence import start_atlassian_oauth as start

    oauth_state = create_oauth_state(user.user_id, user.tenant_id)
    url = await start(user, oauth_state=oauth_state)
    return OAuthStartResponse(authoriseUrl=url)


@router.get("/atlassian/callback")
async def atlassian_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Complete Atlassian OAuth callback — redirects to frontend settings."""
    from app.connectors.confluence import complete_atlassian_oauth

    user = await _resolve_user_from_state(state)
    try:
        await complete_atlassian_oauth(code, state, user)
    except ValueError as exc:
        error_messages = {
            "ATLASSIAN_IDENTITY_UNAVAILABLE": (
                "Atlassian identity could not be verified. Enable the User Identity API "
                "and read:me, then reconnect."
            ),
            "IDENTITY_LINK_ALREADY_CLAIMED": (
                "This Atlassian account is already linked to another Relay user."
            ),
            "IDENTITY_LINK_SUBJECT_MISMATCH": (
                "This Relay user already has a different Atlassian identity linked."
            ),
        }
        error_code = str(exc)
        logger.warning("Atlassian OAuth callback failed", error=error_code)
        return _redirect_to_settings(
            "error", "atlassian", error_messages.get(error_code, "OAuth connection failed")
        )
    except Exception as exc:
        logger.warning("Atlassian OAuth callback failed", error=str(exc))
        return _redirect_to_settings("error", "atlassian", "OAuth connection failed")

    return _redirect_to_settings("connected", "atlassian")


# ── Slack ────────────────────────────────────────────────────────────


@router.get("/slack/start", response_model=OAuthStartResponse)
async def start_slack_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    authorization: str | None = Header(default=None),
) -> OAuthStartResponse:
    """Initiate Slack OAuth flow."""
    from app.connectors.slack import start_slack_oauth as start

    oauth_state = create_oauth_state(user.user_id, user.tenant_id)
    url = await start(user, oauth_state=oauth_state)
    return OAuthStartResponse(authoriseUrl=url)


@router.get("/slack/callback")
async def slack_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Complete Slack OAuth callback — redirects to frontend settings."""
    from app.connectors.slack import complete_slack_oauth

    user = await _resolve_user_from_state(state)
    try:
        await complete_slack_oauth(code, state, user)
    except Exception as exc:
        logger.warning("Slack OAuth callback failed", error=str(exc))
        return _redirect_to_settings("error", "slack", "OAuth connection failed")

    return _redirect_to_settings("connected", "slack")


# ── Google Drive ─────────────────────────────────────────────────────


@router.get("/google/start", response_model=OAuthStartResponse)
async def start_google_oauth(
    user: AuthenticatedUser = Depends(get_authenticated_user),
    authorization: str | None = Header(default=None),
) -> OAuthStartResponse:
    """Initiate Google OAuth flow."""
    from app.connectors.google_drive import start_google_oauth as start

    oauth_state = create_oauth_state(user.user_id, user.tenant_id)
    url = await start(user, oauth_state=oauth_state)
    return OAuthStartResponse(authoriseUrl=url)


@router.get("/google/callback")
async def google_callback(
    code: str = Query(...),
    state: str = Query(...),
) -> RedirectResponse:
    """Complete Google OAuth callback — redirects to frontend settings."""
    from app.connectors.google_drive import complete_google_oauth

    user = await _resolve_user_from_state(state)
    try:
        await complete_google_oauth(code, state, user)
    except ValueError as exc:
        error_messages = {
            "GOOGLE_IDENTITY_UNAVAILABLE": (
                "Google identity could not be verified. Please grant the requested "
                "identity permissions and reconnect."
            ),
            "IDENTITY_LINK_ALREADY_CLAIMED": (
                "This Google account is already linked to another Relay user."
            ),
            "IDENTITY_LINK_SUBJECT_MISMATCH": (
                "This Relay user already has a different Google identity linked."
            ),
        }
        error_code = str(exc)
        logger.warning("Google OAuth callback failed", error=error_code)
        return _redirect_to_settings(
            "error", "google", error_messages.get(error_code, "OAuth connection failed")
        )
    except Exception as exc:
        logger.warning("Google OAuth callback failed", error=str(exc))
        return _redirect_to_settings("error", "google", "OAuth connection failed")

    return _redirect_to_settings("connected", "google")


# ── Helpers ──────────────────────────────────────────────────────────


async def _resolve_user_from_state(state: str) -> AuthenticatedUser:
    """Resolve the user from the OAuth state parameter.

    The state is a short-lived signed binding containing only internal IDs.
    It is never a bearer token and cannot be used as an API credential.
    """
    try:
        tenant_id, user_id = verify_oauth_state(state)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "AUTH_INVALID_TOKEN", "message": "Invalid OAuth state"}},
        ) from exc

    from app.persistence.models import Tenant, User
    from app.persistence.session import get_session_factory, set_tenant_context

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        row = (
            await session.execute(
                select(User, Tenant).join(Tenant, User.tenant_id == Tenant.id).where(
                    User.id == user_id, User.tenant_id == tenant_id
                )
            )
        ).first()
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": {"code": "AUTH_INVALID_TOKEN", "message": "Invalid OAuth state"}},
            )
        user, _tenant = row
        return AuthenticatedUser(
            user_id=user.id,
            tenant_id=user.tenant_id,
            oidc_subject=user.oidc_subject,
            display_name=user.display_name,
            email=user.email,
            is_compliance_user=user.is_compliance_user,
        )


def _redirect_to_settings(
    status_val: str, source: str, error: str | None = None
) -> RedirectResponse:
    """Redirect the browser back to the frontend settings page."""
    settings = get_settings()
    base = settings.WEB_BASE_URL.rstrip("/")
    params = f"?status={status_val}&source={source}"
    if error:
        import urllib.parse
        params += f"&error={urllib.parse.quote(error)}"
    return RedirectResponse(url=f"{base}/settings{params}")
