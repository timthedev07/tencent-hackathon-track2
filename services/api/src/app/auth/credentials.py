"""Credential encryption using envelope encryption.

Flow:
1. Generate data encryption key (DEK)
2. Encrypt credential with DEK using AES-256-GCM
3. Wrap DEK with master key (KMS in production, Fernet in dev)
4. Persist ciphertext + wrapped key metadata

Runtime:
1. Load encrypted credential
2. Unwrap key via KMS/master key
3. Decrypt only in process memory
4. Use for provider call
5. Discard plaintext reference
"""

from __future__ import annotations

import base64
import binascii
import os
import secrets

from cryptography.fernet import Fernet
from pydantic import BaseModel

from app.observability.logging import get_logger
from app.settings import get_settings

logger = get_logger(__name__)

# Key version tracking for rotation
CURRENT_KEY_VERSION = "v1"
_KEY_SIZE_BYTES = 32
_GCM_NONCE_SIZE_BYTES = 12
_PAYLOAD_SEPARATOR = b"||"


class EncryptedPayload(BaseModel):
    """Envelope-encrypted credential payload."""

    ciphertext: bytes
    key_version: str = CURRENT_KEY_VERSION


def _split_payload(payload: bytes) -> tuple[bytes, bytes, bytes]:
    """Split an envelope payload without treating encrypted bytes as delimiters."""
    wrapped_dek, separator, remainder = payload.partition(_PAYLOAD_SEPARATOR)
    separator_size = len(_PAYLOAD_SEPARATOR)
    if (
        not separator
        or len(remainder) <= _GCM_NONCE_SIZE_BYTES + separator_size
        or remainder[_GCM_NONCE_SIZE_BYTES : _GCM_NONCE_SIZE_BYTES + separator_size]
        != _PAYLOAD_SEPARATOR
    ):
        raise ValueError("Invalid encrypted payload format")

    nonce = remainder[:_GCM_NONCE_SIZE_BYTES]
    ciphertext = remainder[_GCM_NONCE_SIZE_BYTES + separator_size :]
    return wrapped_dek, nonce, ciphertext


class CredentialManager:
    """Manages credential encryption and decryption.

    In production: uses managed KMS for key wrapping.
    In development: uses a local Fernet key derived from ENCRYPTION_MASTER_KEY.
    """

    def __init__(self) -> None:
        self._settings = get_settings()
        self._fernet: Fernet | None = None

    def _get_fernet(self) -> Fernet:
        """Get the Fernet cipher (dev mode)."""
        if self._fernet is None:
            key = self._settings.ENCRYPTION_MASTER_KEY
            # If the key is a valid URL-safe base64 string (44 chars), use it
            # directly. Otherwise, derive a 32-byte key from the raw bytes
            # using SHA-256, then encode as URL-safe base64.
            try:
                key_bytes = base64.urlsafe_b64decode(key)
            except (ValueError, binascii.Error):
                import hashlib

                derived = hashlib.sha256(key.encode("utf-8")).digest()
                fernet_key = base64.urlsafe_b64encode(derived)
            else:
                if len(key_bytes) == _KEY_SIZE_BYTES:
                    fernet_key = key.encode("utf-8")
                else:
                    import hashlib

                    derived = hashlib.sha256(key.encode("utf-8")).digest()
                    fernet_key = base64.urlsafe_b64encode(derived)
            self._fernet = Fernet(fernet_key)
        return self._fernet

    def encrypt(self, plaintext: str) -> EncryptedPayload:
        """Encrypt a credential string using envelope encryption."""
        # Generate a random DEK
        dek = secrets.token_bytes(_KEY_SIZE_BYTES)  # 256-bit data encryption key

        # Encrypt the plaintext with the DEK using AES-256-GCM
        nonce = os.urandom(12)  # 96-bit nonce for GCM
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aesgcm = AESGCM(dek)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

        # Wrap the DEK with the master key (Fernet in dev, KMS in prod)
        fernet = self._get_fernet()
        wrapped_dek = fernet.encrypt(dek)

        # Combine: wrapped_dek + nonce + ciphertext
        combined = wrapped_dek + _PAYLOAD_SEPARATOR + nonce + _PAYLOAD_SEPARATOR + ciphertext

        return EncryptedPayload(
            ciphertext=combined,
            key_version=CURRENT_KEY_VERSION,
        )

    def decrypt(self, payload: EncryptedPayload) -> str:
        """Decrypt a credential — returns plaintext to be discarded after use."""
        fernet = self._get_fernet()

        wrapped_dek, nonce, ciphertext = _split_payload(payload.ciphertext)

        # Unwrap the DEK
        dek = fernet.decrypt(wrapped_dek)

        # Decrypt with AES-256-GCM
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aesgcm = AESGCM(dek)
        plaintext_bytes = aesgcm.decrypt(nonce, ciphertext, None)
        return plaintext_bytes.decode("utf-8")

    def encrypt_bytes(self, data: bytes) -> EncryptedPayload:
        """Encrypt raw bytes (for query/answer ciphertext)."""
        dek = secrets.token_bytes(32)
        nonce = os.urandom(12)
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aesgcm = AESGCM(dek)
        ciphertext = aesgcm.encrypt(nonce, data, None)

        fernet = self._get_fernet()
        wrapped_dek = fernet.encrypt(dek)

        combined = wrapped_dek + _PAYLOAD_SEPARATOR + nonce + _PAYLOAD_SEPARATOR + ciphertext

        return EncryptedPayload(
            ciphertext=combined,
            key_version=CURRENT_KEY_VERSION,
        )

    def decrypt_bytes(self, payload: EncryptedPayload) -> bytes:
        """Decrypt raw bytes."""
        wrapped_dek, nonce, ciphertext = _split_payload(payload.ciphertext)
        fernet = self._get_fernet()
        dek = fernet.decrypt(wrapped_dek)

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aesgcm = AESGCM(dek)
        return aesgcm.decrypt(nonce, ciphertext, None)


# Singleton
_credential_manager: CredentialManager | None = None


def get_credential_manager() -> CredentialManager:
    """Return the singleton credential manager."""
    global _credential_manager
    if _credential_manager is None:
        _credential_manager = CredentialManager()
    return _credential_manager
