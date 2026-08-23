"""Sanitize benchmark command metadata before it enters retained artifacts."""

from __future__ import annotations

from collections.abc import Sequence
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
_URL_FLAGS = frozenset({"--database-url"})
_LABELED_URL_FLAGS = frozenset({"--boundary"})


def _sanitize_url(value: str) -> str:
    parsed = urlsplit(value)
    if not parsed.scheme or not parsed.netloc:
        return REDACTED

    host = parsed.netloc.rsplit("@", 1)[-1]
    redacted_query = urlencode(
        [(key, REDACTED) for key, _value in parse_qsl(parsed.query, keep_blank_values=True)],
        doseq=True,
    )
    return urlunsplit((parsed.scheme, host, parsed.path, redacted_query, ""))


def _sanitize_flag_value(flag: str, value: str) -> str:
    if flag in _URL_FLAGS:
        return _sanitize_url(value)
    if flag in _LABELED_URL_FLAGS:
        label, separator, url = value.partition("=")
        if not separator:
            return REDACTED
        return f"{label}={_sanitize_url(url)}"
    return value


def sanitized_command(arguments: Sequence[str]) -> list[str]:
    """Return argv with credentials removed from sensitive URL-valued flags."""

    sanitized: list[str] = []
    index = 0
    sensitive_flags = _URL_FLAGS | _LABELED_URL_FLAGS
    while index < len(arguments):
        argument = arguments[index]
        if argument in sensitive_flags:
            sanitized.append(argument)
            if index + 1 < len(arguments):
                sanitized.append(_sanitize_flag_value(argument, arguments[index + 1]))
                index += 2
                continue
            index += 1
            continue

        flag, separator, value = argument.partition("=")
        if separator and flag in sensitive_flags:
            sanitized.append(f"{flag}={_sanitize_flag_value(flag, value)}")
        else:
            sanitized.append(argument)
        index += 1
    return sanitized
