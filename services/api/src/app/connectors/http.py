"""Small, provider-neutral HTTP helpers for connector adapters.

The helper centralises bounded retries and error classification.  Response
bodies are deliberately excluded from connector errors and logs because they
may contain source content or provider metadata.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from typing import Any

import httpx

from app.connectors.errors import (
    ForbiddenError,
    NotFoundError,
    PermanentError,
    RateLimitedError,
    TransientError,
)

_MAX_ATTEMPTS = 3
_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}


async def request(
    method: str,
    url: str,
    *,
    access_token: str,
    params: Mapping[str, Any] | None = None,
    json: Any = None,
    timeout: float = 15.0,  # noqa: ASYNC109
) -> httpx.Response:
    """Make a bounded provider request and classify transport failures."""
    headers = {"Authorization": f"Bearer {access_token}"}
    for attempt in range(_MAX_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                    json=json,
                )
        except httpx.TimeoutException as exc:
            if attempt == _MAX_ATTEMPTS - 1:
                raise TransientError("Provider request timed out") from exc
            await asyncio.sleep(0.1 * (2**attempt))
            continue
        except httpx.HTTPError as exc:
            if attempt == _MAX_ATTEMPTS - 1:
                raise TransientError("Provider request failed") from exc
            await asyncio.sleep(0.1 * (2**attempt))
            continue

        if response.status_code not in _RETRYABLE_STATUS_CODES:
            if response.status_code == 401:
                raise ForbiddenError("Provider credential rejected")
            if response.status_code == 403:
                raise ForbiddenError("Provider access forbidden")
            if response.status_code == 404:
                raise NotFoundError()
            if response.status_code >= 400:
                raise PermanentError("Provider rejected the request")
            return response

        if attempt == _MAX_ATTEMPTS - 1:
            if response.status_code == 429:
                retry_after = _retry_after_seconds(response)
                raise RateLimitedError(retry_after=retry_after)
            raise TransientError("Provider returned a temporary error")

        delay = min(_retry_after_seconds(response) or (0.1 * (2**attempt)), 2.0)
        await asyncio.sleep(delay)

    raise TransientError("Provider request failed")


def json_body(response: httpx.Response) -> dict[str, Any]:
    """Decode a provider JSON object without exposing invalid bodies."""
    try:
        data = response.json()
    except ValueError as exc:
        raise TransientError("Provider returned malformed JSON") from exc
    if not isinstance(data, dict):
        raise TransientError("Provider returned an invalid response")
    return data


def request_id(response: httpx.Response) -> str | None:
    """Extract a safe provider request identifier."""
    return (
        response.headers.get("x-request-id")
        or response.headers.get("x-arequestid")
        or response.headers.get("x-slack-req-id")
        or response.headers.get("x-guploader-uploadid")
    )


def _retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    if value is None:
        return None
    try:
        return max(0.0, min(float(value), 2.0))
    except ValueError:
        return None
