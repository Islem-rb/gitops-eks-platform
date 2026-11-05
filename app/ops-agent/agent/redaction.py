"""Remove secrets and user data before evidence leaves the cluster.

This is a safety net, not a guarantee: it catches known shapes. The RBAC already keeps Secrets out
of reach, and the collectors only keep short log samples, so little sensitive data should arrive.
"""

import re
from typing import Any

MASK = "[REDACTED]"
# Values under these keys are never sent, whatever they look like
SENSITIVE_KEYS = re.compile(r"password|passwd|secret|token|api[_-]?key|authorization|payload", re.I)
PATTERNS = [
    # scheme://user:password@host, keeping the host because it helps diagnosis
    (re.compile(r"(\b[a-z][a-z0-9+.-]*://)[^\s/:@]*:[^\s/@]+@", re.I), rf"\1{MASK}@"),
    (re.compile(r"https://discord(?:app)?\.com/api/webhooks/\S+", re.I), MASK),
    (re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]+", re.I), f"Bearer {MASK}"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"), MASK),
    (re.compile(r"\b(?:sk-ant-[A-Za-z0-9_-]+|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16})"), MASK),
    (
        re.compile(
            r"\b(password|passwd|pwd|secret|token|api[_-]?key)\b(\s*[=:]\s*)[\"']?[^\s\"',;]+",
            re.I,
        ),
        rf"\1\2{MASK}",
    ),
    # Postgres repeats row values here, and job payloads are user data
    (re.compile(r"\bDETAIL:.*", re.I), f"DETAIL: {MASK}"),
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}\b"), MASK),
]


def redact_text(text: str) -> str:
    for pattern, replacement in PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def redact(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, dict):
        return {
            key: MASK if SENSITIVE_KEYS.search(str(key)) else redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
