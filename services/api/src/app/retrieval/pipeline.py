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
from datetime import UTC, datetime
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit.chain import append_event
from app.auth.credentials import EncryptedPayload, get_credential_manager
from app.auth.oidc import AuthenticatedUser
from app.domain.types import (
    AccessCheckResult,
    AccessOutcome,
    AuthorisedEvidence,
    CandidateRef,
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
    EvidenceItem,
    ObjectVersion,
    QueryRun,
    SourceObject,
)
from app.persistence.session import get_session_factory, set_tenant_context
from app.retrieval.context import compile_context
from app.retrieval.rerank import rerank_authorised_evidence
from app.retrieval.rrf import fuse_candidates
from app.retrieval.underfill import lexical_search_with_fallback, vector_search_with_fallback
from app.retrieval.verify import build_extractive_fallback, verify_answer
from app.settings import get_settings

logger = get_logger(__name__)


async def _decrypt_query(session: AsyncSession, query_id: UUID) -> str:
    run = (
        await session.execute(select(QueryRun).where(QueryRun.id == query_id))
    ).scalar_one_or_none()
    if run is None:
        raise ValueError(f"Query run {query_id} not found")
    cm = get_credential_manager()
    return cm.decrypt_bytes(EncryptedPayload(ciphertext=run.query_ciphertext)).decode("utf-8")


async def _update_status(session: AsyncSession, query_id: UUID, status: str) -> None:
    run = (
        await session.execute(select(QueryRun).where(QueryRun.id == query_id))
    ).scalar_one_or_none()
    if run is not None:
        run.status = status


