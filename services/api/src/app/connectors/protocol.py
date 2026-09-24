"""Connector Protocol — the common interface all provider adapters implement."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.domain.types import (
    AccessCheckResult,
    ChangePage,
    DelegatedCredential,
    FetchAclResult,
    FetchObjectResult,
    ScanPage,
    Source,
    SourceConnection,
)


@runtime_checkable
class Connector(Protocol):
    """Common asynchronous protocol for all provider connectors.

    Each provider adapter implements this interface.
    Provider SDK types must not leak beyond the connector boundary.
    """

    source: Source

    async def initial_scan(
        self,
        connection: SourceConnection,
        cursor: str | None,
    ) -> ScanPage:
        """Perform an initial full scan of all accessible objects."""
        ...

    async def read_changes(
        self,
        connection: SourceConnection,
        cursor: str | None,
    ) -> ChangePage:
        """Read incremental changes since the cursor position."""
        ...

    async def fetch_object(
        self,
        connection: SourceConnection,
        external_object_id: str,
    ) -> FetchObjectResult:
        """Fetch a single object's content and metadata."""
        ...

    async def fetch_acl(
        self,
        connection: SourceConnection,
        external_object_id: str,
    ) -> FetchAclResult:
        """Fetch ACL grants for a single object."""
        ...

    async def check_access(
        self,
        user_credential: DelegatedCredential,
        external_object_id: str,
    ) -> AccessCheckResult:
        """Perform a live access check for a specific user and object.

        This is the authoritative access decision — query-scoped, never cached.
        """
        ...
