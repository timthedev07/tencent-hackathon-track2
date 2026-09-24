"""Canonical JSON serialisation for audit events.

Before hashing:
- serialise keys deterministically (sorted)
- use stable timestamp formatting
- avoid nondeterministic map ordering
- include event type and sequence number
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

GENESIS_MARKER = "genesis-v1-00000000"


def canonical_json(data: dict[str, Any]) -> str:
    """Serialise to canonical JSON with sorted keys and stable formatting."""
    return json.dumps(data, sort_keys=True, ensure_ascii=False, default=str)


def compute_event_hash(event: Any, previous_hash: str) -> str:
    """Compute the SHA-256 hash for an audit event.

    event_hash_n = SHA-256(canonical_json(event_n) || previous_event_hash)
    """
    payload = {
        "event_type": event.event_type,
        "sequence_no": event.sequence_no,
        "tenant_id": str(event.tenant_id),
        "query_id": str(event.query_id) if event.query_id else None,
        "canonical_payload": event.canonical_payload,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }
    canonical = canonical_json(payload)
    combined = f"{canonical}{previous_hash}"
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()
