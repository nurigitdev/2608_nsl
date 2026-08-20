from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable

from .core import DataClassification, Money, decode_value, encode_value
from .data_protection import CredentialMaterialError, ensure_no_credential_material
from .ir import SkillObject, canonical_json
from .json_boundary import load_strict_json
from .nsp_verification import VerifiedNspPackage
from .runtime_models import ExecutionRequest, ExecutionResult
from .security import (
    AuthorizationError,
    DataHandlingPolicy,
    ExecutionPrincipal,
    PrincipalVerification,
)


class IntegrationContractError(ValueError):
    pass


MAX_INTEGRATION_VALUE_DEPTH = 32
MAX_INTEGRATION_VALUE_NODES = 10_000
MAX_INTEGRATION_STRING_BYTES = 1_048_576
MAX_INTEGRATION_DOCUMENT_BYTES = 8 * 1024 * 1024
NEX_RUNTIME_RESULT_FORMAT = "NEX-NSL-RUNTIME-RESULT"
NEX_RUNTIME_RESULT_SCHEMA_VERSION = "1.0"
NEX_SKILL_EXECUTION_JOB_FORMAT = "NEX-NSL-SKILL-EXECUTION-JOB"
NEX_SKILL_EXECUTION_JOB_SCHEMA_VERSION = "1.0"
NEX_PROGRESS_EVENT_FORMAT = "NEX-NSL-EXECUTION-PROGRESS"
NEX_PROGRESS_EVENT_SCHEMA_VERSION = "1.0"


class InputSource(StrEnum):
    USER = "USER"
    CONTEXT = "CONTEXT"
    DEFAULT = "DEFAULT"


class ProgressState(StrEnum):
    STARTED = "STARTED"
    SKILL_RESOLVED = "SKILL_RESOLVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


@dataclass(frozen=True, slots=True)
class ProgressEvent:
    execution_id: str
    sequence: int
    state: ProgressState
    skill_id: str
    skill_version: str
    runtime_status: str | None = None
    error_code: str | None = None

    def __post_init__(self) -> None:
        _safe_identifier(self.execution_id, "progress.execution_id")
        _validate_selector_part(self.skill_id, "progress.skill_id")
        _validate_selector_part(self.skill_version, "progress.skill_version")
        if (
            type(self.sequence) is not int
            or self.sequence <= 0
            or self.sequence > 1_000_000
        ):
            raise IntegrationContractError(
                "progress.sequence must be between 1 and 1000000"
            )
        if not isinstance(self.state, ProgressState):
            raise IntegrationContractError("progress.state is invalid")
        if self.runtime_status is not None:
            _safe_identifier(self.runtime_status, "progress.runtime_status")
        if self.error_code is not None:
            _safe_identifier(self.error_code, "progress.error_code")
        terminal = self.state in {ProgressState.COMPLETED, ProgressState.FAILED}
        if self.runtime_status is not None and not terminal:
            raise IntegrationContractError(
                "runtime status is only valid on terminal progress"
            )
        if self.error_code is not None and self.state is not ProgressState.FAILED:
            raise IntegrationContractError(
                "error code is only valid on failed progress"
            )

    def to_data(self) -> dict[str, Any]:
        return {
            "format": NEX_PROGRESS_EVENT_FORMAT,
            "schema_version": NEX_PROGRESS_EVENT_SCHEMA_VERSION,
            "execution_id": self.execution_id,
            "sequence": self.sequence,
            "state": self.state.value,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "runtime_status": self.runtime_status,
            "error_code": self.error_code,
        }

    @classmethod
    def from_data(cls, value: object) -> ProgressEvent:
        item = _exact_object(
            value,
            "progress",
            {
                "format",
                "schema_version",
                "execution_id",
                "sequence",
                "state",
                "skill_id",
                "skill_version",
                "runtime_status",
                "error_code",
            },
        )
        if item["format"] != NEX_PROGRESS_EVENT_FORMAT:
            raise IntegrationContractError("progress.format is invalid")
        if item["schema_version"] != NEX_PROGRESS_EVENT_SCHEMA_VERSION:
            raise IntegrationContractError("progress.schema_version is unsupported")
        try:
            state = ProgressState(item["state"])
        except (TypeError, ValueError) as error:
            raise IntegrationContractError("progress.state is invalid") from error
        return cls(
            execution_id=item["execution_id"],
            sequence=item["sequence"],
            state=state,
            skill_id=item["skill_id"],
            skill_version=item["skill_version"],
            runtime_status=item["runtime_status"],
            error_code=item["error_code"],
        )

    def to_json(self) -> str:
        return self.to_bytes().decode("utf-8")

    def to_bytes(self) -> bytes:
        return _bounded_canonical_json(self.to_data(), "progress")

    @classmethod
    def from_json(cls, data: bytes) -> ProgressEvent:
        raw = _load_integration_json(data, "progress")
        event = cls.from_data(raw)
        if data != event.to_bytes():
            raise IntegrationContractError("progress JSON must be canonical")
        return event


