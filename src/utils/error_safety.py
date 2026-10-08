"""Remove recognizable credentials from workflow diagnostics."""

import re


_URL_CREDENTIALS = re.compile(r"(?i)(https?://)[^/\s]+@")
_URL_DETAILS = re.compile(r"(?i)(https?://[^\s?#]+)[?#][^\s]*")
_AUTHORIZATION = re.compile(
    r"(?i)(\b(?:proxy[-_]authorization|authorization)[\"']?\s*[:=]\s*[\"']?)"
    r"(?:bearer|basic|token)\s+[^\s,;\"']+"
)
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:[a-z0-9]+[_-])*(?:api[_-]?key|access[_-]?token|refresh[_-]?token|"
    r"token|password|passwd|secret|client[_-]?secret)[\"']?\s*[:=]\s*)"
    r"(?:\[REDACTED\]|\"[^\"]*\"|'[^']*'|[^\s,;\"'&]+)"
)
_BEARER = re.compile(r"(?i)\b(?:bearer|basic)\s+[^\s,;\"']+")
_PROVIDER_TOKEN = re.compile(
    r"\b(?:gh[pousr]_[A-Za-z0-9_]+|github_pat_[A-Za-z0-9_]+|AIza[A-Za-z0-9_-]{30,}|"
    r"sk-[A-Za-z0-9_-]{20,})\b"
)


def sanitize_error_message(message: str) -> str:
    """Redact credentials before serialization; preserve safe diagnostic text.

    Raw external exception messages may contain unknown secret formats. Prefer
    fixed messages at external service boundaries instead of copying them.
    """
    message = _URL_CREDENTIALS.sub(r"\1[REDACTED]@", message)
    message = _URL_DETAILS.sub(r"\1", message)
    message = _AUTHORIZATION.sub(r"\1[REDACTED]", message)
    message = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", message)
    message = _BEARER.sub("[REDACTED]", message)
    return _PROVIDER_TOKEN.sub("[REDACTED]", message)
