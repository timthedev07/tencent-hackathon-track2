"""Typed connector errors — every operation resolves to a domain outcome."""

from __future__ import annotations

from app.domain.types import ConnectorOutcome


class ConnectorError(Exception):
    """Base exception for all connector errors."""

    def __init__(self, outcome: ConnectorOutcome, message: str) -> None:
        self.outcome = outcome
        self.message = message
        super().__init__(f"{outcome.value}: {message}")


class NotFoundError(ConnectorError):
    def __init__(self, message: str = "Object not found") -> None:
        super().__init__(ConnectorOutcome.NOT_FOUND, message)


class ForbiddenError(ConnectorError):
    def __init__(self, message: str = "Access forbidden") -> None:
        super().__init__(ConnectorOutcome.FORBIDDEN, message)


class RateLimitedError(ConnectorError):
    def __init__(
        self, message: str = "Rate limited by provider", retry_after: int | None = None
    ) -> None:
        self.retry_after = retry_after
        super().__init__(ConnectorOutcome.RATE_LIMITED, message)


class TransientError(ConnectorError):
    def __init__(self, message: str = "Transient provider error") -> None:
        super().__init__(ConnectorOutcome.TRANSIENT_ERROR, message)


class PermanentError(ConnectorError):
    def __init__(self, message: str = "Permanent provider error") -> None:
        super().__init__(ConnectorOutcome.PERMANENT_ERROR, message)
