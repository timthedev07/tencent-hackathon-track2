from __future__ import annotations

import base64
import hashlib
from types import SimpleNamespace

import pytest
from app.auth import credentials
from cryptography.fernet import Fernet


def _manager_with_key(monkeypatch: pytest.MonkeyPatch, key: str) -> credentials.CredentialManager:
    settings = SimpleNamespace(ENCRYPTION_MASTER_KEY=key)
    monkeypatch.setattr(credentials, "get_settings", lambda: settings)
    return credentials.CredentialManager()


def test_valid_fernet_key_is_used_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    key = Fernet.generate_key()
    manager = _manager_with_key(monkeypatch, key.decode("ascii"))

    token = manager._get_fernet().encrypt(b"credential")

    assert Fernet(key).decrypt(token) == b"credential"


def test_invalid_fernet_key_uses_derived_key(monkeypatch: pytest.MonkeyPatch) -> None:
    raw_key = "development-key"
    expected_key = base64.urlsafe_b64encode(hashlib.sha256(raw_key.encode("utf-8")).digest())
    manager = _manager_with_key(monkeypatch, raw_key)

    token = manager._get_fernet().encrypt(b"credential")

    assert Fernet(expected_key).decrypt(token) == b"credential"
