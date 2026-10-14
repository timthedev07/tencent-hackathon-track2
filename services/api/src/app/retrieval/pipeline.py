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

from sqlalchemy import func, or_, select
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
    QueryEvent,
    QueryRun,
    SourceConnection,
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

_EMPTY_WORKSPACE_CODE = "NO_SEARCHABLE_CONTENT"
_EMPTY_WORKSPACE_MESSAGE = (
    "No searchable content is available in your connected workspaces yet. "
    "Add content and wait for it to be indexed, then try again."
)


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
    if run is not None and run.status != status:
        run.status = status
        event_type, message = _QUERY_STAGE_EVENTS.get(
            status, (f"query.{status}", "Query progress updated")
        )
        last_sequence = await session.scalar(
            select(func.max(QueryEvent.sequence_no)).where(QueryEvent.query_id == query_id)
        )
        session.add(
            QueryEvent(
                tenant_id=run.tenant_id,
                query_id=query_id,
                sequence_no=(last_sequence or 0) + 1,
                event_type=event_type,
                payload={"message": message},
            )
        )


_QUERY_STAGE_EVENTS = {
    QueryStatus.AUTHENTICATED.value: ("query.authenticated", "Checking your identity"),
    QueryStatus.CANDIDATES.value: ("query.retrieving", "Searching connected sources"),
    QueryStatus.REAUTHORISED.value: ("query.reauthorising", "Checking your current access"),
    QueryStatus.RERANKED.value: ("query.reranking", "Ranking authorised evidence"),
    QueryStatus.COMPILED.value: ("query.generating", "Preparing a grounded answer"),
    QueryStatus.GENERATED.value: ("query.verifying", "Verifying citations"),
    QueryStatus.VERIFIED.value: ("query.auditing", "Saving the audit record"),
}


