from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .audit import AuditEvent
from .ir import canonical_json


class AuditPersistenceError(RuntimeError):
    pass


class AuditIntegrityError(AuditPersistenceError):
    pass


class JsonlAuditStore:
    """Single-writer append-only audit adapter with restart-time verification."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tails: dict[tuple[str | None, str], AuditEvent] = {}
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
        try:
            with self.path.open("ab") as stream:
                stream.write(canonical_json(event.to_data()) + b"\n")
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
        if not self.path.exists():
            return ()
        try:
            lines = self.path.read_bytes().splitlines()
        except OSError as error:
            raise AuditPersistenceError("failed to read audit events") from error
        events: list[AuditEvent] = []
        for line_number, line in enumerate(lines, start=1):
            if not line:
                raise AuditIntegrityError(
                    f"empty audit record at line {line_number}"
                )
            try:
                data: Any = json.loads(line)
                event = AuditEvent.from_data(data)
            except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as error:
                raise AuditIntegrityError(
                    f"invalid audit record at line {line_number}"
                ) from error
            events.append(event)
        return tuple(events)
