"""API-key auth and PII-safe logging helpers."""
import hashlib
import hmac
import logging

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader

from .config import Settings, get_settings

_api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_api_key(
    key: str | None = Security(_api_key_header),
    settings: Settings = Depends(get_settings),
) -> str:
    # compare_digest avoids timing attacks; check every key so timing is constant
    ok = False
    for valid in settings.api_key_list:
        if key and hmac.compare_digest(key.encode(), valid.encode()):
            ok = True
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid or missing API key")
    return key


def plate_fingerprint(plate: str | None) -> str:
    """Short one-way hash so logs can correlate records without showing the plate."""
    if not plate:
        return "none"
    return hashlib.sha256(plate.encode()).hexdigest()[:10]


def get_logger(name: str) -> logging.Logger:
    logging.basicConfig(
        level=logging.INFO,
        format='{"time":"%(asctime)s","level":"%(levelname)s","logger":"%(name)s","msg":"%(message)s"}',
    )
    # botocore debug logs can include request bodies; keep them quiet
    logging.getLogger("botocore").setLevel(logging.WARNING)
    return logging.getLogger(name)
