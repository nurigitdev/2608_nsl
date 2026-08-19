from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Mapping


REDACTED = "[REDACTED]"

_CREDENTIAL_KEYS = frozenset(
    {
        "accesskey",
        "accesstoken",
        "apikey",
        "authorization",
        "clientsecret",
        "cookie",
        "credential",
        "credentials",
        "idtoken",
        "password",
        "passwd",
        "privatekey",
        "pwd",
        "refreshtoken",
        "secret",
        "sessiontoken",
        "token",
    }
)
_CREDENTIAL_LABEL = (
    r"access[_-]?key|access[_-]?token|api[_-]?key|authorization|"
    r"client[_-]?secret|cookie|credentials?|id[_-]?token|password|passwd|"
    r"private[_-]?key|pwd|refresh[_-]?token|secret|session[_-]?token|token"
)
_KEY_VALUE_PATTERN = re.compile(
    rf"(?i)\b({_CREDENTIAL_LABEL})\b(\s*[:=]\s*)"
    r"(\"[^\"\r\n]*\"|'[^'\r\n]*'|(?:Bearer|Basic)\s+[^\s,;\]}]+|"
    r"[^\s,;\]}]+)"
)
_AUTH_SCHEME_PATTERN = re.compile(
    r"(?i)\b(Bearer|Basic)\s+[A-Za-z0-9._~+/=-]+"
)
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN(?: [A-Z0-9]+)* PRIVATE KEY-----.*?"
    r"-----END(?: [A-Z0-9]+)* PRIVATE KEY-----",
    re.DOTALL,
)
_JWT_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]+\."
    r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![A-Za-z0-9_-])"
)


class CredentialMaterialError(ValueError):
    pass


def _normalized_key(key: object) -> str:
    return re.sub(r"[^a-z0-9]", "", str(key).lower())


def is_credential_key(key: object) -> bool:
    return _normalized_key(key) in _CREDENTIAL_KEYS


def redact_text(text: str, sensitive_values: tuple[str, ...] = ()) -> str:
    redacted = text
    for value in sorted(set(sensitive_values), key=len, reverse=True):
        if value:
            redacted = redacted.replace(value, REDACTED)
    redacted = _PRIVATE_KEY_PATTERN.sub(REDACTED, redacted)
    redacted = _KEY_VALUE_PATTERN.sub(
        lambda match: f"{match.group(1)}{match.group(2)}{REDACTED}",
        redacted,
    )
    redacted = _AUTH_SCHEME_PATTERN.sub(
        lambda match: f"{match.group(1)} {REDACTED}", redacted
    )
    return _JWT_PATTERN.sub(REDACTED, redacted)


def redact_data(value: Any) -> Any:
    if isinstance(value, Mapping):
        redacted: dict[Any, Any] = {}
        for key, item in value.items():
            safe_key = redact_text(key) if isinstance(key, str) else key
            redacted[safe_key] = (
                REDACTED if is_credential_key(key) else redact_data(item)
            )
        return redacted
    if isinstance(value, list):
        return [redact_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_data(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def find_credential_path(value: Any, path: str = "$") -> str | None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            if isinstance(key, str) and redact_text(key) != key:
                return f"{path}.{REDACTED}"
            item_path = f"{path}.{key}"
            if is_credential_key(key) and item is not None and item != "":
                return item_path
            found = find_credential_path(item, item_path)
            if found is not None:
                return found
        return None
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            found = find_credential_path(item, f"{path}[{index}]")
            if found is not None:
                return found
        return None
    if isinstance(value, str) and redact_text(value) != value:
        return path
    return None


def ensure_no_credential_material(value: Any, surface: str) -> None:
    path = find_credential_path(value)
    if path is not None:
        raise CredentialMaterialError(
            f"credential material is forbidden in {surface} at {path}"
        )


def collect_sensitive_text(value: Any) -> tuple[str, ...]:
    collected: list[str] = []

    def visit(item: Any) -> None:
        if isinstance(item, Mapping):
            for nested in item.values():
                visit(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                visit(nested)
        elif isinstance(item, str) and item:
            collected.append(item)
        elif isinstance(item, (date, datetime, Decimal, int, float)) and not isinstance(
            item, bool
        ):
            rendered = str(item)
            if len(rendered) >= 4:
                collected.append(rendered)

    visit(value)
    return tuple(dict.fromkeys(collected))
