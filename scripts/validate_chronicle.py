#!/usr/bin/env python3
"""Run Chronicle's deterministic backend smoke checks against PostgreSQL."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))


async def main() -> None:
    from app.auth.oidc import AuthenticatedUser
    from app.domain.backtest_service import get_backtest_run, run_backtests
    from app.domain.knowledge_audit_service import list_knowledge_findings
    from app.persistence.models import QueryEvent, SourceObject, Tenant, User
    from app.persistence.session import (
        dispose_engine,
        get_session_factory,
        set_tenant_context,
    )
    from sqlalchemy import select

    factory = get_session_factory()
    async with factory() as session:
        tenant = await session.scalar(select(Tenant).where(Tenant.external_org_key == "dev-org"))
        if tenant is None:
            raise RuntimeError("Chronicle fixture is not seeded")
        await set_tenant_context(session, tenant.id)
        compliance = await session.scalar(
            select(User).where(
                User.tenant_id == tenant.id,
                User.oidc_subject == "compliance",
            )
        )
        if compliance is None:
            raise RuntimeError("Chronicle compliance user is not seeded")
        bob = await session.scalar(
            select(User).where(
                User.tenant_id == tenant.id,
                User.oidc_subject == "bob",
            )
        )
        if bob is None:
            raise RuntimeError("Chronicle Bob fixture user is not seeded")
        alice = await session.scalar(
            select(User).where(
                User.tenant_id == tenant.id,
                User.oidc_subject == "alice",
            )
        )
        if alice is None:
            raise RuntimeError("Chronicle Alice fixture user is not seeded")
        actor = AuthenticatedUser(
            user_id=UUID(str(compliance.id)),
            tenant_id=UUID(str(tenant.id)),
            oidc_subject=compliance.oidc_subject,
            display_name=compliance.display_name,
            email=compliance.email,
            is_compliance_user=True,
        )
        bob_actor = AuthenticatedUser(
            user_id=UUID(str(bob.id)),
            tenant_id=UUID(str(tenant.id)),
            oidc_subject=bob.oidc_subject,
            display_name=bob.display_name,
            email=bob.email,
            is_compliance_user=False,
        )
        alice_actor = AuthenticatedUser(
            user_id=UUID(str(alice.id)),
            tenant_id=UUID(str(tenant.id)),
            oidc_subject=alice.oidc_subject,
            display_name=alice.display_name,
            email=alice.email,
            is_compliance_user=False,
        )

    findings = await list_knowledge_findings(actor)
    run_summary = await run_backtests(actor)
    run = await get_backtest_run(UUID(run_summary["runId"]), actor)
    required_findings = {"conflict", "staleness", "access_risk", "process_gap"}
    observed_findings = {item["type"] for item in findings["findings"]}
    missing_findings = required_findings - observed_findings
    failures = [result for result in run["results"] if not result["passed"]]
    event_checks: dict[str, bool] = {}
    if "--exercise-events" in sys.argv:
        from app.authorisation.live_check import check_live_access
        from app.domain.demo_service import apply_fixture_event
        from app.domain.types import AccessOutcome, Source

        async def drive_access() -> AccessOutcome:
            async with factory() as session:
                await set_tenant_context(session, actor.tenant_id)
                drive_object = await session.scalar(
                    select(SourceObject).where(
                        SourceObject.tenant_id == actor.tenant_id,
                        SourceObject.external_object_id == "drive-security-PAY-2026-042",
                    )
                )
                if drive_object is None:
                    raise RuntimeError("Drive fixture object is not seeded")
                decision = await check_live_access(
                    session,
                    bob_actor,
                    Source.GOOGLE_DRIVE,
                    UUID(str(drive_object.id)),
                )
                return decision.outcome

        event_checks["bobDeniedInitially"] = await drive_access() == AccessOutcome.DENY
        await apply_fixture_event("grant-bob-drive", actor)
        event_checks["bobAllowedAfterGrant"] = await drive_access() == AccessOutcome.ALLOW
        await apply_fixture_event("revoke-bob-drive", actor)
        event_checks["bobDeniedAfterRevoke"] = await drive_access() == AccessOutcome.DENY
        update = await apply_fixture_event("publish-runbook-v2", actor)
        event_checks["runbookV2Published"] = update.get("revision") == "2"

    query_checks: dict[str, bool] = {}
    if "--queries" in sys.argv:
        from app.domain.audit_service import get_audit_capsule
        from app.domain.query_service import create_query_run, get_result
        from app.retrieval.pipeline import run_query_pipeline

        async def execute_query(
            requester: AuthenticatedUser,
            as_of: datetime | None = None,
        ) -> tuple[dict, dict, list[tuple[int, str]]]:
            created = await create_query_run(
                requester.user_id,
                requester.tenant_id,
                "What caused the payment gateway outage?",
                as_of,
            )
            query_id = created["id"]
            await run_query_pipeline(query_id, requester)
            result = await get_result(query_id, requester)
            capsule = await get_audit_capsule(query_id, actor)
            async with factory() as session:
                await set_tenant_context(session, requester.tenant_id)
                events = (
                    await session.execute(
                        select(QueryEvent.sequence_no, QueryEvent.event_type)
                        .where(QueryEvent.query_id == query_id)
                        .order_by(QueryEvent.sequence_no)
                    )
                ).all()
            return result, capsule, [(row[0], row[1]) for row in events]

        alice_current, alice_capsule, alice_events = await execute_query(alice_actor)
        alice_historical, historical_capsule, historical_events = await execute_query(
            alice_actor,
            datetime(2026, 9, 1, 10, 30, tzinfo=UTC),
        )
        bob_current, bob_capsule, bob_events = await execute_query(bob_actor)
        expected_event_types = {
            "query.queued",
            "query.authenticated",
            "query.retrieving",
            "query.reauthorising",
            "query.reranking",
            "query.generating",
            "query.verifying",
            "query.auditing",
            "query.completed",
        }

        def valid_event_stream(events: list[tuple[int, str]]) -> bool:
            return (
                [sequence for sequence, _ in events] == list(range(1, len(events) + 1))
                and {event_type for _, event_type in events} == expected_event_types
                and events[-1][1] == "query.completed"
            )

        query_checks = {
            "aliceCurrentCompleted": alice_current["status"] == "completed",
            "aliceCurrentIncludesDrive": any(
                item["source"] == "google_drive" for item in alice_current["citations"]
            ),
            "historicalCompleted": alice_historical["status"] == "completed",
            "historicalExcludesFutureJira": all(
                item["source"] != "jira" for item in alice_historical["citations"]
            ),
            "historicalAsOfPersisted": (historical_capsule["asOf"] == "2026-09-01T10:30:00+00:00"),
            "bobCompleted": bob_current["status"] == "completed",
            "bobExcludesRestrictedDrive": all(
                item["source"] != "google_drive" for item in bob_current["citations"]
            ),
            "capsulesSealed": all(
                capsule.get("authorisedContextHash") and capsule.get("contextManifest")
                for capsule in (alice_capsule, historical_capsule, bob_capsule)
            ),
            "auditChainsVerified": all(
                capsule.get("auditChainVerified")
                for capsule in (alice_capsule, historical_capsule, bob_capsule)
            ),
            "queryEventsPersistedInOrder": all(
                valid_event_stream(events)
                for events in (alice_events, historical_events, bob_events)
            ),
        }

    checkpoint_checks: dict[str, bool] = {}
    if "--checkpoint" in sys.argv:
        from app.domain.audit_service import (
            verify_audit_integrity,
            write_audit_checkpoint,
        )

        checkpoint = await write_audit_checkpoint(actor)
        integrity = await verify_audit_integrity(actor)
        checkpoint_checks = {
            "checkpointWritten": checkpoint.get("status") == "written",
            "chainValid": bool(integrity["chain"]["valid"]),
            "checkpointValid": bool(integrity["checkpoint"]["valid"]),
        }

    report = {
        "status": (
            "passed"
            if (
                not missing_findings
                and not failures
                and all(event_checks.values())
                and all(query_checks.values())
                and all(checkpoint_checks.values())
            )
            else "failed"
        ),
        "findingTypes": sorted(observed_findings),
        "missingFindingTypes": sorted(missing_findings),
        "backtest": run_summary,
        "failedCases": failures,
        "eventChecks": event_checks,
        "queryChecks": query_checks,
        "checkpointChecks": checkpoint_checks,
    }
    print(json.dumps(report, indent=2, sort_keys=True))  # noqa: T201
    await dispose_engine()
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
