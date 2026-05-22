"""
BinBot AI Auto Mode — AES Encryption Service
Encrypts/decrypts Binance API keys at rest using AES-256-GCM.
"""

import os
import logging
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import settings

logger = logging.getLogger(__name__)

# AES-256 requires a 32-byte key
_KEY_BYTES = 32
_NONCE_BYTES = 12  # GCM standard nonce size


def _get_key() -> bytes:
    """Derive the encryption key from settings."""
    raw = settings.ENCRYPTION_KEY.encode("utf-8")
    # Pad or truncate to 32 bytes
    if len(raw) < _KEY_BYTES:
        raw = raw.ljust(_KEY_BYTES, b"\0")
    return raw[:_KEY_BYTES]


def encrypt(plaintext: str) -> bytes:
    """
    Encrypt a plaintext string using AES-256-GCM.
    Returns: nonce (12 bytes) + ciphertext+tag
    """
    key = _get_key()
    nonce = os.urandom(_NONCE_BYTES)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    return nonce + ciphertext


def decrypt(data: bytes) -> str:
    """
    Decrypt AES-256-GCM encrypted data.
    Input: nonce (12 bytes) + ciphertext+tag
    Returns: plaintext string
    """
    key = _get_key()
    nonce = data[:_NONCE_BYTES]
    ciphertext = data[_NONCE_BYTES:]
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    return plaintext.decode("utf-8")
