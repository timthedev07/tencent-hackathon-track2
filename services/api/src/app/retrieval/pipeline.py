"""Query pipeline — the full 10-stage retrieval-to-answer pipeline.

B. Candidate retrieval (vector + FTS)
C. Reciprocal-rank fusion
D. Local ACL projection
E. Live reauthorisation
F. Authorised text load
G. Reranking
H. Context compilation
I. Generation
J. Verification
K. Audit commit (atomic: state + answer + evidence + audit event)

INV-005: final answer not emitted until audit commit succeeds.
INV-001: candidate text must not enter reranker/context/LLM/logs until auth.
"""

from __future__ import annotations

import time
from contextlib import suppress
from datetime import UTC, datetime
from uuid import UUID, uuid5

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.chain import append_event
from app.auth.credentials import EncryptedPayload, get_credential_manager
from app.auth.oidc import AuthenticatedUser
from app.domain.query_execution import (
    append_query_event,
    fail_query_execution,
    record_query_stage,
)
from app.domain.types import (
    AccessCheckResult,
    AccessOutcome,
    AuthorisedEvidence,
    CandidateRef,
    CompiledContext,
    GeneratedAnswer,
    QueryStatus,
    RerankedEvidence,
    Source,
)
from app.models.gateway import get_model_gateway
from app.models.output_schemas import GeneratedAnswer as OutputGeneratedAnswer
from app.observability.logging import get_logger
from app.observability.metrics import QUERY_LATENCY
from app.persistence.models import (
    AccessDecision,
    Chunk,
    EvidenceCapsule,
    EvidenceItem,
    ObjectVersion,
    QueryRun,
    SourceObject,
)
from app.persistence.session import get_session_factory, set_tenant_context
from app.retrieval.context import compile_context
from app.retrieval.rerank import rerank_authorised_evidence
from app.retrieval.rrf import fuse_candidates
from app.retrieval.temporal import active_at
from app.retrieval.underfill import lexical_search_with_fallback, vector_search_with_fallback
from app.retrieval.verify import build_extractive_fallback, verify_answer
from app.settings import get_settings

logger = get_logger(__name__)


