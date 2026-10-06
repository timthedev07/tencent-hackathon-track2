"""Webhook handling routes.

POST /v1/webhooks/{source}

Processing order:
1. Request received
2. Verify provider signature
3. Validate minimum payload schema
4. Extract provider delivery/event ID
5. Deduplicate
6. Persist safe delivery metadata
7. Enqueue idempotent task
8. Acknowledge provider

Invalid signature/replay/malformed → reject before enqueueing.
"""

from __future__ import annotations

from fastapi import APIRouter, Header, HTTPException, Request, status
from pydantic import BaseModel

from app.observability.logging import get_logger
from app.settings import get_settings

router = APIRouter()
logger = get_logger(__name__)

_WEBHOOK_REPLAY_WINDOW_SECONDS = 300  # 5 minutes


class WebhookAcknowledgement(BaseModel):
    status: str = "received"


@router.post(
    "/{source}",
    response_model=WebhookAcknowledgement,
    status_code=status.HTTP_202_ACCEPTED,
)
async def handle_webhook(
    source: str,
    request: Request,
    x_slack_signature: str | None = Header(default=None),
    x_slack_request_timestamp: str | None = Header(default=None),
) -> WebhookAcknowledgement:
    """Handle incoming provider webhooks.

    Verifies signature before any processing (Section 18).
    """
    body = await request.body()

    # Validate source
    valid_sources = {"confluence", "jira", "slack", "google_drive"}
    if source not in valid_sources:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": {"code": "SOURCE_OBJECT_NOT_FOUND", "message": "Unknown source."}},
        )

    # Verify signature (source-specific)
    if source == "slack":
        await _verify_slack_signature(body, x_slack_signature, x_slack_request_timestamp)
    elif source in ("confluence", "jira"):
        # Atlassian webhooks: verify via HMAC-SHA256 with shared secret
        await _verify_atlassian_signature(body, request)
    elif source == "google_drive":
        # Google Drive webhooks: verify via channel token or OAuth
        await _verify_google_signature(body, request)

    # Parse and extract delivery ID
    try:
        payload = await request.json() if body else {}
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": {
                    "code": "SOURCE_FETCH_FAILED",
                    "message": "Webhook payload is malformed.",
                }
            },
        ) from exc

    # Deduplicate and enqueue
    from app.domain.ingestion_service import handle_webhook_delivery

    await handle_webhook_delivery(source, payload, body)

    return WebhookAcknowledgement()


async def _verify_slack_signature(
    body: bytes,
    signature: str | None,
    timestamp: str | None,
) -> None:
    """Verify Slack webhook signature using HMAC-SHA256."""
    import hashlib
    import hmac
    import time

    if not signature or not timestamp:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {"code": "AUTH_INVALID_TOKEN", "message": "Invalid webhook signature."}
            },
        )

    # Reject replays (5 minute window)
    try:
        ts = int(timestamp)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "AUTH_INVALID_TOKEN", "message": "Invalid timestamp."}},
        ) from None

    if abs(time.time() - ts) > _WEBHOOK_REPLAY_WINDOW_SECONDS:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "AUTH_INVALID_TOKEN", "message": "Webhook expired."}},
        )

    settings = get_settings()
    basestring = f"v0:{timestamp}:{body.decode('utf-8')}".encode()
    computed = (
        "v0="
        + hmac.new(
            settings.SLACK_SIGNING_SECRET.encode("utf-8"),
            basestring,
            hashlib.sha256,
        ).hexdigest()
    )

    if not hmac.compare_digest(computed, signature):
        logger.warning("Slack webhook signature verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {"code": "AUTH_INVALID_TOKEN", "message": "Invalid webhook signature."}
            },
        )


async def _verify_atlassian_signature(body: bytes, request: Request) -> None:
    """Verify Atlassian webhook signature.

    Atlassian sends a custom 'X-Hub-Signature' header for webhooks configured
    with a shared secret. The signature is HMAC-SHA256 of the raw body.
    """
    signature = request.headers.get("X-Hub-Signature") or request.headers.get("x-hub-signature")
    if not signature:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "AUTH_INVALID_TOKEN",
                    "message": "Missing webhook signature.",
                }
            },
        )

    import hashlib
    import hmac

    settings = get_settings()
    secret = settings.ATLASSIAN_WEBHOOK_SECRET
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "AUTH_INVALID_TOKEN",
                    "message": "Webhook signing not configured.",
                }
            },
        )

    computed = (
        "sha256="
        + hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
    )

    if not hmac.compare_digest(computed, signature):
        logger.warning("Atlassian webhook signature verification failed")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {"code": "AUTH_INVALID_TOKEN", "message": "Invalid webhook signature."}
            },
        )


async def _verify_google_signature(body: bytes, request: Request) -> None:
    """Verify Google Drive webhook notification.

    Google Drive push notifications do not include a body — they send
    a channel token in the 'X-Goog-Channel-Token' header. Verify this
    token matches a known value.
    """
    channel_token = request.headers.get("X-Goog-Channel-Token") or request.headers.get(
        "x-goog-channel-token"
    )
    resource_state = request.headers.get("X-Goog-Resource-State") or request.headers.get(
        "x-goog-resource-state"
    )

    if not resource_state:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": {
                    "code": "AUTH_INVALID_TOKEN",
                    "message": "Missing Google Drive webhook headers.",
                }
            },
        )

    if channel_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "AUTH_INVALID_TOKEN", "message": "Missing channel token."}},
        )

    import hmac

    expected_token = get_settings().GOOGLE_WEBHOOK_TOKEN
    if not expected_token or not hmac.compare_digest(channel_token, expected_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"error": {"code": "AUTH_INVALID_TOKEN", "message": "Invalid channel token."}},
        )
