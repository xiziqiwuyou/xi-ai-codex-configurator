from __future__ import annotations

from collections.abc import Iterable


REDACTED = "<redacted>"


def redact(value: object, secrets: Iterable[str] = ()) -> str:
    text = str(value)
    for secret in secrets:
        if secret:
            text = text.replace(secret, REDACTED)
    return text
