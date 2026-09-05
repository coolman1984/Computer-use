"""Keep review metadata useful without exposing authentication or payload data."""
from __future__ import annotations
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# Word-boundary matching, not a bare substring: an unanchored search on "key"
# or "session" flags routine identifiers as secrets — Nexacro-style ids are
# dotted/camelCase and pack many words together (e.g. "grdKeyword",
# "divCategory.form.tabDiv_Org"), and without \b those all contain "key" as a
# substring even though nothing sensitive is there. \b still catches the
# standalone words these fields actually care about ("password", "id=session").
_SECRET = re.compile(r"\b(password|passwd|token|cookie|authorization|session|otp|secret|key)\b", re.I)

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
