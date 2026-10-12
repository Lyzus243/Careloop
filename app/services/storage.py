"""Receipt storage. Local disk for Phase 3; swap to R2 in Phase 4 by
implementing the same three functions."""

import os
import secrets

RECEIPTS_DIR = os.getenv("RECEIPTS_DIR", "receipts")
ALLOWED_EXT = {".jpg", ".jpeg", ".png"}
MAX_BYTES = 5 * 1024 * 1024


def _ensure_dir():
    os.makedirs(RECEIPTS_DIR, exist_ok=True)


def save_receipt(filename: str, data: bytes) -> str:
    """Store a receipt and return its key (relative path). Raises ValueError on invalid input."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("Receipt must be a JPG or PNG image")
    if len(data) > MAX_BYTES:
        raise ValueError("Receipt must be 5MB or smaller")
    _ensure_dir()
    key = secrets.token_urlsafe(20) + ext
    with open(os.path.join(RECEIPTS_DIR, key), "wb") as f:
        f.write(data)
    return key


def receipt_path(key: str) -> str | None:
    """Return the absolute path for a receipt key, or None if it doesn't exist."""
    if not key or "/" in key or "\\" in key or ".." in key:
        return None
    p = os.path.join(RECEIPTS_DIR, key)
    return p if os.path.isfile(p) else None


def delete_receipt(key: str) -> None:
    p = receipt_path(key)
    if p:
        try: os.remove(p)
        except OSError: pass
