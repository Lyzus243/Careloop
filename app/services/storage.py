"""Receipt storage. Two backends behind one interface:

  - local (default): writes to RECEIPTS_DIR on disk. No cloud credentials needed.
  - s3: Cloudflare R2, Supabase Storage, Backblaze B2, AWS S3 — any S3-compatible
    store. Enabled by setting STORAGE_BACKEND=s3 plus the STORAGE_* env vars.

The three core functions stay stable so the rest of the app never knows which
backend is active. Presigned URLs let the browser upload directly to storage
and download receipts without routing bytes through FastAPI.
"""

import os
import secrets
import mimetypes

STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local").lower()
RECEIPTS_DIR = os.getenv("RECEIPTS_DIR", "receipts")
ALLOWED_EXT = {".jpg", ".jpeg", ".png"}
ALLOWED_MIME = {"image/jpeg", "image/png"}
MAX_BYTES = 5 * 1024 * 1024

S3_ENDPOINT = os.getenv("STORAGE_ENDPOINT")        # e.g. https://<acct>.r2.cloudflarestorage.com
S3_ACCESS_KEY = os.getenv("STORAGE_ACCESS_KEY")
S3_SECRET_KEY = os.getenv("STORAGE_SECRET_KEY")
S3_BUCKET = os.getenv("STORAGE_BUCKET")
S3_REGION = os.getenv("STORAGE_REGION", "auto")


# ──────────────────────────────────────────────────────────────────
# Local-disk backend
# ──────────────────────────────────────────────────────────────────

def _local_ensure_dir():
    os.makedirs(RECEIPTS_DIR, exist_ok=True)


def _local_save(filename: str, data: bytes) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("Receipt must be a JPG or PNG image")
    if len(data) > MAX_BYTES:
        raise ValueError("Receipt must be 5MB or smaller")
    _local_ensure_dir()
    key = secrets.token_urlsafe(20) + ext
    with open(os.path.join(RECEIPTS_DIR, key), "wb") as f:
        f.write(data)
    return key


def _local_path(key: str) -> str | None:
    if not key or "/" in key or "\\" in key or ".." in key:
        return None
    p = os.path.join(RECEIPTS_DIR, key)
    return p if os.path.isfile(p) else None


def _local_delete(key: str) -> None:
    p = _local_path(key)
    if p:
        try: os.remove(p)
        except OSError: pass


# ──────────────────────────────────────────────────────────────────
# S3-compatible backend (R2, Supabase, AWS, Backblaze)
# ──────────────────────────────────────────────────────────────────

_s3_client = None


def _s3():
    global _s3_client
    if _s3_client is not None:
        return _s3_client
    if not all([S3_ENDPOINT, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET]):
        raise RuntimeError(
            "STORAGE_BACKEND=s3 requires STORAGE_ENDPOINT, STORAGE_ACCESS_KEY, "
            "STORAGE_SECRET_KEY and STORAGE_BUCKET to be set."
        )
    import boto3
    from botocore.config import Config
    _s3_client = boto3.client(
        "s3",
        endpoint_url=S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        region_name=S3_REGION,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )
    return _s3_client


def _s3_save(filename: str, data: bytes) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("Receipt must be a JPG or PNG image")
    if len(data) > MAX_BYTES:
        raise ValueError("Receipt must be 5MB or smaller")
    mime = mimetypes.types_map.get(ext, "application/octet-stream")
    key = "receipts/" + secrets.token_urlsafe(20) + ext
    _s3().put_object(Bucket=S3_BUCKET, Key=key, Body=data, ContentType=mime)
    return key


def _s3_delete(key: str) -> None:
    if not key:
        return
    try:
        _s3().delete_object(Bucket=S3_BUCKET, Key=key)
    except Exception:
        pass


# ──────────────────────────────────────────────────────────────────
# Public interface
# ──────────────────────────────────────────────────────────────────

def save_receipt(filename: str, data: bytes) -> str:
    """Store a receipt, return its key. Raises ValueError on invalid input."""
    if STORAGE_BACKEND == "s3":
        return _s3_save(filename, data)
    return _local_save(filename, data)


def receipt_path(key: str) -> str | None:
    """Local-disk only: absolute path for a receipt, or None. On S3 always None
    — use presigned_download_url() to serve the file."""
    if STORAGE_BACKEND == "s3":
        return None
    return _local_path(key)


