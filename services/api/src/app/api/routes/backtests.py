"""Knowledge backtest endpoints."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from app.api.dependencies.auth import get_compliance_user
from app.auth.oidc import AuthenticatedUser

router = APIRouter()


@router.post("/backtests/runs")
async def create_backtest_run(
    user: AuthenticatedUser = Depends(get_compliance_user),
) -> dict:
    from app.domain.backtest_service import run_backtests

    return await run_backtests(user)


@router.get("/backtests/runs/{run_id}")
async def read_backtest_run(
    run_id: UUID,
    user: AuthenticatedUser = Depends(get_compliance_user),
) -> dict:
    from app.domain.backtest_service import get_backtest_run

    return await get_backtest_run(run_id, user)
