#!/usr/bin/env python3
"""Seed the deterministic Chronicle payment-incident fixture."""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "services" / "api" / "src"))
FIXTURE_PATH = ROOT / "packages" / "fixtures" / "chronicle" / "incident.json"


def _id(key: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"chronicle-fixture:{key}")


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _required_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def main() -> None:
    """Seed users, four fixture sources, temporal evidence, ACLs, and tests."""
    from app.models.gateway import deterministic_embedding
    from app.persistence.models import (
        AclGrant,
        BacktestCase,
        Chunk,
        Claim,
        FixtureEvent,
        IdentityLink,
        ObjectVersion,
        PolicyVersion,
        Principal,
        SourceConnection,
        SourceObject,
        Tenant,
        User,
    )
    from app.persistence.session import (
        dispose_engine,
        get_session_factory,
        set_tenant_context,
    )
    from app.settings import get_settings
    from sqlalchemy import select

    fixture: dict[str, Any] = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    tenant_data = fixture["tenant"]
    factory = get_session_factory()

    async with factory() as session:
        tenant = await session.scalar(
            select(Tenant).where(Tenant.external_org_key == tenant_data["externalOrgKey"])
        )
        if tenant is None:
            tenant = Tenant(
                id=_id("tenant"),
                external_org_key=tenant_data["externalOrgKey"],
                display_name=tenant_data["displayName"],
            )
            session.add(tenant)
            await session.flush()
        else:
            tenant.display_name = tenant_data["displayName"]

        await set_tenant_context(session, tenant.id)

        users: dict[str, User] = {}
        for item in fixture["users"]:
            user = await session.scalar(
                select(User).where(
                    User.tenant_id == tenant.id,
                    User.oidc_subject == item["subject"],
                )
            )
            if user is None:
                user = User(
                    id=_id(f"user:{item['subject']}"),
                    tenant_id=tenant.id,
                    oidc_subject=item["subject"],
                )
                session.add(user)
            user.display_name = item["displayName"]
            user.email = item["email"]
            user.is_compliance_user = item["compliance"]
            users[item["subject"]] = user
        await session.flush()

        sources = sorted({item["source"] for item in fixture["objects"]})
        connections: dict[str, SourceConnection] = {}
        for source in sources:
            workspace_id = f"chronicle-fixture-{source}"
            connection = await session.scalar(
                select(SourceConnection).where(
                    SourceConnection.tenant_id == tenant.id,
                    SourceConnection.source == source,
                    SourceConnection.external_workspace_id == workspace_id,
                )
            )
            if connection is None:
                connection = SourceConnection(
                    id=_id(f"connection:{source}"),
                    tenant_id=tenant.id,
                    source=source,
                    external_workspace_id=workspace_id,
                )
                session.add(connection)
            connection.mode = "fixture"
            connection.status = "connected"
            connection.display_name = f"Chronicle {source.replace('_', ' ').title()} Fixture"
            connections[source] = connection
        await session.flush()

        identities: dict[tuple[str, str], IdentityLink] = {}
        for source, connection in connections.items():
            for subject, user in users.items():
                identity = await session.scalar(
                    select(IdentityLink).where(
                        IdentityLink.user_id == user.id,
                        IdentityLink.source_connection_id == connection.id,
                    )
                )
                boundary = _required_dt("2026-08-01T00:00:00Z")
                if identity is None:
                    identity = IdentityLink(
                        id=_id(f"identity:{source}:{subject}"),
                        tenant_id=tenant.id,
                        user_id=user.id,
                        source_connection_id=connection.id,
                        provider_subject_id=f"{source}:{subject}",
                        verified_by="fixture",
                    )
                    session.add(identity)
                identity.status = "verified"
                identity.valid_from = boundary
                identity.valid_to = None
                identity.observed_at = boundary
                identity.retired_at = None
                identities[(source, subject)] = identity
        await session.flush()

        principals: dict[tuple[str, str], Principal] = {}
        for (source, subject), identity in identities.items():
            principal = await session.scalar(
                select(Principal).where(
                    Principal.source_connection_id == identity.source_connection_id,
                    Principal.principal_type == "user",
                    Principal.provider_principal_id == identity.provider_subject_id,
                )
            )
            if principal is None:
                principal = Principal(
                    id=_id(f"principal:{source}:{subject}"),
                    tenant_id=tenant.id,
                    source_connection_id=identity.source_connection_id,
                    principal_type="user",
                    provider_principal_id=identity.provider_subject_id,
                    display_name=users[subject].display_name,
                )
                session.add(principal)
            principals[(source, subject)] = principal
        await session.flush()

        objects: dict[str, SourceObject] = {}
        settings = get_settings()
        for item in fixture["objects"]:
            source = item["source"]
            source_object = await session.scalar(
                select(SourceObject).where(
                    SourceObject.tenant_id == tenant.id,
                    SourceObject.source == source,
                    SourceObject.external_object_id == item["externalObjectId"],
                )
            )
            if source_object is None:
                source_object = SourceObject(
                    id=_id(f"object:{item['key']}"),
                    tenant_id=tenant.id,
                    source_connection_id=connections[source].id,
                    source=source,
                    external_object_id=item["externalObjectId"],
                    object_type=item["objectType"],
                )
                session.add(source_object)
                await session.flush()
            source_object.canonical_url = item["url"]
            source_object.is_deleted = False
            source_object.is_queryable = True

            version = await session.scalar(
                select(ObjectVersion).where(
                    ObjectVersion.source_object_id == source_object.id,
                    ObjectVersion.source_revision == item["revision"],
                )
            )
            if version is None:
                version = ObjectVersion(
                    id=_id(f"version:{item['key']}:{item['revision']}"),
                    tenant_id=tenant.id,
                    source_object_id=source_object.id,
                    source_revision=item["revision"],
                    normalised_title=item["title"],
                    normalised_content=item["content"],
                    content_hash=_hash(item["content"]),
                    valid_from=_dt(item["validFrom"]),
                    observed_at=_dt(item["observedAt"]),
                    ingestion_status="active",
                )
                session.add(version)
                await session.flush()
            # Preserve any later fixture event state on a repeat seed. A full
            # reset is handled by reset_demo.py; seeding alone is idempotent.
            if source_object.current_version_id is None:
                source_object.current_revision = version.source_revision
                source_object.current_version_id = version.id

            chunk = await session.scalar(
                select(Chunk).where(
                    Chunk.object_version_id == version.id,
                    Chunk.ordinal == 0,
                )
            )
            if chunk is None:
                chunk = Chunk(
                    id=_id(f"chunk:{item['key']}:{item['revision']}:0"),
                    tenant_id=tenant.id,
                    object_version_id=version.id,
                    ordinal=0,
                    section_path=[item["title"]],
                    text=item["content"],
                    embedding=deterministic_embedding(
                        item["content"], settings.EMBEDDING_DIMENSION
                    ),
                    embedding_model=settings.EMBEDDING_MODEL,
                    chunk_hash=_hash(item["content"]),
                    token_count=max(1, len(item["content"]) // 4),
                )
                session.add(chunk)

            for claim_data in item.get("claims", []):
                claim = await session.scalar(
                    select(Claim).where(
                        Claim.object_version_id == version.id,
                        Claim.subject == claim_data["subject"],
                        Claim.predicate == claim_data["predicate"],
                        Claim.object_value == claim_data["object"],
                    )
                )
                if claim is None:
                    session.add(
                        Claim(
                            id=_id(
                                "claim:"
                                f"{item['key']}:{claim_data['predicate']}:{claim_data['object']}"
                            ),
                            tenant_id=tenant.id,
                            object_version_id=version.id,
                            subject=claim_data["subject"],
                            predicate=claim_data["predicate"],
                            object_value=claim_data["object"],
                            valid_from=version.valid_from,
                            observed_at=version.observed_at,
                        )
                    )

            for subject in item["readers"]:
                principal = principals[(source, subject)]
                initial_grant_id = _id(f"grant:{item['key']}:{subject}:initial")
                grant = await session.get(AclGrant, initial_grant_id)
                if grant is None:
                    grant = AclGrant(
                        id=initial_grant_id,
                        tenant_id=tenant.id,
                        source_object_id=source_object.id,
                        principal_id=principal.id,
                        permission="read",
                        grant_effect="allow",
                        source_revision=item["revision"],
                        valid_from=_dt("2026-08-01T00:00:00Z"),
                        observed_at=_dt("2026-08-01T00:00:00Z"),
                    )
                    session.add(grant)
            objects[item["key"]] = source_object

        policy_rules = {
            "contractorsRequireExplicitSensitiveGrant": True,
            "sev1RequiresSecurityApproval": True,
        }
        policy = await session.scalar(
            select(PolicyVersion).where(
                PolicyVersion.tenant_id == tenant.id,
                PolicyVersion.version == "policy-v1",
            )
        )
        if policy is None:
            session.add(
                PolicyVersion(
                    id=_id("policy:v1"),
                    tenant_id=tenant.id,
                    version="policy-v1",
                    rules_json=policy_rules,
                    effective_at=_dt("2026-08-01T00:00:00Z"),
                    content_hash=_hash(json.dumps(policy_rules, sort_keys=True)),
                )
            )

        for item in fixture["events"]:
            event = await session.scalar(
                select(FixtureEvent).where(
                    FixtureEvent.tenant_id == tenant.id,
                    FixtureEvent.event_key == item["id"],
                )
            )
            if event is None:
                session.add(
                    FixtureEvent(
                        id=_id(f"event:{item['id']}"),
                        tenant_id=tenant.id,
                        event_key=item["id"],
                        payload=item,
                    )
                )

        for item in fixture["backtests"]:
            case = await session.scalar(
                select(BacktestCase).where(
                    BacktestCase.tenant_id == tenant.id,
                    BacktestCase.name == item["name"],
                )
            )
            if case is None:
                session.add(
                    BacktestCase(
                        id=_id(f"backtest:{item['name']}"),
                        tenant_id=tenant.id,
                        name=item["name"],
                        question=item["question"],
                        requester_subject=item["requester"],
                        as_of=_dt(item.get("asOf")),
                        expected_visibility={"externalObjectIds": item["expectedVisible"]},
                        expected_behavior=item["expectedBehavior"],
                    )
                )

        await session.commit()
        print(  # noqa: T201
            f"Seeded {fixture['fixtureVersion']} for tenant {tenant.external_org_key}: "
            f"{len(users)} users, {len(objects)} objects, {len(fixture['events'])} events"
        )

    await dispose_engine()


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