def delete_receipt(key: str) -> None:
    if STORAGE_BACKEND == "s3":
        return _s3_delete(key)
    return _local_delete(key)


def presigned_upload_url(filename: str, content_type: str) -> tuple[str, str]:
    """Return (url, key) so the browser can PUT the file directly to storage.
    Only available on S3. On local backend raises RuntimeError — the client
    should fall back to multipart upload to /orders."""
    if STORAGE_BACKEND != "s3":
        raise RuntimeError("Presigned uploads need STORAGE_BACKEND=s3")
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXT:
        raise ValueError("Receipt must be a JPG or PNG image")
    if content_type not in ALLOWED_MIME:
        raise ValueError("Receipt content-type must be image/jpeg or image/png")
    key = "receipts/" + secrets.token_urlsafe(20) + ext
    url = _s3().generate_presigned_url(
        "put_object",
        Params={
            "Bucket": S3_BUCKET,
            "Key": key,
            "ContentType": content_type,
            "ContentLength": MAX_BYTES,  # upper bound; smaller files still accepted
        },
        ExpiresIn=600,
    )
    return url, key


def presigned_download_url(key: str, ttl: int = 900) -> str | None:
    """Return a short-lived URL for reading a receipt. On local backend
    returns None — the caller falls back to serving the file from disk."""
    if STORAGE_BACKEND != "s3":
        return None
    if not key:
        return None
    try:
        return _s3().generate_presigned_url(
            "get_object",
            Params={"Bucket": S3_BUCKET, "Key": key},
            ExpiresIn=ttl,
        )
    except Exception:
        return None


def backend_name() -> str:
    return STORAGE_BACKEND


# ──────────────────────────────────────────────────────────────────
# Product images
# ──────────────────────────────────────────────────────────────────

import re as _re

PRODUCT_IMAGE_MAX_BYTES = 2 * 1024 * 1024
_KEY_RE = _re.compile(r"^[A-Za-z0-9_\-]{16,64}\.(jpg|jpeg|png)$")
_S3_PREFIX = "products/"
_LOCAL_SUBDIR = "product_images"


def _safe_product_key(key):
    if not key or "/" in key or "\\" in key or ".." in key:
        return None
    if not _KEY_RE.match(key):
        return None
    return key


def _ext_of(filename):
    return os.path.splitext(filename or "")[1].lower()


def save_product_image(filename: str, data: bytes) -> str:
    """Store a product image and return its key (bare token.ext, no prefix)."""
    ext = _ext_of(filename)
    if ext not in ALLOWED_EXT:
        raise ValueError("Image must be a JPG or PNG")
    if len(data) > PRODUCT_IMAGE_MAX_BYTES:
        raise ValueError("Image must be 2MB or smaller")
    key = secrets.token_urlsafe(20).replace("-", "").replace("_", "")[:24] + ext
    if STORAGE_BACKEND == "s3":
        mime = mimetypes.types_map.get(ext, "image/jpeg")
        _s3().put_object(Bucket=S3_BUCKET, Key=_S3_PREFIX + key, Body=data, ContentType=mime)
    else:
        d = os.path.join(RECEIPTS_DIR, _LOCAL_SUBDIR)
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, key), "wb") as f:
            f.write(data)
    return key


def read_product_image(key: str):
    """Return (bytes, content_type) or (None, None)."""
    k = _safe_product_key(key)
    if not k:
        return None, None
    ext = _ext_of(k)
    mime = mimetypes.types_map.get(ext, "image/jpeg")
    if STORAGE_BACKEND == "s3":
        try:
            r = _s3().get_object(Bucket=S3_BUCKET, Key=_S3_PREFIX + k)
            return r["Body"].read(), mime
        except Exception:
            return None, None
    p = os.path.join(RECEIPTS_DIR, _LOCAL_SUBDIR, k)
    if not os.path.isfile(p):
        return None, None
    with open(p, "rb") as f:
        return f.read(), mime


def delete_product_image(key: str) -> None:
    k = _safe_product_key(key)
    if not k:
        return
    if STORAGE_BACKEND == "s3":
        try:
            _s3().delete_object(Bucket=S3_BUCKET, Key=_S3_PREFIX + k)
        except Exception:
            pass
        return
    p = os.path.join(RECEIPTS_DIR, _LOCAL_SUBDIR, k)
    if os.path.isfile(p):
        try: os.remove(p)
        except OSError: pass
