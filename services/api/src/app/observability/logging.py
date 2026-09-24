"""structlog JSON logger configuration.

Permitted fields: request_id, query_id, tenant_id, internal user ID,
source, external object ID, task ID, error code, status, latency,
candidate counts, queue name, retry count, provider request ID.

Forbidden: source content, chunk text, query plaintext, answer plaintext,
access token, refresh token, authorisation header, OAuth code, KMS material.
"""

from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

# Fields that must never appear in logs
_FORBIDDEN_PATTERNS = re.compile(
    r"(token|secret|password|credential|auth.*header|oauth.*code|kms.*key|refresh.*token)",
    re.IGNORECASE,
)


def _sanitise_value(value: Any) -> Any:
    """Redact forbidden fields."""
    if isinstance(value, str) and _FORBIDDEN_PATTERNS.search(value):
        return "[REDACTED]"
    return value


def _filter_sensitive_fields(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """Processor that strips sensitive fields from log events."""
    forbidden_keys = {
        "source_content",
        "chunk_text",
        "query_plaintext",
        "answer_plaintext",
        "access_token",
        "refresh_token",
        "authorisation_header",
        "authorization_header",
        "oauth_code",
        "kms_plaintext",
        "kms_key",
    }
    return {k: _sanitise_value(v) for k, v in event_dict.items() if k not in forbidden_keys}


def configure_logging() -> None:
    """Configure structlog JSON logging."""
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=logging.INFO,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _filter_sensitive_fields,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a configured structlog logger."""
    return structlog.get_logger(name)
