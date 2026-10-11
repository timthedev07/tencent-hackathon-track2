"""Safe, structured logging for the API and worker processes.

Every log line has a timestamp, level, logger, service and environment. HTTP
requests additionally carry a request ID, while query and worker code can bind
query/task/tenant context through ``structlog.contextvars``.

The security boundary is deliberate: source text, query text, answer text,
credentials and authentication material are removed before rendering.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping
from typing import Any

import structlog

from app.settings import get_settings

_FORBIDDEN_KEYS = {
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
_SENSITIVE_KEY_PATTERN = re.compile(
    r"(?:token|secret|password|credential|authorization|authorisation|oauth.*code|kms.*key)",
    re.IGNORECASE,
)
_SECRET_VALUE_PATTERN = re.compile(
    r"(?:Bearer\s+\S+|xox[baprs]-[A-Za-z0-9-]+|eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)",
    re.IGNORECASE,
)
_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_REDACTED = "[REDACTED]"

_runtime_context: dict[str, str] = {}


def request_id_from_header(value: str | None) -> str:
    """Accept a safe caller request ID or create a new one."""
    from uuid import uuid4

    if value and _REQUEST_ID_PATTERN.fullmatch(value):
        return value
    return str(uuid4())


def bind_context(**values: object) -> None:
    """Bind safe request/task context to all logs in the current context."""
    structlog.contextvars.bind_contextvars(
        **{key: str(value) for key, value in values.items() if value is not None}
    )


def clear_context() -> None:
    """Clear request/task context after a request or task completes."""
    structlog.contextvars.clear_contextvars()


def _sanitise_value(value: Any, key: str | None = None) -> Any:
    """Recursively remove sensitive fields from structured log values."""
    if key in _FORBIDDEN_KEYS or (key is not None and _SENSITIVE_KEY_PATTERN.search(key)):
        return _REDACTED
    if isinstance(value, Mapping):
        return {
            str(child_key): _sanitise_value(child_value, str(child_key))
            for child_key, child_value in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [_sanitise_value(item) for item in value]
    if isinstance(value, str) and _SECRET_VALUE_PATTERN.search(value):
        return _REDACTED
    return value


def _filter_sensitive_fields(_logger: Any, _method_name: str, event_dict: Any) -> Any:
    """Strip source content and authentication material from every event."""
    return {
        str(key): _sanitise_value(value, str(key))
        for key, value in event_dict.items()
        if str(key) not in _FORBIDDEN_KEYS
    }


def _add_runtime_context(_logger: Any, _method_name: str, event_dict: Any) -> Any:
    """Add process-level service metadata without relying on request context."""
    for key, value in _runtime_context.items():
        event_dict.setdefault(key, value)
    return event_dict


def _add_trace_context(_logger: Any, _method_name: str, event_dict: Any) -> Any:
    """Add W3C trace and span IDs when an active OpenTelemetry span exists."""
    try:
        from opentelemetry import trace
        from opentelemetry.trace import format_span_id, format_trace_id

        span_context = trace.get_current_span().get_span_context()
        if span_context.is_valid:
            event_dict.setdefault("trace_id", format_trace_id(span_context.trace_id))
            event_dict.setdefault("span_id", format_span_id(span_context.span_id))
    except Exception:
        # Logging must never break the request if optional tracing is absent or
        # has not completed initialisation.
        pass
    return event_dict


def _renderer(settings: Any) -> Any:
    if settings.LOG_FORMAT.lower() == "json":
        return structlog.processors.JSONRenderer(sort_keys=True)
    return structlog.dev.ConsoleRenderer(
        colors=settings.LOG_COLOURS,
        pad_event_to=32,
    )


def configure_logging(service_name: str | None = None) -> None:
    """Configure consistent structlog and standard-library logging handlers."""
    settings = get_settings()
    level_name = settings.LOG_LEVEL.upper()
    level = getattr(logging, level_name, logging.INFO)

    _runtime_context.clear()
    _runtime_context.update(
        service=service_name or settings.SERVICE_NAME,
        environment=settings.APP_ENV,
    )

    pre_chain = [
        structlog.contextvars.merge_contextvars,
        _add_runtime_context,
        structlog.stdlib.add_logger_name,
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        _add_trace_context,
        structlog.processors.format_exc_info,
        _filter_sensitive_fields,
    ]
    formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=pre_chain,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            _renderer(settings),
        ],
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(level)

    # The request middleware is the canonical access log. These handlers would
    # otherwise duplicate every request and make failures hard to spot.
    logger_levels = {
        "uvicorn.access": logging.WARNING,
        "uvicorn.error": level,
        "sqlalchemy.engine": logging.INFO if settings.SQL_ECHO else logging.WARNING,
        "sqlalchemy.pool": logging.WARNING,
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "opentelemetry": logging.WARNING,
    }
    for logger_name, logger_level in logger_levels.items():
        named_logger = logging.getLogger(logger_name)
        named_logger.handlers.clear()
        named_logger.propagate = True
        named_logger.setLevel(logger_level)

    logging.captureWarnings(capture=True)
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.contextvars.merge_contextvars,
            _add_runtime_context,
            structlog.stdlib.add_logger_name,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_trace_context,
            structlog.processors.format_exc_info,
            _filter_sensitive_fields,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a configured structured logger."""
    return structlog.get_logger(name)