async def _load_authorised_text(
    session: AsyncSession,
    candidates: list[CandidateRef],
    authorised_object_ids: set[UUID],
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
            SourceObject.id,
            SourceObject.source,
            SourceObject.canonical_url,
        )
        .join(ObjectVersion, Chunk.object_version_id == ObjectVersion.id)
        .join(SourceObject, ObjectVersion.source_object_id == SourceObject.id)
        .where(Chunk.id.in_(chunk_ids))
    )
    rows = (await session.execute(stmt)).all()

    evidence_list: list[AuthorisedEvidence] = []
    for row in rows:
        fused_score = next(
            (c.fused_score for c in authorised_candidates if c.chunk_id == row[0]), 0.0
        )
        evidence_list.append(
            AuthorisedEvidence(
                evidence_id=uuid4(),
                chunk_id=row[0],
                text=row[1],
                source_object_id=row[5],
                source=Source(row[6]),
                source_revision=row[3] or "",
                title=row[4],
                canonical_url=row[7],
                fused_score=fused_score,
            )
        )

    by_object: dict[UUID, list[AuthorisedEvidence]] = {}
    for ev in evidence_list:
        by_object.setdefault(ev.source_object_id, []).append(ev)
    deduped: list[AuthorisedEvidence] = []
    for evs in by_object.values():
        if len(evs) > 1:
            evs.sort(key=lambda e: e.source_revision, reverse=True)
            deduped.append(evs[0])
        else:
            deduped.extend(evs)

    logger.info(
        "Authorised text loaded", candidate_count=len(candidates), evidence_count=len(deduped)
    )
    return deduped


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
) -> None:
    cm = get_credential_manager()
    settings = get_settings()
    encrypted_answer = cm.encrypt_bytes(answer.model_dump_json().encode("utf-8"))

    run = (
        await session.execute(select(QueryRun).where(QueryRun.id == query_id))
    ).scalar_one_or_none()
    if run is not None:
        run.status = status
        run.answer_ciphertext = encrypted_answer.ciphertext
        run.completed_at = datetime.now(UTC)
        run.latency_ms = int((datetime.now(UTC) - run.started_at).total_seconds() * 1000)
        run.model_version = settings.GENERATION_MODEL
        run.prompt_version = "answer_v1"
        run.retriever_version = "rrf_v1"
        run.reranker_version = "deterministic-authority-v1"
        run.context_policy_version = "xml_v1"
        if partial_sources:
            run.partial_sources = partial_sources

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
                query_id=query_id,
                tenant_id=tenant_id,
                chunk_id=ev.chunk_id,
                source_object_id=ev.source_object_id,
                source_revision=ev.source_revision,
                source=ev.source.value,
                title_snapshot=ev.title,
                canonical_url=ev.canonical_url,
                quoted_span_ciphertext=quoted_span_encrypted.ciphertext,
                rank=reranked_ev.rank,
                retrieval_score=ev.fused_score,
                rerank_score=reranked_ev.rerank_score,
            )
        )

    await append_event(
        session=session,
        tenant_id=tenant_id,
        event_type="query.completed",
        canonical_payload={
            "query_id": str(query_id),
            "status": status,
            "evidence_count": len(reranked),
            "access_decision_count": len(access_decisions),
            "partial_sources": partial_sources or [],
        },
        query_id=query_id,
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
            query_text = await _decrypt_query(session, query_id)
            await _update_status(session, query_id, QueryStatus.AUTHENTICATED.value)
            await session.commit()

        gateway = get_model_gateway()
        query_embedding = await gateway.embed([query_text], settings.EMBEDDING_MODEL)
        embedding = query_embedding[0] if query_embedding else []

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            vector_hits = await vector_search_with_fallback(
                session, user.tenant_id, embedding, settings.CANDIDATE_TOP_K
            )
            lexical_hits = await lexical_search_with_fallback(
                session, user.tenant_id, query_text, settings.CANDIDATE_TOP_K
            )
            await _update_status(session, query_id, QueryStatus.CANDIDATES.value)
            await session.commit()

        candidates = fuse_candidates(vector_hits, lexical_hits, settings.RRF_K)

        if not candidates:
            await _audit_terminal_result(
                query_id=query_id,
                tenant_id=user.tenant_id,
                answer=_terminal_answer(),
                status=QueryStatus.DENIED.value,
            )
            return {"status": "denied", "query_id": str(query_id)}

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
            local_verdicts = await batch_check_local_acl(session, user.user_id, acl_candidates)

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
                session, candidates_after_local, authorised_object_ids
            )
            await _update_status(session, query_id, QueryStatus.REAUTHORISED.value)
            await session.commit()

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

        reranked = rerank_authorised_evidence(evidence_list, settings.FINAL_EVIDENCE_COUNT)

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            await _update_status(session, query_id, QueryStatus.RERANKED.value)
            await session.commit()

        context_evidence = [r.evidence for r in reranked]
        compiled_context = compile_context(context_evidence, query_text=query_text)

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            await _update_status(session, query_id, QueryStatus.COMPILED.value)
            await session.commit()

        generated_obj = await gateway.generate(compiled_context, OutputGeneratedAnswer)
        generated_answer = (
            generated_obj
            if isinstance(generated_obj, GeneratedAnswer)
            else GeneratedAnswer(
                answer=getattr(generated_obj, "answer", ""),
                claims=getattr(generated_obj, "claims", []),
            )
        )

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            await _update_status(session, query_id, QueryStatus.GENERATED.value)
            await session.commit()

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

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            await _update_status(session, query_id, QueryStatus.VERIFIED.value)
            await session.commit()

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
            )
            await session.commit()

        latency = time.monotonic() - start_time
        QUERY_LATENCY.labels(status=final_status).observe(latency)
        logger.info("Query pipeline completed", query_id=str(query_id), status=final_status)
        return {"status": final_status, "query_id": str(query_id)}

    except Exception as exc:
        logger.exception("Query pipeline failed", query_id=str(query_id), error=str(exc))
        try:
            async with factory() as session:
                await set_tenant_context(session, user.tenant_id)
                await _update_status(session, query_id, QueryStatus.FAILED.value)
                run = (
                    await session.execute(select(QueryRun).where(QueryRun.id == query_id))
                ).scalar_one_or_none()
                if run is not None:
                    run.error_code = "PIPELINE_ERROR"
                    run.completed_at = datetime.now(UTC)
                await session.commit()
        except Exception:
            pass
        QUERY_LATENCY.labels(status="failed").observe(time.monotonic() - start_time)
        return {"status": "failed", "query_id": str(query_id)}