@runtime_checkable
class ProgressSink(Protocol):
    async def publish(self, event: ProgressEvent) -> None:
        ...


class NullProgressSink:
    async def publish(self, event: ProgressEvent) -> None:
        if not isinstance(event, ProgressEvent):
            raise IntegrationContractError("progress sink requires ProgressEvent")


class InMemoryProgressSink:
    def __init__(self) -> None:
        self.events: list[ProgressEvent] = []
        self._tails: dict[str, ProgressEvent] = {}

    async def publish(self, event: ProgressEvent) -> None:
        if not isinstance(event, ProgressEvent):
            raise IntegrationContractError("progress sink requires ProgressEvent")
        previous = self._tails.get(event.execution_id)
        if previous is not None:
            if previous.state in {ProgressState.COMPLETED, ProgressState.FAILED}:
                raise IntegrationContractError("progress cannot follow a terminal event")
            if event.sequence != previous.sequence + 1:
                raise IntegrationContractError(
                    "progress sequence must increase by exactly one"
                )
        elif event.sequence != 1:
            raise IntegrationContractError("first progress sequence must be one")
        self.events.append(event)
        self._tails[event.execution_id] = event


@dataclass(frozen=True, slots=True)
class InputProvenance:
    sources: Mapping[str, InputSource]

    def __post_init__(self) -> None:
        if not isinstance(self.sources, Mapping):
            raise IntegrationContractError("input provenance must be a mapping")
        copied: dict[str, InputSource] = {}
        for name, source in self.sources.items():
            if type(name) is not str or not name:
                raise IntegrationContractError(
                    "input provenance names must be non-empty strings"
                )
            if not isinstance(source, InputSource):
                raise IntegrationContractError(
                    "input provenance source must be USER, CONTEXT, or DEFAULT"
                )
            copied[name] = source
        try:
            ensure_no_credential_material(copied, "input_provenance")
        except CredentialMaterialError as error:
            raise IntegrationContractError(
                "credential material is forbidden in input provenance"
            ) from error
        object.__setattr__(self, "sources", MappingProxyType(copied))

    @classmethod
    def from_data(cls, value: object) -> InputProvenance:
        if type(value) is not dict:
            raise IntegrationContractError("input provenance wire value must be an object")
        try:
            sources = {name: InputSource(source) for name, source in value.items()}
        except (TypeError, ValueError) as error:
            raise IntegrationContractError("input provenance source is invalid") from error
        return cls(sources)

    def validate_inputs(self, inputs: StructuredInputs) -> None:
        if not isinstance(inputs, StructuredInputs):
            raise IntegrationContractError(
                "input provenance validation requires StructuredInputs"
            )
        input_names = set(inputs.values)
        source_names = set(self.sources)
        if input_names != source_names:
            raise IntegrationContractError(
                "input provenance must exactly cover structured inputs"
            )

    def to_data(self) -> dict[str, str]:
        return {
            name: self.sources[name].value
            for name in sorted(self.sources)
        }


@dataclass(frozen=True, slots=True)
class RuntimeResultEnvelope:
    runtime_result: ExecutionResult

    def __post_init__(self) -> None:
        if not isinstance(self.runtime_result, ExecutionResult):
            raise IntegrationContractError(
                "runtime result envelope requires ExecutionResult"
            )

    def to_data(self) -> dict[str, Any]:
        return {
            "format": NEX_RUNTIME_RESULT_FORMAT,
            "schema_version": NEX_RUNTIME_RESULT_SCHEMA_VERSION,
            "runtime_result": self.runtime_result.to_data(),
        }

    def to_json(self) -> str:
        return self.to_bytes().decode("utf-8")

    def to_bytes(self) -> bytes:
        return _bounded_canonical_json(self.to_data(), "runtime result")


@dataclass(frozen=True, slots=True)
class VerifiedPrincipalContext:
    principal: ExecutionPrincipal

    def __post_init__(self) -> None:
        if not isinstance(self.principal, ExecutionPrincipal):
            raise IntegrationContractError(
                "principal context requires ExecutionPrincipal"
            )
        try:
            self.principal.validate(require_verified=True)
        except AuthorizationError as error:
            raise IntegrationContractError(
                "NeX-AE principal must be verified and safe"
            ) from error

    @classmethod
    def from_data(cls, value: object) -> VerifiedPrincipalContext:
        item = _exact_object(
            value,
            "principal",
            {
                "tenant_id",
                "subject_id",
                "actor_type",
                "roles",
                "scopes",
                "auth_context_ref",
                "verification",
                "on_behalf_of",
            },
        )
        try:
            verification = PrincipalVerification(item["verification"])
        except (TypeError, ValueError) as error:
            raise IntegrationContractError(
                "principal.verification is invalid"
            ) from error
        on_behalf_of = item["on_behalf_of"]
        if on_behalf_of is not None:
            on_behalf_of = _required_string(on_behalf_of, "principal.on_behalf_of")
        return cls(
            ExecutionPrincipal(
                tenant_id=_required_string(item["tenant_id"], "principal.tenant_id"),
                subject_id=_required_string(
                    item["subject_id"], "principal.subject_id"
                ),
                actor_type=_required_string(
                    item["actor_type"], "principal.actor_type"
                ),
                roles=_string_set(item["roles"], "principal.roles"),
                scopes=_string_set(item["scopes"], "principal.scopes"),
                auth_context_ref=_required_string(
                    item["auth_context_ref"], "principal.auth_context_ref"
                ),
                verification=verification,
                on_behalf_of=on_behalf_of,
            )
        )

    def to_data(self) -> dict[str, Any]:
        principal = self.principal
        return {
            "tenant_id": principal.tenant_id,
            "subject_id": principal.subject_id,
            "actor_type": principal.actor_type,
            "roles": sorted(principal.roles),
            "scopes": sorted(principal.scopes),
            "auth_context_ref": principal.auth_context_ref,
            "verification": principal.verification.value,
            "on_behalf_of": principal.on_behalf_of,
        }


@dataclass(frozen=True, slots=True)
class ExplicitDataHandlingPolicy:
    policy: DataHandlingPolicy

    def __post_init__(self) -> None:
        if not isinstance(self.policy, DataHandlingPolicy) or not isinstance(
            self.policy.max_trace_classification, DataClassification
        ):
            raise IntegrationContractError(
                "data policy requires a valid DataHandlingPolicy"
            )

    @classmethod
    def from_data(cls, value: object) -> ExplicitDataHandlingPolicy:
        item = _exact_object(
            value,
            "data_policy",
            {
                "max_trace_classification",
                "snapshot_retention_days",
                "audit_retention_days",
            },
        )
        try:
            classification = DataClassification(
                item["max_trace_classification"]
            )
            policy = DataHandlingPolicy(
                max_trace_classification=classification,
                snapshot_retention_days=item["snapshot_retention_days"],
                audit_retention_days=item["audit_retention_days"],
            )
        except (TypeError, ValueError) as error:
            raise IntegrationContractError("data_policy is invalid") from error
        return cls(policy)

    def to_data(self) -> dict[str, Any]:
        return {
            "max_trace_classification": self.policy.max_trace_classification.value,
            "snapshot_retention_days": self.policy.snapshot_retention_days,
            "audit_retention_days": self.policy.audit_retention_days,
        }


