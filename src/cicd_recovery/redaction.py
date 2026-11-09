"""Best-effort redaction for log evidence before reports are persisted."""

from __future__ import annotations

import re

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(authorization\s*:\s*bearer\s+)[^\s,;]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(\bBearer\s+)[A-Za-z0-9._~+/=-]{12,}"), r"\1[REDACTED]"),
    (re.compile(r"\b(gh[pousr]_[A-Za-z0-9_]{20,})\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\b(github_pat_[A-Za-z0-9_]{20,})\b"), "[REDACTED_GITHUB_TOKEN]"),
    (re.compile(r"\b(AKIA[0-9A-Z]{16})\b"), "[REDACTED_AWS_ACCESS_KEY]"),
    (
        re.compile(r"(?i)(\b(?:password|passwd|client_secret|api_key|access_token)\s*[=:]\s*)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
    (
        re.compile(r"(?i)(\baws_secret_access_key\s*[=:]\s*)[^\s,;]+"),
        r"\1[REDACTED]",
    ),
)


def redact_text(text: str) -> str:
    """Redact common credential patterns. This is not a substitute for GitHub secret masking."""
    redacted = text
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted
