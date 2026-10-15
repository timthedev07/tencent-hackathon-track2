from __future__ import annotations

from app.auth.credentials import CredentialManager, EncryptedPayload
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def test_decrypts_existing_payload_with_separator_in_nonce() -> None:
    """Existing payloads remain readable when their nonce contains ``||``."""
    manager = CredentialManager()
    dek = b"a" * 32
    nonce = b"first||lasts"
    plaintext = "atlassian-refresh-token"
    wrapped_dek = manager._get_fernet().encrypt(dek)
    ciphertext = AESGCM(dek).encrypt(nonce, plaintext.encode(), None)
    payload = EncryptedPayload(ciphertext=wrapped_dek + b"||" + nonce + b"||" + ciphertext)

    assert manager.decrypt(payload) == plaintext
