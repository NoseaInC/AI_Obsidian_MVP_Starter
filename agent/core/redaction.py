from __future__ import annotations

import hashlib
import json
import re
from typing import Any


SENSITIVE_KEY = re.compile(r"(?:authorization|api[_-]?key|token|secret|password|cookie|session)", re.I)
BEARER = re.compile(r"(?i)bearer\s+[a-z0-9._~+/=-]+")
KEY_LIKE = re.compile(r"(?i)\b(?:sk|rk|pk)-[a-z0-9_-]{8,}\b")
QUERY_SECRET = re.compile(r"(?i)([?&](?:api[_-]?key|token|secret|password|access_token)=)[^&#\s]+")


def redact_secret_text(value: str) -> str:
    """Remove secret-shaped values without replacing ordinary long prose."""

    return QUERY_SECRET.sub(r"\1[REDACTED]", KEY_LIKE.sub("[REDACTED]", BEARER.sub("Bearer [REDACTED]", value)))


def redact_model_context(value: Any) -> Any:
    """Preserve bounded model context while removing secret-bearing fields."""

    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else redact_model_context(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact_model_context(item) for item in value]
    if isinstance(value, str):
        return redact_secret_text(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)


def redact(value: Any) -> Any:
    """Return a JSON-safe structure with secrets and long private prose removed."""
    if isinstance(value, dict):
        return {
            str(key): "[REDACTED]" if SENSITIVE_KEY.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [redact(item) for item in value]
    if isinstance(value, bytes):
        return {"type": "bytes", "size": len(value), "sha256": hashlib.sha256(value).hexdigest()[:12]}
    if isinstance(value, str):
        cleaned = redact_secret_text(value)
        if len(cleaned) > 240:
            return {"type": "text", "chars": len(cleaned), "sha256": hashlib.sha256(cleaned.encode()).hexdigest()[:12]}
        return cleaned
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return str(value)[:120]


def summary(value: Any, limit: int = 600) -> str:
    encoded = json.dumps(redact(value), ensure_ascii=False, sort_keys=True)
    return encoded if len(encoded) <= limit else encoded[: limit - 1] + "…"
