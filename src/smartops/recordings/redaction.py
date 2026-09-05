"""Keep review metadata useful without exposing authentication or payload data."""
from __future__ import annotations
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_SECRET = re.compile(r"(password|passwd|token|cookie|authorization|session|otp|secret|key)", re.I)

def redact_text(value: str, limit: int = 160) -> str:
    if not value or _SECRET.search(value): return "[redacted]"
    return value[:limit]

def redact_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        query = urlencode([(k, "[redacted]" if _SECRET.search(k) else v) for k, v in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))
    except ValueError:
        return "[redacted]"

def safe_network_summary(method: str, url: str, resource_type: str, status: int | None = None) -> dict[str, object]:
    return {"method": method, "url": redact_url(url), "resource_type": resource_type, "status": status}
