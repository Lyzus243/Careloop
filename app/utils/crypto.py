"""Symmetric encryption for third-party access tokens stored at rest.

The WhatsApp access token is a long-lived credential that can send messages on a
vendor's behalf, so it is never stored in plaintext. The key lives in the
WHATSAPP_TOKEN_ENCRYPTION_KEY env var and is generated once with:

    python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

If the key is missing the module degrades gracefully (fernet is None) and the
WhatsApp service disables itself, mirroring how email_service handles a missing
API key.
"""
import os
import logging

from dotenv import load_dotenv

if os.path.exists(".env"):
    load_dotenv(dotenv_path=".env", override=False)

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:  # pragma: no cover - cryptography ships with python-jose
    Fernet = None
    InvalidToken = Exception

_KEY = os.getenv("WHATSAPP_TOKEN_ENCRYPTION_KEY")

fernet = None
if _KEY and Fernet is not None:
    try:
        fernet = Fernet(_KEY.encode() if isinstance(_KEY, str) else _KEY)
    except Exception as e:
        print(f"WARNING: WHATSAPP_TOKEN_ENCRYPTION_KEY is not a valid Fernet key: {e}")
        fernet = None
elif not _KEY:
    print("WARNING: WHATSAPP_TOKEN_ENCRYPTION_KEY not set - WhatsApp integration disabled")


class EncryptionUnavailable(RuntimeError):
    """Raised when encrypt/decrypt is attempted without a configured key."""


def is_available() -> bool:
    return fernet is not None


def encrypt(plaintext: str) -> str:
    """Encrypt a token for storage. Never log the input or the output."""
    if fernet is None:
        raise EncryptionUnavailable("WHATSAPP_TOKEN_ENCRYPTION_KEY is not configured")
    if plaintext is None:
        raise ValueError("Cannot encrypt None")
    return fernet.encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(ciphertext: str) -> str:
    """Decrypt a stored token. Raises EncryptionUnavailable if the key changed."""
    if fernet is None:
        raise EncryptionUnavailable("WHATSAPP_TOKEN_ENCRYPTION_KEY is not configured")
    if not ciphertext:
        raise EncryptionUnavailable("No stored token to decrypt")
    try:
        return fernet.decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except InvalidToken as e:
        raise EncryptionUnavailable(
            "Stored WhatsApp token could not be decrypted (encryption key changed?)"
        ) from e
