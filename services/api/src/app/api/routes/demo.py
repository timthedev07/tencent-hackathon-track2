"""Development-only controls for deterministic fixture events."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import get_compliance_user
from app.auth.oidc import AuthenticatedUser

router = APIRouter()


@router.post("/demo/fixtures/events/{event_id}/apply")
async def apply_demo_event(
    event_id: str,
    user: AuthenticatedUser = Depends(get_compliance_user),
) -> dict:
    from app.domain.demo_service import apply_fixture_event

    return await apply_fixture_event(event_id, user)
