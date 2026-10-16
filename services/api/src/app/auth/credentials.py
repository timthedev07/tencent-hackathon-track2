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
import os
import secrets
import struct

from cryptography.fernet import Fernet
from pydantic import BaseModel

from app.observability.logging import get_logger
from app.settings import get_settings

logger = get_logger(__name__)

# Key version tracking for rotation
CURRENT_KEY_VERSION = "v1"
_EXPECTED_PAYLOAD_PARTS = 3  # wrapped_dek || nonce || ciphertext

# Length-prefix format: 4-byte big-endian length + data, repeated for each part.
# This replaces the old b"||" delimiter which could collide with raw ciphertext
# bytes and cause intermittent decryption failures (500 errors).
_LENGTH_PREFIX = struct.Struct(">I")


def _pack_parts(*parts: bytes) -> bytes:
    """Combine multiple byte segments using length-prefixed encoding."""
    chunks: list[bytes] = []
    for part in parts:
        chunks.append(_LENGTH_PREFIX.pack(len(part)))
        chunks.append(part)
    return b"".join(chunks)


def _unpack_parts(data: bytes) -> tuple[bytes, ...]:
    """Split length-prefixed payload into its parts.

    Falls back to the legacy b"||" delimiter for rows encrypted before
    the format change, so existing data in the DB stays readable.
    """
    # Try length-prefixed format first.
    result: list[bytes] = []
    offset = 0
    try:
        for _ in range(_EXPECTED_PAYLOAD_PARTS):
            if offset + 4 > len(data):
                raise ValueError("truncated length prefix")
            (size,) = _LENGTH_PREFIX.unpack_from(data, offset)
            offset += 4
            if offset + size > len(data):
                raise ValueError("truncated part data")
            result.append(data[offset : offset + size])
            offset += size
        if offset != len(data):
            # Trailing bytes — not a clean length-prefixed payload.
            raise ValueError("trailing bytes")
        return tuple(result)
    except ValueError:
        pass

    # Legacy fallback: b"||"-delimited format.
    parts = data.split(b"||")
    if len(parts) != _EXPECTED_PAYLOAD_PARTS:
        raise ValueError("Invalid encrypted payload format")
    return tuple(parts)


class EncryptedPayload(BaseModel):
    """Envelope-encrypted credential payload."""

    ciphertext: bytes
    key_version: str = CURRENT_KEY_VERSION


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
                if len(key_bytes) != 32:
                    raise ValueError
                fernet_key = key.encode("utf-8")
            except (ValueError, base64.binascii.Error):
                import hashlib

                derived = hashlib.sha256(key.encode("utf-8")).digest()
                fernet_key = base64.urlsafe_b64encode(derived)
            self._fernet = Fernet(fernet_key)
        return self._fernet

    def encrypt(self, plaintext: str) -> EncryptedPayload:
        """Encrypt a credential string using envelope encryption."""
        # Generate a random DEK
        dek = secrets.token_bytes(32)  # 256-bit data encryption key

        # Encrypt the plaintext with the DEK using AES-256-GCM
        nonce = os.urandom(12)  # 96-bit nonce for GCM
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        aesgcm = AESGCM(dek)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)

        # Wrap the DEK with the master key (Fernet in dev, KMS in prod)
        fernet = self._get_fernet()
        wrapped_dek = fernet.encrypt(dek)

        # Combine: wrapped_dek + nonce + ciphertext using length-prefixed
        # encoding to avoid delimiter collision with raw bytes.
        combined = _pack_parts(wrapped_dek, nonce, ciphertext)

        return EncryptedPayload(
            ciphertext=combined,
            key_version=CURRENT_KEY_VERSION,
        )

    def decrypt(self, payload: EncryptedPayload) -> str:
        """Decrypt a credential — returns plaintext to be discarded after use."""
        fernet = self._get_fernet()

        # Split the combined payload
        wrapped_dek, nonce, ciphertext = _unpack_parts(payload.ciphertext)

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

        combined = _pack_parts(wrapped_dek, nonce, ciphertext)

        return EncryptedPayload(
            ciphertext=combined,
            key_version=CURRENT_KEY_VERSION,
        )

    def decrypt_bytes(self, payload: EncryptedPayload) -> bytes:
        """Decrypt raw bytes."""
        wrapped_dek, nonce, ciphertext = _unpack_parts(payload.ciphertext)
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
