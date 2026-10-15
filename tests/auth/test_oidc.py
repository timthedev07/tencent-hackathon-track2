from __future__ import annotations

from app.auth.oidc import _is_dev_compliance_subject


def test_only_carol_has_the_development_compliance_role() -> None:
    assert _is_dev_compliance_subject("carol")
    assert _is_dev_compliance_subject("CAROL")
    assert not _is_dev_compliance_subject("alice")
    assert not _is_dev_compliance_subject("compliance")