async def _decrypt_query(session: AsyncSession, query_id: UUID, user_id: UUID) -> str:
    run = (
        await session.execute(
            select(QueryRun).where(
                QueryRun.id == query_id,
                QueryRun.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if run is None:
        raise ValueError(f"Query run {query_id} not found")
    cm = get_credential_manager()
    return cm.decrypt_bytes(EncryptedPayload(ciphertext=run.query_ciphertext)).decode("utf-8")


async def _load_as_of(session: AsyncSession, query_id: UUID) -> datetime | None:
    return (
        await session.execute(select(QueryRun.as_of).where(QueryRun.id == query_id))
    ).scalar_one_or_none()


async def _load_authorised_text(
    session: AsyncSession,
    query_id: UUID,
    candidates: list[CandidateRef],
    authorised_object_ids: set[UUID],
    as_of: datetime | None = None,
) -> list[AuthorisedEvidence]:
    if not candidates:
        return []
    authorised_candidates = [c for c in candidates if c.source_object_id in authorised_object_ids]
    if not authorised_candidates:
        return []

    chunk_ids = [c.chunk_id for c in authorised_candidates]
    stmt = (
        select(
            Chunk.id,
            Chunk.text,
            Chunk.object_version_id,
            ObjectVersion.source_revision,
            ObjectVersion.normalised_title,
            ObjectVersion.content_hash,
            ObjectVersion.observed_at,
            ObjectVersion.valid_from,
            SourceObject.id,
            SourceObject.source,
            SourceObject.canonical_url,
        )
        .join(ObjectVersion, Chunk.object_version_id == ObjectVersion.id)
        .join(SourceObject, ObjectVersion.source_object_id == SourceObject.id)
        .where(
            Chunk.id.in_(chunk_ids),
            *([active_at(ObjectVersion, as_of)] if as_of else []),
        )
    )
    rows = (await session.execute(stmt)).all()

    selected_versions: dict[UUID, tuple[UUID, tuple[float, float, str]]] = {}
    for row in rows:
        object_id = row[8]
        version_rank = (
            row[6].timestamp(),
            row[7].timestamp() if row[7] else float("-inf"),
            row[3],
        )
        current = selected_versions.get(object_id)
        if current is None or version_rank > current[1]:
            selected_versions[object_id] = (row[2], version_rank)

    evidence_list: list[AuthorisedEvidence] = []
    for row in rows:
        if selected_versions[row[8]][0] != row[2]:
            continue
        fused_score = next(
            (c.fused_score for c in authorised_candidates if c.chunk_id == row[0]), 0.0
        )
        evidence_list.append(
            AuthorisedEvidence(
                evidence_id=uuid5(query_id, str(row[0])),
                chunk_id=row[0],
                object_version_id=row[2],
                text=row[1],
                source_object_id=row[8],
                source=Source(row[9]),
                source_revision=row[3] or "",
                content_hash=row[5],
                title=row[4],
                canonical_url=row[10],
                fused_score=fused_score,
            )
        )

    logger.info(
        "Authorised text loaded",
        candidate_count=len(candidates),
        evidence_count=len(evidence_list),
    )
    return evidence_list


async def _audit_commit(
    session: AsyncSession,
    query_id: UUID,
    tenant_id: UUID,
    answer: GeneratedAnswer,
    reranked: list[RerankedEvidence],
    access_decisions: dict[UUID, AccessCheckResult],
    object_source_map: dict[UUID, Source],
    status: str,
    partial_sources: list[str] | None = None,
    compiled_context: CompiledContext | None = None,
) -> None:
    cm = get_credential_manager()
    settings = get_settings()
    encrypted_answer = cm.encrypt_bytes(answer.model_dump_json().encode("utf-8"))

    run = (
        await session.execute(select(QueryRun).where(QueryRun.id == query_id))
    ).scalar_one_or_none()
    if run is not None:
        if compiled_context is None:
            compiled_context = compile_context(
                [],
                requester=str(run.user_id),
                as_of=run.as_of,
            )
        run.status = status
        run.answer_ciphertext = encrypted_answer.ciphertext
        run.completed_at = datetime.now(UTC)
        run.latency_ms = int((datetime.now(UTC) - run.started_at).total_seconds() * 1000)
        run.model_version = settings.GENERATION_MODEL
        run.embedding_version = settings.EMBEDDING_MODEL
        run.prompt_version = "answer_v1"
        run.policy_version = compiled_context.policy_version
        run.retriever_version = "rrf_v1"
        run.reranker_version = "deterministic-authority-v1"
        run.context_policy_version = "xml_v1"
        run.context_compiler_version = compiled_context.context_compiler_version
        run.authorised_context_hash = compiled_context.snapshot_hash
        if partial_sources:
            run.partial_sources = partial_sources

        session.add(
            EvidenceCapsule(
                tenant_id=tenant_id,
                query_id=query_id,
                snapshot_hash=compiled_context.snapshot_hash,
                context_manifest={
                    "requester": compiled_context.requester,
                    "asOf": compiled_context.as_of.isoformat() if compiled_context.as_of else None,
                    "policyVersion": compiled_context.policy_version,
                    "contextCompilerVersion": compiled_context.context_compiler_version,
                    "allowedEvidence": compiled_context.evidence_manifest,
                },
                answer_ciphertext=encrypted_answer.ciphertext,
            )
        )

    for source_object_id, decision_result in access_decisions.items():
        source = object_source_map.get(source_object_id)
        if source is None:
            continue
        session.add(
            AccessDecision(
                query_id=query_id,
                tenant_id=tenant_id,
                source_object_id=source_object_id,
                source=source.value,
                decision=decision_result.outcome.value,
                reason_code=decision_result.reason_code,
                access_checked_at=datetime.now(UTC),
                provider_latency_ms=decision_result.latency_ms,
                provider_request_id=decision_result.provider_request_id,
            )
        )

    for reranked_ev in reranked:
        ev = reranked_ev.evidence
        quoted_span_encrypted = cm.encrypt_bytes(ev.text.encode("utf-8"))
        session.add(
            EvidenceItem(
                id=ev.evidence_id,
                query_id=query_id,
                tenant_id=tenant_id,
                chunk_id=ev.chunk_id,
                object_version_id=ev.object_version_id,
                source_object_id=ev.source_object_id,
                source_revision=ev.source_revision,
                source=ev.source.value,
                title_snapshot=ev.title,
                canonical_url=ev.canonical_url,
                quoted_span_ciphertext=quoted_span_encrypted.ciphertext,
                content_hash=ev.content_hash,
                rank=reranked_ev.rank,
                retrieval_score=ev.fused_score,
                rerank_score=reranked_ev.rerank_score,
            )
        )

    terminal_event = {
        QueryStatus.PARTIAL.value: "query.partial",
        QueryStatus.DENIED.value: "query.denied",
        QueryStatus.FAILED.value: "query.failed",
    }.get(status, "query.completed")
    terminal_message = {
        QueryStatus.PARTIAL.value: "Answer completed with partial source coverage",
        QueryStatus.DENIED.value: "No authorised evidence was available",
        QueryStatus.FAILED.value: "The query could not be completed",
    }.get(status, "Answer completed")

    await append_event(
        session=session,
        tenant_id=tenant_id,
        event_type=terminal_event,
        canonical_payload={
            "query_id": str(query_id),
            "status": status,
            "evidence_count": len(reranked),
            "access_decision_count": len(access_decisions),
            "partial_sources": partial_sources or [],
            "authorised_context_hash": (
                compiled_context.snapshot_hash if compiled_context else None
            ),
        },
        query_id=query_id,
    )
    await append_query_event(
        session,
        tenant_id,
        query_id,
        terminal_event,
        {"message": terminal_message, "status": status},
        status=status,
    )


def _terminal_answer(
    message: str = "No authorised evidence was available for this query.",
) -> GeneratedAnswer:
    return GeneratedAnswer(answer=message, claims=[])


async def _audit_terminal_result(
    *,
    query_id: UUID,
    tenant_id: UUID,
    answer: GeneratedAnswer,
    status: str,
    access_decisions: dict[UUID, AccessCheckResult] | None = None,
    object_source_map: dict[UUID, Source] | None = None,
    partial_sources: list[str] | None = None,
    reranked: list[RerankedEvidence] | None = None,
) -> None:
    factory = get_session_factory()
    async with factory() as session:
        await set_tenant_context(session, tenant_id)
        await _audit_commit(
            session=session,
            query_id=query_id,
            tenant_id=tenant_id,
            answer=answer,
            reranked=reranked or [],
            access_decisions=access_decisions or {},
            object_source_map=object_source_map or {},
            status=status,
            partial_sources=partial_sources,
            compiled_context=None,
        )
        await session.commit()


async def run_query_pipeline(query_id: UUID, user: AuthenticatedUser) -> dict:
    settings = get_settings()
    start_time = time.monotonic()
    factory = get_session_factory()

    logger.info("Query pipeline started", query_id=str(query_id))

    try:
        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            query_text = await _decrypt_query(session, query_id, user.user_id)
            as_of = await _load_as_of(session, query_id)

        await record_query_stage(
            query_id,
            user.tenant_id,
            status=QueryStatus.AUTHENTICATED.value,
            event_type="query.authenticated",
            message="Identity verified",
        )
        await record_query_stage(
            query_id,
            user.tenant_id,
            status=QueryStatus.AUTHENTICATED.value,
            event_type="query.retrieving",
            message="Searching connected sources",
        )

        gateway = get_model_gateway()
        query_embedding = await gateway.embed([query_text], settings.EMBEDDING_MODEL)
        embedding = query_embedding[0] if query_embedding else []

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            vector_hits = await vector_search_with_fallback(
                session,
                user.tenant_id,
                embedding,
                settings.CANDIDATE_TOP_K,
                as_of,
            )
            lexical_hits = await lexical_search_with_fallback(
                session,
                user.tenant_id,
                query_text,
                settings.CANDIDATE_TOP_K,
                as_of,
            )

        candidates = fuse_candidates(vector_hits, lexical_hits, settings.RRF_K)

        if not candidates:
            await _audit_terminal_result(
                query_id=query_id,
                tenant_id=user.tenant_id,
                answer=_terminal_answer(),
                status=QueryStatus.DENIED.value,
            )
            return {"status": "denied", "query_id": str(query_id)}

        await record_query_stage(
            query_id,
            user.tenant_id,
            status=QueryStatus.CANDIDATES.value,
            event_type="query.reauthorising",
            message="Checking your current access",
        )

        from app.authorisation.live_check import check_live_access_batch
        from app.authorisation.local_acl import batch_check_local_acl

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            seen_objects: set[UUID] = set()
            acl_candidates: list[tuple[UUID, Source]] = []
            for c in candidates:
                if c.source_object_id not in seen_objects:
                    acl_candidates.append((c.source_object_id, c.source))
                    seen_objects.add(c.source_object_id)
            local_verdicts = await batch_check_local_acl(
                session,
                user.user_id,
                acl_candidates,
                as_of,
            )

        local_denied = {
            obj_id for obj_id, v in local_verdicts.items() if v.outcome == AccessOutcome.DENY
        }
        local_denied_results = {
            obj_id: AccessCheckResult(outcome=v.outcome, reason_code=v.reason)
            for obj_id, v in local_verdicts.items()
            if v.outcome == AccessOutcome.DENY
        }
        local_object_source_map = dict(acl_candidates)
        candidates_after_local = [c for c in candidates if c.source_object_id not in local_denied]

        if not candidates_after_local:
            await _audit_terminal_result(
                query_id=query_id,
                tenant_id=user.tenant_id,
                answer=_terminal_answer(),
                status=QueryStatus.DENIED.value,
                access_decisions=local_denied_results,
                object_source_map=local_object_source_map,
            )
            return {"status": "denied", "query_id": str(query_id)}

        unique_objects: list[tuple[UUID, Source]] = []
        seen_for_live: set[UUID] = set()
        for c in candidates_after_local[: settings.LIVE_AUTH_TOP_OBJECTS]:
            if c.source_object_id not in seen_for_live:
                unique_objects.append((c.source_object_id, c.source))
                seen_for_live.add(c.source_object_id)

        object_source_map: dict[UUID, Source] = dict(unique_objects)

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            access_results = await check_live_access_batch(
                session=session,
                user=user,
                objects=unique_objects,
                deadline_ms=settings.QUERY_AUTH_DEADLINE_MS,
            )

        authorised_object_ids: set[UUID] = set()
        partial_sources: set[str] = set()
        for obj_id, result in access_results.items():
            if result.outcome == AccessOutcome.ALLOW:
                authorised_object_ids.add(obj_id)
            elif result.outcome in {
                AccessOutcome.TIMEOUT,
                AccessOutcome.UNKNOWN,
                AccessOutcome.EXPIRED_CREDENTIAL,
            }:
                source = object_source_map.get(obj_id)
                if source:
                    partial_sources.add(source.value)

        all_access_results = {**local_denied_results, **access_results}
        all_object_source_map = {**local_object_source_map, **object_source_map}

        if not authorised_object_ids:
            await _audit_terminal_result(
                query_id=query_id,
                tenant_id=user.tenant_id,
                answer=_terminal_answer(),
                status=QueryStatus.DENIED.value,
                access_decisions=all_access_results,
                object_source_map=all_object_source_map,
            )
            return {"status": "denied", "query_id": str(query_id)}

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            evidence_list = await _load_authorised_text(
                session,
                query_id,
                candidates_after_local,
                authorised_object_ids,
                as_of,
            )

        if not evidence_list:
            await _audit_terminal_result(
                query_id=query_id,
                tenant_id=user.tenant_id,
                answer=_terminal_answer(),
                status=QueryStatus.DENIED.value,
                access_decisions=all_access_results,
                object_source_map=all_object_source_map,
            )
            return {"status": "denied", "query_id": str(query_id)}

        await record_query_stage(
            query_id,
            user.tenant_id,
            status=QueryStatus.REAUTHORISED.value,
            event_type="query.reranking",
            message="Ranking authorised evidence",
        )
        reranked = rerank_authorised_evidence(evidence_list, settings.FINAL_EVIDENCE_COUNT)

        context_evidence = [r.evidence for r in reranked]
        compiled_context = compile_context(
            context_evidence,
            query_text=query_text,
            requester=user.oidc_subject,
            as_of=as_of,
        )

        await record_query_stage(
            query_id,
            user.tenant_id,
            status=QueryStatus.COMPILED.value,
            event_type="query.generating",
            message="Generating answer",
        )

        generated_obj = await gateway.generate(compiled_context, OutputGeneratedAnswer)
        generated_answer = (
            generated_obj
            if isinstance(generated_obj, GeneratedAnswer)
            else GeneratedAnswer(
                answer=getattr(generated_obj, "answer", ""),
                claims=getattr(generated_obj, "claims", []),
            )
        )

        await record_query_stage(
            query_id,
            user.tenant_id,
            status=QueryStatus.GENERATED.value,
            event_type="query.verifying",
            message="Verifying citations",
        )

        verification = verify_answer(generated_answer, context_evidence)
        if not verification.valid:
            logger.info("Verification failed — attempting repair", reason=verification.reason)
            repaired_obj = await gateway.generate(compiled_context, OutputGeneratedAnswer)
            repaired_answer = (
                repaired_obj
                if isinstance(repaired_obj, GeneratedAnswer)
                else GeneratedAnswer(
                    answer=getattr(repaired_obj, "answer", ""),
                    claims=getattr(repaired_obj, "claims", []),
                )
            )
            repair_verification = verify_answer(repaired_answer, context_evidence)
            if not repair_verification.valid:
                final_answer = build_extractive_fallback(context_evidence, query_text)
            else:
                final_answer = repaired_answer
        else:
            final_answer = verification.answer or generated_answer

        await record_query_stage(
            query_id,
            user.tenant_id,
            status=QueryStatus.VERIFIED.value,
            event_type="query.auditing",
            message="Saving audit record",
        )

        is_partial = bool(partial_sources)
        final_status = QueryStatus.PARTIAL.value if is_partial else QueryStatus.COMPLETED.value

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            await _audit_commit(
                session=session,
                query_id=query_id,
                tenant_id=user.tenant_id,
                answer=final_answer,
                reranked=reranked,
                access_decisions=all_access_results,
                object_source_map=all_object_source_map,
                status=final_status,
                partial_sources=list(partial_sources) if partial_sources else None,
                compiled_context=compiled_context,
            )
            await session.commit()

        latency = time.monotonic() - start_time
        QUERY_LATENCY.labels(status=final_status).observe(latency)
        logger.info("Query pipeline completed", query_id=str(query_id), status=final_status)
        return {"status": final_status, "query_id": str(query_id)}

    except Exception as exc:
        logger.exception("Query pipeline failed", query_id=str(query_id), error=str(exc))
        with suppress(Exception):
            await fail_query_execution(
                query_id,
                user.tenant_id,
                error_code="PIPELINE_ERROR",
                message="The query could not be completed.",
            )
        QUERY_LATENCY.labels(status="failed").observe(time.monotonic() - start_time)
        return {"status": "failed", "query_id": str(query_id)}
