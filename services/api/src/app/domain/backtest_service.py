"""Deterministic role/time visibility regression runner."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.oidc import AuthenticatedUser
from app.authorisation.live_check import check_live_access
from app.authorisation.local_acl import check_local_acl
from app.domain.types import AccessOutcome, Source
from app.persistence.models import (
    BacktestCase,
    BacktestResult,
    BacktestRun,
    ObjectVersion,
    SourceConnection,
    SourceObject,
    User,
)
from app.persistence.session import get_session_factory, set_tenant_context
from app.retrieval.temporal import active_at
from app.settings import get_settings


async def run_backtests(user: AuthenticatedUser) -> dict:
    """Run the curated fixture suite and persist exact visibility regressions."""
    settings = get_settings()
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        cases = list(
            (
                await session.scalars(
                    select(BacktestCase)
                    .where(
                        BacktestCase.tenant_id == user.tenant_id,
                        BacktestCase.active.is_(True),
                    )
                    .order_by(BacktestCase.name)
                )
            ).all()
        )
        run = BacktestRun(
            tenant_id=user.tenant_id,
            suite_version="chronicle-fixture-v1",
            policy_version="policy-v1",
            prompt_version="answer-v1",
            model_version=settings.GENERATION_MODEL,
            embedding_version=settings.EMBEDDING_MODEL,
            reranker_version="deterministic-authority-v1",
            context_compiler_version="context-v1",
        )
        session.add(run)
        await session.flush()

        passed_count = 0
        for case in cases:
            actual = await _visible_external_ids(session, user.tenant_id, case)
            expected = set(case.expected_visibility.get("externalObjectIds", []))
            leaked = actual - expected
            missing = expected - actual
            passed = not leaked and not missing
            if passed:
                passed_count += 1
            score = len(actual & expected) / len(expected) if expected else 1.0
            drift_class = None
            if leaked:
                drift_class = "hindsight_leakage" if case.as_of else "permission_leakage"
            elif missing:
                drift_class = "visibility_regression"
            session.add(
                BacktestResult(
                    tenant_id=user.tenant_id,
                    run_id=run.id,
                    case_id=case.id,
                    passed=passed,
                    answer_hash=hashlib.sha256(
                        "\x1f".join(sorted(actual)).encode("utf-8")
                    ).hexdigest(),
                    citation_score=score,
                    leakage_check=not leaked,
                    drift_class=drift_class,
                    notes=_notes(leaked, missing),
                )
            )

        run.status = "completed" if passed_count == len(cases) else "failed"
        run.completed_at = datetime.now(UTC)
        await session.commit()
        return {
            "runId": str(run.id),
            "status": run.status,
            "caseCount": len(cases),
            "passedCount": passed_count,
            "failedCount": len(cases) - passed_count,
        }


async def _visible_external_ids(
    session: AsyncSession, tenant_id: UUID, case: BacktestCase
) -> set[str]:
    requester = await session.scalar(
        select(User).where(
            User.tenant_id == tenant_id,
            User.oidc_subject == case.requester_subject,
        )
    )
    if requester is None:
        return set()
    auth_user = AuthenticatedUser(
        user_id=UUID(str(requester.id)),
        tenant_id=tenant_id,
        oidc_subject=requester.oidc_subject,
        display_name=requester.display_name,
        email=requester.email,
        is_compliance_user=requester.is_compliance_user,
    )
    objects = list(
        (
            await session.scalars(
                select(SourceObject)
                .join(
                    SourceConnection,
                    SourceObject.source_connection_id == SourceConnection.id,
                )
                .where(
                    SourceObject.tenant_id == tenant_id,
                    SourceConnection.mode == "fixture",
                )
            )
        ).all()
    )

    visible: set[str] = set()
    for source_object in objects:
        if not await _has_visible_version(session, source_object, case.as_of):
            continue
        source = Source(source_object.source)
        historical = await check_local_acl(
            session,
            UUID(str(requester.id)),
            source,
            UUID(str(source_object.id)),
            case.as_of,
        )
        if historical.outcome != AccessOutcome.ALLOW:
            continue
        current = await check_live_access(
            session,
            auth_user,
            source,
            UUID(str(source_object.id)),
        )
        if current.outcome == AccessOutcome.ALLOW:
            visible.add(source_object.external_object_id)
    return visible


async def _has_visible_version(
    session: AsyncSession,
    source_object: SourceObject,
    as_of: datetime | None,
) -> bool:
    if as_of is None:
        return bool(
            source_object.is_queryable
            and not source_object.is_deleted
            and source_object.current_version_id
        )
    version_id = await session.scalar(
        select(ObjectVersion.id)
        .where(
            ObjectVersion.source_object_id == source_object.id,
            ObjectVersion.ingestion_status == "active",
            active_at(ObjectVersion, as_of),
        )
        .limit(1)
    )
    return version_id is not None


def _notes(leaked: set[str], missing: set[str]) -> str | None:
    notes: list[str] = []
    if leaked:
        notes.append(f"unexpected visibility: {', '.join(sorted(leaked))}")
    if missing:
        notes.append(f"missing expected visibility: {', '.join(sorted(missing))}")
    return "; ".join(notes) if notes else None


async def get_backtest_run(run_id: UUID, user: AuthenticatedUser) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, user.tenant_id)
        run = await session.scalar(
            select(BacktestRun).where(
                BacktestRun.id == run_id,
                BacktestRun.tenant_id == user.tenant_id,
            )
        )
        if run is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": {"code": "BACKTEST_RUN_NOT_FOUND"}},
            )
        rows = (
            await session.execute(
                select(BacktestResult, BacktestCase)
                .join(BacktestCase, BacktestResult.case_id == BacktestCase.id)
                .where(BacktestResult.run_id == run_id)
                .order_by(BacktestCase.name)
            )
        ).all()
        return {
            "runId": str(run.id),
            "status": run.status,
            "suiteVersion": run.suite_version,
            "versions": {
                "policy": run.policy_version,
                "prompt": run.prompt_version,
                "model": run.model_version,
                "embedding": run.embedding_version,
                "reranker": run.reranker_version,
                "contextCompiler": run.context_compiler_version,
            },
            "results": [
                {
                    "caseId": str(case.id),
                    "name": case.name,
                    "requester": case.requester_subject,
                    "asOf": case.as_of.isoformat() if case.as_of else None,
                    "passed": result.passed,
                    "leakageCheck": result.leakage_check,
                    "citationScore": result.citation_score,
                    "driftClass": result.drift_class,
                    "notes": result.notes,
                }
                for result, case in rows
            ],
        }
