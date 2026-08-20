from __future__ import annotations

import json
from typing import Any, Callable


JsonErrorFactory = Callable[[str, str], ValueError]


def load_strict_json(
    data: bytes,
    *,
    document_name: str,
    error_factory: JsonErrorFactory,
) -> Any:
    if type(data) is not bytes:
        raise error_factory("$", f"expected {document_name} bytes")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as error:
        raise error_factory("$", "expected UTF-8 JSON") from error

    def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise error_factory("$", f"duplicate object field: {key}")
            value[key] = item
        return value

    def reject_non_finite(value: str) -> None:
        raise error_factory("$", f"non-finite JSON number: {value}")

    try:
        return json.loads(
            text,
            object_pairs_hook=unique_object,
            parse_constant=reject_non_finite,
        )
    except ValueError as error:
        if not isinstance(error, json.JSONDecodeError):
            raise
        raise error_factory(
            "$", f"invalid JSON at line {error.lineno}, column {error.colno}"
        ) from error
