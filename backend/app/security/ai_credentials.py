"""Authenticated encryption; the separate key is never stored in database backups."""

import base64
import os
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from app.config import Settings
from app.integrations.providers import ProviderError

AAD = b"whatsapp-memory:openai:v1"


def cipher(settings: Settings) -> AESGCM:
    try:
        if not settings.ai_credentials_key_file:
            raise ValueError("Missing path")
        key = Path(settings.ai_credentials_key_file).read_bytes()
        if len(key) != 32:
            raise ValueError("Invalid key")
        return AESGCM(key)
    except (OSError, ValueError):
        raise ProviderError("AI credential encryption key unavailable; restore its original file") from None


def encrypt_key(settings: Settings, secret: SecretStr) -> str:
    nonce = os.urandom(12)
    encrypted = cipher(settings).encrypt(nonce, secret.get_secret_value().encode(), AAD)
    return base64.b64encode(nonce + encrypted).decode()


def decrypt_key(settings: Settings, encrypted: str) -> SecretStr:
    try:
        data = base64.b64decode(encrypted, validate=True)
        return SecretStr(cipher(settings).decrypt(data[:12], data[12:], AAD).decode())
    except (ValueError, InvalidTag, UnicodeError):
        raise ProviderError("Saved AI credential cannot be decrypted; restore its original key") from None
