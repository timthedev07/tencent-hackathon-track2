"""Jira connector — OAuth flows and connector implementation."""

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


async def start_jira_oauth(user: AuthenticatedUser) -> str:
    """Initiate Jira OAuth flow (uses Atlassian OAuth)."""
    from app.connectors.confluence import start_atlassian_oauth

    return await start_atlassian_oauth(user)


async def complete_jira_oauth(code: str, state: str, user: AuthenticatedUser) -> None:
    """Complete Jira OAuth callback (uses Atlassian OAuth)."""
    from app.connectors.confluence import complete_atlassian_oauth

    await complete_atlassian_oauth(code, state, user)


class JiraConnector:
    """Jira connector implementing the Connector protocol."""

    source = Source.JIRA

    async def initial_scan(self, connection: SourceConnection, cursor: str | None) -> ScanPage:
        return ScanPage(objects=[], next_cursor=None)

    async def read_changes(self, connection: SourceConnection, cursor: str | None) -> ChangePage:
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
