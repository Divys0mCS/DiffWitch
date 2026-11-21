"""Logging helpers.

Logs must never contain credentials, tokens or full private keys, so every
module uses these helpers instead of printing raw values.
"""

from __future__ import annotations

import logging
import re
import sys

_REDACTIONS = (
    re.compile(r"(?i)(authorization|bearer)\s*[:=]?\s*[A-Za-z0-9._\-+/=]{8,}"),
    re.compile(r"(?i)(x-hub-signature-256)\s*[:=]?\s*[a-f0-9]{16,}"),
    re.compile(r"(?i)(api[_-]?key|secret|password|token)=([^\s&\"']+)"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bgh[psru]_[A-Za-z0-9]{20,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z\-_]{30,}\b"),
)


def redact(value: object) -> str:
    """Return ``value`` as text with anything secret-looking masked."""
    text = value if isinstance(value, str) else repr(value)
    for pattern in _REDACTIONS:
        text = pattern.sub(
            lambda m: (m.group(1) + "=[redacted]") if m.groups() else "[redacted]", text
        )
    return text


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s %(levelname)-8s %(name)s :: %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S%z",
        )
    )
    root.addHandler(handler)
    root.setLevel(level)
    # httpx logs every request URL at INFO, which can leak signed URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
