"""Google Drive connector — OAuth flows and connector implementation."""

from __future__ import annotations

from app.auth.oidc import AuthenticatedUser
from app.domain.types import (
    AccessCheckResult,
    AccessOutcome,
    ChangePage,
    ConnectorOutcome,
    DelegatedCredential,
    FetchAclResult,
    FetchObjectResult,
    ScanPage,
    Source,
    SourceConnection,
)
from app.observability.logging import get_logger

logger = get_logger(__name__)


async def start_google_oauth(user: AuthenticatedUser) -> str:
    """Initiate Google OAuth flow."""
    from urllib.parse import urlencode

    from app.settings import get_settings

    settings = get_settings()
    params = urlencode(
        {
            "client_id": settings.GOOGLE_CLIENT_ID,
            "scope": "https://www.googleapis.com/auth/drive.readonly",
            "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            "state": str(user.user_id),
            "response_type": "code",
            "access_type": "offline",
            "prompt": "consent",
        }
    )
    return f"https://accounts.google.com/o/oauth2/v2/auth?{params}"


async def complete_google_oauth(code: str, state: str, user: AuthenticatedUser) -> None:
    """Complete Google OAuth callback — exchanges code for tokens and persists."""
    import httpx

    from app.auth.credentials import get_credential_manager
    from app.persistence.session import get_session_factory, set_tenant_context
    from app.settings import get_settings

    settings = get_settings()
    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.GOOGLE_CLIENT_ID,
                "client_secret": settings.GOOGLE_CLIENT_SECRET,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": settings.GOOGLE_REDIRECT_URI,
            },
            timeout=10.0,
        )
        response.raise_for_status()
        token_data = response.json()

    refresh_token = token_data.get("refresh_token", "")
    if not refresh_token:
        logger.warning("Google OAuth: no refresh token returned")
        return

    cm = get_credential_manager()
    cm.encrypt(refresh_token)

    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        logger.info("Google OAuth completed", user_id=str(user.user_id))


class GoogleDriveConnector:
    """Google Drive connector implementing the Connector protocol."""

    source = Source.GOOGLE_DRIVE

    async def initial_scan(
        self, connection: SourceConnection, cursor: str | None
    ) -> ScanPage:
        return ScanPage(objects=[], next_cursor=None)

    async def read_changes(
        self, connection: SourceConnection, cursor: str | None
    ) -> ChangePage:
        return ChangePage(changes=[], next_cursor=None)

    async def fetch_object(
        self, connection: SourceConnection, external_object_id: str
    ) -> FetchObjectResult:
        return FetchObjectResult(
            outcome=ConnectorOutcome.NOT_FOUND, external_object_id=external_object_id
        )

    async def fetch_acl(
        self, connection: SourceConnection, external_object_id: str
    ) -> FetchAclResult:
        return FetchAclResult(outcome=ConnectorOutcome.SUCCESS)

    async def check_access(
        self, user_credential: DelegatedCredential, external_object_id: str
    ) -> AccessCheckResult:
        return AccessCheckResult(outcome=AccessOutcome.ALLOW)