@dataclass(frozen=True, slots=True)
class SkillExecutionJob:
    execution_id: str
    skill_id: str
    skill_version: str
    expected_semantic_hash: str
    inputs: StructuredInputs
    input_provenance: InputProvenance
    runtime_context: StructuredRuntimeContext
    principal: VerifiedPrincipalContext
    data_policy: ExplicitDataHandlingPolicy

    def __post_init__(self) -> None:
        _safe_identifier(self.execution_id, "execution_id")
        _validate_selector_part(self.skill_id, "skill_id")
        _validate_selector_part(self.skill_version, "skill_version")
        _semantic_hash(self.expected_semantic_hash, "expected_semantic_hash")
        contracts = (
            (self.inputs, StructuredInputs, "inputs"),
            (self.input_provenance, InputProvenance, "input_provenance"),
            (self.runtime_context, StructuredRuntimeContext, "runtime_context"),
            (self.principal, VerifiedPrincipalContext, "principal"),
            (self.data_policy, ExplicitDataHandlingPolicy, "data_policy"),
        )
        for value, expected, field in contracts:
            if not isinstance(value, expected):
                raise IntegrationContractError(
                    f"SkillExecutionJob.{field} has an invalid contract type"
                )
        self.input_provenance.validate_inputs(self.inputs)

    @classmethod
    def from_data(cls, value: object) -> SkillExecutionJob:
        item = _exact_object(
            value,
            "job",
            {
                "format",
                "schema_version",
                "execution_id",
                "skill_id",
                "skill_version",
                "expected_semantic_hash",
                "inputs",
                "input_provenance",
                "runtime_context",
                "principal",
                "data_policy",
            },
        )
        if item["format"] != NEX_SKILL_EXECUTION_JOB_FORMAT:
            raise IntegrationContractError("job.format is invalid")
        if item["schema_version"] != NEX_SKILL_EXECUTION_JOB_SCHEMA_VERSION:
            raise IntegrationContractError("job.schema_version is unsupported")
        return cls(
            execution_id=item["execution_id"],
            skill_id=item["skill_id"],
            skill_version=item["skill_version"],
            expected_semantic_hash=item["expected_semantic_hash"],
            inputs=StructuredInputs.from_data(item["inputs"]),
            input_provenance=InputProvenance.from_data(item["input_provenance"]),
            runtime_context=StructuredRuntimeContext.from_data(
                item["runtime_context"]
            ),
            principal=VerifiedPrincipalContext.from_data(item["principal"]),
            data_policy=ExplicitDataHandlingPolicy.from_data(item["data_policy"]),
        )

    def to_data(self) -> dict[str, Any]:
        return {
            "format": NEX_SKILL_EXECUTION_JOB_FORMAT,
            "schema_version": NEX_SKILL_EXECUTION_JOB_SCHEMA_VERSION,
            "execution_id": self.execution_id,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "expected_semantic_hash": self.expected_semantic_hash,
            "inputs": self.inputs.to_data(),
            "input_provenance": self.input_provenance.to_data(),
            "runtime_context": self.runtime_context.to_data(),
            "principal": self.principal.to_data(),
            "data_policy": self.data_policy.to_data(),
        }

    def to_json(self) -> str:
        return self.to_bytes().decode("utf-8")

    def to_bytes(self) -> bytes:
        return _bounded_canonical_json(self.to_data(), "job")

    @classmethod
    def from_json(cls, data: bytes) -> SkillExecutionJob:
        raw = _load_integration_json(data, "job")
        job = cls.from_data(raw)
        if data != job.to_bytes():
            raise IntegrationContractError("job JSON must be canonical")
        return job

    def to_runtime_request(self) -> ExecutionRequest:
        return ExecutionRequest(
            execution_id=self.execution_id,
            inputs=self.inputs.to_runtime(),
            runtime_context=self.runtime_context.to_runtime(),
            principal=self.principal.principal,
            data_policy=self.data_policy.policy,
        )


@dataclass(frozen=True, slots=True)
class StructuredInputs:
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        normalized = _normalize_mapping(self.values, "inputs")
        object.__setattr__(self, "values", MappingProxyType(normalized))

    @classmethod
    def from_data(cls, value: object) -> StructuredInputs:
        if type(value) is not dict:
            raise IntegrationContractError("inputs wire value must be an object")
        _validate_typed_wire(value, "inputs", 0, set())
        try:
            decoded = decode_value(value)
        except (TypeError, ValueError) as error:
            raise IntegrationContractError("inputs contain an invalid typed value") from error
        return cls(decoded)

    def to_data(self) -> dict[str, Any]:
        return encode_value(_portable_copy(self.values))

    def to_runtime(self) -> dict[str, Any]:
        return _portable_copy(self.values)


@dataclass(frozen=True, slots=True)
class StructuredRuntimeContext:
    values: Mapping[str, Any]

    def __post_init__(self) -> None:
        normalized = _normalize_mapping(self.values, "runtime_context")
        object.__setattr__(self, "values", MappingProxyType(normalized))

    @classmethod
    def from_data(cls, value: object) -> StructuredRuntimeContext:
        if type(value) is not dict:
            raise IntegrationContractError(
                "runtime_context wire value must be an object"
            )
        _validate_typed_wire(value, "runtime_context", 0, set())
        try:
            decoded = decode_value(value)
        except (TypeError, ValueError) as error:
            raise IntegrationContractError(
                "runtime_context contains an invalid typed value"
            ) from error
        return cls(decoded)

    def to_data(self) -> dict[str, Any]:
        return encode_value(_portable_copy(self.values))

    def to_runtime(self) -> dict[str, Any]:
        return _portable_copy(self.values)


