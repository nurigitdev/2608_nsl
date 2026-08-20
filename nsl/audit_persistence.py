from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .audit import AuditEvent
from .ir import canonical_json


class AuditPersistenceError(RuntimeError):
    pass


class AuditIntegrityError(AuditPersistenceError):
    pass


AUDIT_STORAGE_SCHEMA_VERSION = "1.0"


@dataclass(frozen=True, slots=True)
class _StoredAuditRecord:
    event: AuditEvent
    stored_at: datetime
    expires_at: datetime


class JsonlAuditStore:
    """Single-writer append-only audit adapter with restart-time verification."""

    def __init__(
        self,
        path: str | Path,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tails: dict[tuple[str | None, str], AuditEvent] = {}
        self._now_provider = now_provider or (lambda: datetime.now(UTC))
        self._load_and_verify()

    def append(self, event: AuditEvent) -> None:
        if not isinstance(event, AuditEvent):
            raise TypeError("audit store accepts AuditEvent values only")
        event.verify()
        key = (event.tenant_id, event.execution_id)
        previous = self._tails.get(key)
        expected_sequence = 1 if previous is None else previous.sequence + 1
        expected_previous_hash = None if previous is None else previous.event_hash
        if event.sequence != expected_sequence:
            raise AuditIntegrityError(
                f"audit sequence discontinuity for {event.execution_id}"
            )
        if event.previous_event_hash != expected_previous_hash:
            raise AuditIntegrityError(
                f"audit hash chain discontinuity for {event.execution_id}"
            )
        stored_at = self._now()
        record = _StoredAuditRecord(
            event,
            stored_at,
            stored_at + timedelta(days=event.retention_days),
        )
        try:
            with self.path.open("ab") as stream:
                stream.write(canonical_json(self._record_to_data(record)) + b"\n")
                stream.flush()
        except OSError as error:
            raise AuditPersistenceError("failed to append audit event") from error
        self._tails[key] = event

    def read_execution(
        self, *, tenant_id: str, execution_id: str
    ) -> tuple[AuditEvent, ...]:
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("tenant_id must be non-empty")
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("execution_id must be non-empty")
        return tuple(
            event
            for event in self._read_events()
            if event.tenant_id == tenant_id and event.execution_id == execution_id
        )

    def delete_execution(self, *, tenant_id: str, execution_id: str) -> int:
        if not isinstance(tenant_id, str) or not tenant_id:
            raise ValueError("tenant_id must be non-empty")
        if not isinstance(execution_id, str) or not execution_id:
            raise ValueError("execution_id must be non-empty")
        records = self._read_records()
        retained = tuple(
            record
            for record in records
            if not (
                record.event.tenant_id == tenant_id
                and record.event.execution_id == execution_id
            )
        )
        deleted_count = len(records) - len(retained)
        if deleted_count:
            self._write_records(retained)
            self._load_and_verify()
        return deleted_count

    def purge_expired(self, now: datetime | None = None) -> int:
        effective_now = self._now() if now is None else now
        if not isinstance(effective_now, datetime) or effective_now.tzinfo is None:
            raise ValueError("audit purge time must be timezone-aware")
        records = self._read_records()
        grouped: dict[tuple[str | None, str], list[_StoredAuditRecord]] = {}
        for record in records:
            grouped.setdefault(
                (record.event.tenant_id, record.event.execution_id), []
            ).append(record)
        expired_keys = {
            key
            for key, execution_records in grouped.items()
            if all(
                record.expires_at <= effective_now
                for record in execution_records
            )
        }
        retained = tuple(
            record
            for record in records
            if (record.event.tenant_id, record.event.execution_id)
            not in expired_keys
        )
        deleted_count = len(records) - len(retained)
        if deleted_count:
            self._write_records(retained)
            self._load_and_verify()
        return deleted_count

    def _load_and_verify(self) -> None:
        self._tails.clear()
        for event in self._read_events():
            key = (event.tenant_id, event.execution_id)
            previous = self._tails.get(key)
            expected_sequence = 1 if previous is None else previous.sequence + 1
            expected_previous_hash = None if previous is None else previous.event_hash
            if event.sequence != expected_sequence:
                raise AuditIntegrityError(
                    f"audit sequence discontinuity for {event.execution_id}"
                )
            if event.previous_event_hash != expected_previous_hash:
                raise AuditIntegrityError(
                    f"audit hash chain discontinuity for {event.execution_id}"
                )
            self._tails[key] = event

    def _read_events(self) -> tuple[AuditEvent, ...]:
        return tuple(record.event for record in self._read_records())

    def _read_records(self) -> tuple[_StoredAuditRecord, ...]:
        if not self.path.exists():
            return ()
        try:
            lines = self.path.read_bytes().splitlines()
        except OSError as error:
            raise AuditPersistenceError("failed to read audit events") from error
        records: list[_StoredAuditRecord] = []
        for line_number, line in enumerate(lines, start=1):
            if not line:
                raise AuditIntegrityError(
                    f"empty audit record at line {line_number}"
                )
            try:
                data: Any = json.loads(line)
                record = self._record_from_data(data)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise AuditIntegrityError(
                    f"invalid audit record at line {line_number}"
                ) from error
            records.append(record)
        return tuple(records)

    def _write_records(self, records: tuple[_StoredAuditRecord, ...]) -> None:
        content = b"".join(
            canonical_json(self._record_to_data(record)) + b"\n"
            for record in records
        )
        try:
            self.path.write_bytes(content)
        except OSError as error:
            raise AuditPersistenceError("failed to rewrite audit events") from error

    def _record_to_data(self, record: _StoredAuditRecord) -> dict[str, Any]:
        return {
            "storage_schema_version": AUDIT_STORAGE_SCHEMA_VERSION,
            "stored_at": record.stored_at.isoformat(),
            "expires_at": record.expires_at.isoformat(),
            "event": record.event.to_data(),
        }

    def _record_from_data(self, data: Any) -> _StoredAuditRecord:
        if not isinstance(data, dict) or set(data) != {
            "storage_schema_version",
            "stored_at",
            "expires_at",
            "event",
        }:
            raise ValueError("invalid stored audit record schema")
        if data["storage_schema_version"] != AUDIT_STORAGE_SCHEMA_VERSION:
            raise ValueError("unsupported stored audit record schema version")
        event = AuditEvent.from_data(data["event"])
        stored_at = self._parse_time(data["stored_at"], "stored_at")
        expires_at = self._parse_time(data["expires_at"], "expires_at")
        if expires_at != stored_at + timedelta(days=event.retention_days):
            raise ValueError("stored audit retention metadata mismatch")
        return _StoredAuditRecord(event, stored_at, expires_at)

    def _now(self) -> datetime:
        return self._validate_time(
            self._now_provider(), "audit storage clock must be timezone-aware"
        )

    @staticmethod
    def _parse_time(value: Any, field_name: str) -> datetime:
        if not isinstance(value, str) or not value:
            raise ValueError(f"stored audit {field_name} must be non-empty")
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as error:
            raise ValueError(
                f"stored audit {field_name} must be ISO-8601"
            ) from error
        return JsonlAuditStore._validate_time(
            parsed, f"stored audit {field_name} must include a timezone"
        )

    @staticmethod
    def _validate_time(value: Any, message: str) -> datetime:
        if not isinstance(value, datetime) or value.tzinfo is None:
            raise ValueError(message)
        return value