async def _has_searchable_content(session: AsyncSession, tenant_id: UUID) -> bool:
    """Return whether this tenant has any indexed content eligible for search.

    This inexpensive check prevents an empty workspace from waiting on an
    embedding provider before the pipeline can return its no-content result.
    """
    stmt = (
        select(Chunk.id)
        .join(ObjectVersion, Chunk.object_version_id == ObjectVersion.id)
        .join(SourceObject, ObjectVersion.source_object_id == SourceObject.id)
        .join(SourceConnection, SourceObject.source_connection_id == SourceConnection.id)
        .where(
            Chunk.tenant_id == tenant_id,
            SourceObject.tenant_id == tenant_id,
            SourceObject.is_queryable.is_(True),
            SourceObject.is_deleted.is_(False),
            SourceObject.current_version_id == ObjectVersion.id,
            ObjectVersion.ingestion_status == "active",
            ObjectVersion.retired_at.is_(None),
            or_(Chunk.embedding.isnot(None), Chunk.tsv.isnot(None)),
        )
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none() is not None


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
            Chunk.ordinal,
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
    rows = list((await session.execute(stmt)).all())

    candidate_order = {
        candidate.chunk_id: index for index, candidate in enumerate(authorised_candidates)
    }
    candidate_scores = {
        candidate.chunk_id: candidate.fused_score for candidate in authorised_candidates
    }
    rows.sort(key=lambda row: candidate_order.get(row[0], len(candidate_order)))
    evidence_list: list[AuthorisedEvidence] = []
    for row in rows:
        evidence_list.append(
            AuthorisedEvidence(
                evidence_id=uuid4(),
                chunk_id=row[0],
                text=row[1],
                chunk_ordinal=row[2],
                source_object_id=row[6],
                source=Source(row[7]),
                source_revision=row[4] or "",
                title=row[5],
                canonical_url=row[8],
                fused_score=candidate_scores.get(row[0], 0.0),
            )
        )

    logger.info(
        "Authorised text loaded", candidate_count=len(candidates), evidence_count=len(evidence_list)
    )
    return evidence_list


def _reranked_for_compiled_context(
    reranked: list[RerankedEvidence],
    compiled_evidence: list[AuthorisedEvidence],
) -> list[RerankedEvidence]:
    """Return citation metadata for prompt evidence, in prompt order only."""
    by_evidence_id = {item.evidence.evidence_id: item for item in reranked}
    included: list[RerankedEvidence] = []
    for rank, evidence in enumerate(compiled_evidence):
        item = by_evidence_id.get(evidence.evidence_id)
        if item is None:
            continue
        included.append(
            RerankedEvidence(
                evidence=evidence,
                rerank_score=item.rerank_score,
                rank=rank,
            )
        )
    return included


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
    error_code: str | None = None,
    model_version: str | None = None,
    embedding_version: str | None = None,
) -> None:
    cm = get_credential_manager()
    encrypted_answer = cm.encrypt_bytes(answer.model_dump_json().encode("utf-8"))

    run = (
        await session.execute(select(QueryRun).where(QueryRun.id == query_id))
    ).scalar_one_or_none()
    if run is not None:
        run.status = status
        run.error_code = error_code
        run.answer_ciphertext = encrypted_answer.ciphertext
        run.completed_at = datetime.now(UTC)
        run.latency_ms = int((datetime.now(UTC) - run.started_at).total_seconds() * 1000)
        run.model_version = model_version
        run.embedding_version = embedding_version
        run.prompt_version = "answer_v1"
        run.retriever_version = "rrf_v1"
        run.reranker_version = "deterministic-authority-v1"
        run.context_policy_version = "xml_v1"
        if partial_sources:
            run.partial_sources = partial_sources
        last_sequence = await session.scalar(
            select(func.max(QueryEvent.sequence_no)).where(QueryEvent.query_id == query_id)
        )
        terminal_messages = {
            QueryStatus.COMPLETED.value: "Answer ready",
            QueryStatus.PARTIAL.value: "Answer ready with source warnings",
            QueryStatus.DENIED.value: "No authorised evidence was available",
            QueryStatus.FAILED.value: "The query failed",
        }
        session.add(
            QueryEvent(
                tenant_id=run.tenant_id,
                query_id=query_id,
                sequence_no=(last_sequence or 0) + 1,
                event_type=f"query.{status}",
                payload={"message": terminal_messages.get(status, "Query finished")},
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
                query_id=query_id,
                tenant_id=tenant_id,
                chunk_id=ev.chunk_id,
                source_object_id=ev.source_object_id,
                source_revision=ev.source_revision,
                model_evidence_id=ev.evidence_id,
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
    error_code: str | None = None,
    model_version: str | None = None,
    embedding_version: str | None = None,
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
            error_code=error_code,
            model_version=model_version,
            embedding_version=embedding_version,
        )
        await session.commit()


async def run_query_pipeline(  # noqa: PLR0911
    query_id: UUID, user: AuthenticatedUser
) -> dict:
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

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            has_searchable_content = await _has_searchable_content(session, user.tenant_id)
            if has_searchable_content:
                continue_pipeline = True
            else:
                await _update_status(session, query_id, QueryStatus.CANDIDATES.value)
                await session.commit()
                continue_pipeline = False

        if not continue_pipeline:
            await _audit_terminal_result(
                query_id=query_id,
                tenant_id=user.tenant_id,
                answer=_terminal_answer(_EMPTY_WORKSPACE_MESSAGE),
                status=QueryStatus.DENIED.value,
                error_code=_EMPTY_WORKSPACE_CODE,
            )
            logger.info(
                "Query completed without indexed content",
                query_id=str(query_id),
                outcome=_EMPTY_WORKSPACE_CODE,
            )
            return {
                "status": "denied",
                "query_id": str(query_id),
                "error_code": _EMPTY_WORKSPACE_CODE,
            }

        gateway = get_model_gateway()
        query_embedding = await gateway.embed([query_text], gateway.embedding_model_version)
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
                embedding_version=gateway.embedding_model_version,
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
                embedding_version=gateway.embedding_model_version,
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
                embedding_version=gateway.embedding_model_version,
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
                embedding_version=gateway.embedding_model_version,
            )
            return {"status": "denied", "query_id": str(query_id)}

        reranked = rerank_authorised_evidence(evidence_list, settings.FINAL_EVIDENCE_COUNT)

        async with factory() as session:
            await set_tenant_context(session, user.tenant_id)
            await _update_status(session, query_id, QueryStatus.RERANKED.value)
            await session.commit()

        context_evidence = [r.evidence for r in reranked]
        compiled_context = compile_context(context_evidence, query_text=query_text)
        context_evidence = compiled_context.evidence_blocks
        compiled_reranked = _reranked_for_compiled_context(reranked, context_evidence)

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
                reranked=compiled_reranked,
                access_decisions=all_access_results,
                object_source_map=all_object_source_map,
                status=final_status,
                partial_sources=list(partial_sources) if partial_sources else None,
                model_version=gateway.generation_model_version,
                embedding_version=gateway.embedding_model_version,
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
        return {"status": "failed", "query_id": str(query_id), "error_code": "PIPELINE_ERROR"}