class SkillResolutionCode(StrEnum):
    INVALID_SELECTOR = "INVALID_SKILL_SELECTOR"
    UNKNOWN_SKILL = "UNKNOWN_SKILL"
    DUPLICATE_IDENTITY = "DUPLICATE_SKILL_IDENTITY"


class SkillResolutionError(LookupError):
    def __init__(self, code: SkillResolutionCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ResolvedSkill:
    skill: SkillObject
    package_hash: str
    signer_key_id: str | None


@runtime_checkable
class SkillResolver(Protocol):
    def resolve(self, skill_id: str, skill_version: str) -> ResolvedSkill:
        ...


class VerifiedPackageSkillResolver:
    """Resolves exact Skill identities from packages that crossed NSP verification."""

    def __init__(self, packages: Sequence[VerifiedNspPackage]) -> None:
        if not isinstance(packages, Sequence) or isinstance(
            packages, (str, bytes, bytearray)
        ):
            raise IntegrationContractError(
                "Skill resolver packages must be a sequence"
            )
        skills: dict[tuple[str, str], ResolvedSkill] = {}
        for package in packages:
            if not isinstance(package, VerifiedNspPackage):
                raise IntegrationContractError(
                    "Skill resolver accepts verified NSP packages only"
                )
            signer_key_id = (
                package.signature.key_id if package.signature is not None else None
            )
            for verified in package.skills:
                identity = (
                    verified.skill.skill_id,
                    verified.skill.skill_version,
                )
                if identity in skills:
                    raise SkillResolutionError(
                        SkillResolutionCode.DUPLICATE_IDENTITY,
                        "duplicate verified Skill ID/version",
                    )
                skills[identity] = ResolvedSkill(
                    skill=verified.skill,
                    package_hash=package.package_hash,
                    signer_key_id=signer_key_id,
                )
        self._skills = skills

    def resolve(self, skill_id: str, skill_version: str) -> ResolvedSkill:
        try:
            _validate_selector_part(skill_id, "skill_id")
            _validate_selector_part(skill_version, "skill_version")
        except IntegrationContractError as error:
            raise SkillResolutionError(
                SkillResolutionCode.INVALID_SELECTOR,
                "invalid Skill ID/version selector",
            ) from error
        try:
            return self._skills[(skill_id, skill_version)]
        except KeyError as error:
            raise SkillResolutionError(
                SkillResolutionCode.UNKNOWN_SKILL,
                "verified Skill ID/version is not registered",
            ) from error


def _validate_selector_part(value: object, field: str) -> None:
    if (
        type(value) is not str
        or not value.strip()
        or not value.isascii()
        or len(value) > 256
    ):
        raise IntegrationContractError(
            f"{field} must be non-empty ASCII up to 256 bytes"
        )
    try:
        ensure_no_credential_material(value, f"SkillSelector.{field}")
    except CredentialMaterialError as error:
        raise IntegrationContractError(
            f"credential material is forbidden in {field}"
        ) from error


def _normalize_mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise IntegrationContractError(f"{field} must be a structured mapping")
    counter = [0]
    normalized = _normalize_value(value, field, 0, counter)
    assert isinstance(normalized, dict)
    try:
        ensure_no_credential_material(normalized, field)
    except CredentialMaterialError as error:
        raise IntegrationContractError(
            f"credential material is forbidden in {field}"
        ) from error
    return normalized


def _normalize_value(
    value: object,
    path: str,
    depth: int,
    counter: list[int],
) -> Any:
    if depth > MAX_INTEGRATION_VALUE_DEPTH:
        raise IntegrationContractError(f"{path} exceeds structured value depth")
    counter[0] += 1
    if counter[0] > MAX_INTEGRATION_VALUE_NODES:
        raise IntegrationContractError(f"{path} exceeds structured value size")
    if type(value) is Decimal:
        if not value.is_finite():
            raise IntegrationContractError(f"{path} contains non-finite Decimal")
        return value
    if type(value) is str:
        if len(value.encode("utf-8")) > MAX_INTEGRATION_STRING_BYTES:
            raise IntegrationContractError(f"{path} string exceeds 1 MiB")
        return value
    if value is None or type(value) in {bool, int, date, datetime, Money}:
        return value
    if isinstance(value, Mapping):
        copied: dict[str, Any] = {}
        for key, item in value.items():
            if type(key) is not str or not key:
                raise IntegrationContractError(
                    f"{path} field names must be non-empty strings"
                )
            copied[key] = _normalize_value(
                item,
                f"{path}.{key}",
                depth + 1,
                counter,
            )
        return copied
    if isinstance(value, (list, tuple)):
        return [
            _normalize_value(item, f"{path}[{index}]", depth + 1, counter)
            for index, item in enumerate(value)
        ]
    raise IntegrationContractError(
        f"{path} contains unsupported structured value type"
    )


def _portable_copy(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _portable_copy(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_portable_copy(item) for item in value]
    return value


def _exact_object(
    value: object,
    path: str,
    fields: set[str],
) -> dict[str, Any]:
    if type(value) is not dict:
        raise IntegrationContractError(f"{path} must be an object")
    actual = set(value)
    missing = fields - actual
    unexpected = actual - fields
    if missing:
        raise IntegrationContractError(f"{path} missing fields: {sorted(missing)}")
    if unexpected:
        raise IntegrationContractError(
            f"{path} unexpected fields: {sorted(unexpected)}"
        )
    return value


def _required_string(value: object, path: str) -> str:
    if type(value) is not str or not value.strip():
        raise IntegrationContractError(f"{path} must be a non-empty string")
    return value


def _string_set(value: object, path: str) -> frozenset[str]:
    if type(value) is not list or not all(
        type(item) is str and bool(item.strip()) for item in value
    ):
        raise IntegrationContractError(f"{path} must be an array of strings")
    if len(value) != len(set(value)):
        raise IntegrationContractError(f"{path} must not contain duplicates")
    return frozenset(value)


def _safe_identifier(value: object, path: str) -> str:
    text = _required_string(value, path)
    if not text.isascii() or len(text) > 256:
        raise IntegrationContractError(f"{path} must be safe ASCII up to 256 bytes")
    try:
        ensure_no_credential_material(text, path)
    except CredentialMaterialError as error:
        raise IntegrationContractError(
            f"credential material is forbidden in {path}"
        ) from error
    return text


def _semantic_hash(value: object, path: str) -> str:
    digest = _required_string(value, path)
    if (
        len(digest) != 71
        or not digest.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in digest[7:])
    ):
        raise IntegrationContractError(f"{path} must be a lowercase sha256 digest")
    return digest


def _load_integration_json(data: bytes, document: str) -> Any:
    if type(data) is bytes and len(data) > MAX_INTEGRATION_DOCUMENT_BYTES:
        raise IntegrationContractError(f"{document} JSON exceeds 8 MiB")
    return load_strict_json(
        data,
        document_name=f"NeX integration {document}",
        error_factory=lambda path, reason: IntegrationContractError(
            f"invalid {document} JSON at {path}: {reason}"
        ),
    )


def _bounded_canonical_json(value: Any, document: str) -> bytes:
    data = canonical_json(value)
    if len(data) > MAX_INTEGRATION_DOCUMENT_BYTES:
        raise IntegrationContractError(f"{document} JSON exceeds 8 MiB")
    return data


def _validate_typed_wire(
    value: Any,
    path: str,
    depth: int,
    seen: set[int],
) -> None:
    if depth > MAX_INTEGRATION_VALUE_DEPTH:
        raise IntegrationContractError(f"{path} exceeds typed wire depth")
    if isinstance(value, (dict, list)):
        identity = id(value)
        if identity in seen:
            raise IntegrationContractError(f"{path} contains a cyclic wire value")
        seen.add(identity)
    try:
        if isinstance(value, dict):
            tag = value.get("$type")
            if tag is not None:
                fields = {
                    "Money": {"$type", "amount", "currency"},
                    "Decimal": {"$type", "value"},
                    "Date": {"$type", "value"},
                    "DateTime": {"$type", "value"},
                }
                if type(tag) is not str or tag not in fields:
                    raise IntegrationContractError(f"{path} has an unknown typed tag")
                if set(value) != fields[tag]:
                    raise IntegrationContractError(
                        f"{path} typed value fields are invalid"
                    )
                return
            for key, item in value.items():
                _validate_typed_wire(item, f"{path}.{key}", depth + 1, seen)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                _validate_typed_wire(item, f"{path}[{index}]", depth + 1, seen)
    finally:
        if isinstance(value, (dict, list)):
            seen.remove(id(value))
