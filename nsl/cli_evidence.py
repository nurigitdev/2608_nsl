from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from .audit import AuditEvent
from .data_protection import ensure_no_credential_material


def write_result_document(path: Path, payload: Mapping[str, Any]) -> None:
    if path.suffix.lower() != ".json":
        raise ValueError("result output path must end with .json")
    ensure_no_credential_material(payload, "CLI result output")
    _atomic_write(path, _json_line(payload))


def write_audit_jsonl(path: Path, events: Sequence[AuditEvent]) -> None:
    if path.suffix.lower() != ".jsonl":
        raise ValueError("audit output path must end with .jsonl")
    documents = [event.to_data() for event in events]
    ensure_no_credential_material(documents, "CLI audit output")
    _atomic_write(path, b"".join(_json_line(item) for item in documents))


def _json_line(payload: Mapping[str, Any]) -> bytes:
    return (
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def _atomic_write(path: Path, data: bytes) -> None:
    temporary_path = path.with_name(f".{path.name}.nsl-tmp")
    try:
        with temporary_path.open("xb") as stream:
            stream.write(data)
            stream.flush()
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)
