"""Jira connector — OAuth flows and connector implementation."""

from __future__ import annotations

from datetime import datetime

from app.auth.oidc import AuthenticatedUser
from app.connectors.http import json_body, request
from app.connectors.parsing import flatten_adf, strip_html
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

    async def initial_scan(
        self,
        connection: SourceConnection,
        cursor: str | None,
        credential: DelegatedCredential | None = None,
    ) -> ScanPage:
        credential = _require_credential(credential)
        response = await request(
            "GET",
            _api_url(connection, credential, "/rest/api/3/search/jql"),
            access_token=credential.access_token,
            params={
                "jql": "ORDER BY updated ASC",
                "nextPageToken": cursor or "",
                "maxResults": 100,
                "fields": "summary,updated,project,issuetype,parent",
            },
        )
        payload = json_body(response)
        issues = payload.get("issues", [])
        objects = [_scan_object(item) for item in issues if isinstance(item, dict)]
        return ScanPage(
            objects=objects,
            next_cursor=(
                str(payload["nextPageToken"])
                if payload.get("nextPageToken")
                else None
            ),
        )

    async def read_changes(
        self,
        connection: SourceConnection,
        cursor: str | None,
        credential: DelegatedCredential | None = None,
    ) -> ChangePage:
        page = await self.initial_scan(connection, cursor, credential)
        return ChangePage(changes=page.objects, next_cursor=page.next_cursor)

    async def fetch_object(
        self,
        connection: SourceConnection,
        external_object_id: str,
        credential: DelegatedCredential | None = None,
    ) -> FetchObjectResult:
        credential = _require_credential(credential)
        response = await request(
            "GET",
            _api_url(connection, credential, f"/rest/api/3/issue/{external_object_id}"),
            access_token=credential.access_token,
            params={
                "fields": "summary,description,comment,project,issuetype,parent,updated,security",
            },
        )
        payload = json_body(response)
        fields = payload.get("fields") if isinstance(payload.get("fields"), dict) else {}
        comments = fields.get("comment") if isinstance(fields.get("comment"), dict) else {}
        comment_text = []
        for comment in comments.get("comments", []):
            if isinstance(comment, dict):
                comment_text.append(flatten_adf(comment.get("body")))
        description = flatten_adf(fields.get("description"))
        content = strip_html(
            "\n\n".join(part for part in [description, *comment_text] if part)
        )
        issue_links = payload.get("self")
        web_link = (
            payload.get("_links", {}).get("web")
            if isinstance(payload.get("_links"), dict)
            else None
        )
        updated = fields.get("updated")
        return FetchObjectResult(
            outcome=ConnectorOutcome.SUCCESS,
            external_object_id=external_object_id,
            source_revision=str(updated or payload.get("id") or external_object_id),
            object_type="issue",
            parent_external_id=_parent_id(fields.get("parent")),
            canonical_url=str(web_link or issue_links) if (web_link or issue_links) else None,
            title=str(fields.get("summary")) if fields.get("summary") else external_object_id,
            content=content,
            last_modified_at=_parse_datetime(updated),
            provider_metadata={
                "project": _project_key(fields.get("project")),
                "security_level": fields.get("security"),
            },
        )

    async def fetch_acl(
        self,
        connection: SourceConnection,
        external_object_id: str,
        credential: DelegatedCredential | None = None,
    ) -> FetchAclResult:
        credential = _require_credential(credential)
        response = await request(
            "GET",
            _api_url(connection, credential, f"/rest/api/3/issue/{external_object_id}"),
            access_token=credential.access_token,
            params={"fields": "security"},
        )
        json_body(response)
        # Jira issue security levels are not group identities.  Do not project
        # a security-level ID into a local allow grant; live Jira access remains
        # authoritative for this provider-specific restriction.
        return FetchAclResult(outcome=ConnectorOutcome.SUCCESS)

    async def check_access(  # noqa: PLR0911
        self, user_credential: DelegatedCredential, external_object_id: str
    ) -> AccessCheckResult:
        """Authoritatively check Jira issue visibility with a delegated token."""
        import httpx

        cloud_id = user_credential.provider_metadata.get("cloud_id")
        if not cloud_id:
            return AccessCheckResult(outcome=AccessOutcome.UNKNOWN, reason_code="cloud_id_missing")

        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"https://api.atlassian.com/ex/jira/{cloud_id}/rest/api/3/issue/{external_object_id}",
                    headers={"Authorization": f"Bearer {user_credential.access_token}"},
                    params={"fields": "summary,updated,project,issuetype,security"},
                )
        except httpx.TimeoutException:
            return AccessCheckResult(outcome=AccessOutcome.TIMEOUT, reason_code="provider_timeout")
        except httpx.HTTPError:
            return AccessCheckResult(outcome=AccessOutcome.UNKNOWN, reason_code="provider_error")

        request_id = response.headers.get("x-arequestid") or response.headers.get("x-request-id")
        if response.status_code == 200:
            return AccessCheckResult(
                outcome=AccessOutcome.ALLOW,
                reason_code="provider_allow",
                provider_request_id=request_id,
            )
        if response.status_code == 401:
            return AccessCheckResult(
                outcome=AccessOutcome.EXPIRED_CREDENTIAL,
                reason_code="credential_expired",
                provider_request_id=request_id,
            )
        if response.status_code in {403, 404}:
            return AccessCheckResult(
                outcome=AccessOutcome.DENY,
                reason_code="object_not_found_or_forbidden",
                provider_request_id=request_id,
            )
        if response.status_code == 429:
            return AccessCheckResult(
                outcome=AccessOutcome.TIMEOUT,
                reason_code="provider_rate_limited",
                provider_request_id=request_id,
            )
        return AccessCheckResult(
            outcome=AccessOutcome.UNKNOWN,
            reason_code=f"provider_status_{response.status_code}",
            provider_request_id=request_id,
        )


def _require_credential(credential: DelegatedCredential | None) -> DelegatedCredential:
    if credential is None or not credential.access_token:
        raise ValueError("SOURCE_CREDENTIAL_EXPIRED")
    return credential


def _api_url(
    connection: SourceConnection,
    credential: DelegatedCredential,
    path: str,
) -> str:
    cloud_id = str(credential.provider_metadata.get("cloud_id") or connection.external_workspace_id)
    return f"https://api.atlassian.com/ex/jira/{cloud_id}{path}"


def _scan_object(item: dict[str, object]) -> dict[str, object]:
    fields = item.get("fields") if isinstance(item.get("fields"), dict) else {}
    return {
        "external_object_id": str(item.get("key") or item.get("id")),
        "object_type": "issue",
        "parent_external_id": _parent_id(fields.get("parent")),
        "canonical_url": _web_url(item),
        "title": str(fields.get("summary")) if fields.get("summary") else None,
        "source_revision": str(fields.get("updated") or item.get("id")),
    }


def _parent_id(value: object) -> str | None:
    return str(value.get("key") or value.get("id")) if isinstance(value, dict) else None


def _project_key(value: object) -> str | None:
    return str(value.get("key")) if isinstance(value, dict) and value.get("key") else None


def _web_url(item: dict[str, object]) -> str | None:
    links = item.get("_links")
    if not isinstance(links, dict) or not links.get("web"):
        return None
    return str(links["web"])


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
