from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .audit import AuditRecorder
from .core import (
    CheckStatus,
    Completeness,
    DataClassification,
    ExecutionStatus,
    ValueEnvelope,
    encode_value,
)
from .ir import SkillObject
from .security import DataHandlingPolicy, ExecutionPrincipal


class LimitExceeded(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ExecutionRequest:
    execution_id: str
    inputs: Mapping[str, Any]
    runtime_context: Mapping[str, Any]
    principal: ExecutionPrincipal
    data_policy: DataHandlingPolicy = DataHandlingPolicy()


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    status: CheckStatus
    severity: str
    message: str
    completeness: Completeness
    provenance_refs: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EmitRecord:
    values: Mapping[str, Any]
    classifications: Mapping[str, DataClassification]


@dataclass(frozen=True, slots=True)
class ResourceUsage:
    tool_calls: int
    loop_iterations: int
    emitted_rows: int
    max_collection_size_seen: int


@dataclass(frozen=True, slots=True)
class RuntimeErrorInfo:
    code: str
    category: str
    message: str
    node_id: str | None = None
    detail_code: str | None = None


@dataclass(frozen=True, slots=True)
class ExecutionResult:
    execution_id: str
    skill_id: str
    skill_version: str
    semantic_hash: str
    status: ExecutionStatus
    checks: tuple[CheckResult, ...]
    outputs: tuple[EmitRecord, ...]
    resources: ResourceUsage
    error: RuntimeErrorInfo | None = None

    def semantic_view(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "semantic_hash": self.semantic_hash,
            "status": self.status.value,
            "checks": [
                {
                    "check_id": check.check_id,
                    "status": check.status.value,
                    "severity": check.severity,
                    "message": check.message,
                    "completeness": check.completeness.value,
                }
                for check in self.checks
            ],
            "outputs": [encode_value(dict(record.values)) for record in self.outputs],
            "resources": {
                "tool_calls": self.resources.tool_calls,
                "loop_iterations": self.resources.loop_iterations,
                "emitted_rows": self.resources.emitted_rows,
                "max_collection_size_seen": self.resources.max_collection_size_seen,
            },
            "error": None
            if self.error is None
            else {
                "code": self.error.code,
                "category": self.error.category,
                "message": self.error.message,
                "node_id": self.error.node_id,
                "detail_code": self.error.detail_code,
            },
        }


@dataclass(slots=True)
class _ResourceMeter:
    tool_calls: int = 0
    loop_iterations: int = 0
    emitted_rows: int = 0
    max_collection_size_seen: int = 0


@dataclass(slots=True)
class _ExecutionContext:
    skill: SkillObject
    request: ExecutionRequest
    audit: AuditRecorder
    frames: list[dict[str, ValueEnvelope]] = field(default_factory=list)
    checks: list[CheckResult] = field(default_factory=list)
    outputs: list[EmitRecord] = field(default_factory=list)
    resources: _ResourceMeter = field(default_factory=_ResourceMeter)
    invocation_counter: int = 0

    def bind(self, symbol_id: str, value: ValueEnvelope) -> None:
        frame = self.frames[-1]
        if symbol_id in frame:
            raise RuntimeError(f"immutable symbol already bound: {symbol_id}")
        frame[symbol_id] = value

    def resolve(self, symbol_id: str) -> ValueEnvelope:
        for frame in reversed(self.frames):
            if symbol_id in frame:
                return frame[symbol_id]
        raise RuntimeError(f"unknown symbol: {symbol_id}")

    def usage(self) -> ResourceUsage:
        return ResourceUsage(
            self.resources.tool_calls,
            self.resources.loop_iterations,
            self.resources.emitted_rows,
            self.resources.max_collection_size_seen,
        )
