from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType
from typing import Any, Mapping, Protocol, runtime_checkable

from .core import BusinessOperationId, Money, PayloadHash, ToolCapability, ValueEnvelope
from .data_protection import ensure_no_credential_material
from .ir import WriteExpression, canonical_json
from .runtime_models import ExecutionContext


_PAYLOAD_HASH_DOMAIN = b"NSL-WRITE-PAYLOAD-V1\0"
_WRITE_INTENT_HASH_DOMAIN = b"NSL-WRITE-INTENT-V1\0"


class WriteIntentError(ValueError):
    """Base class for controlled WRITE intent construction failures."""


class CanonicalWriteValueError(WriteIntentError):
    pass


class ApprovalRefreshRequiredError(WriteIntentError):
    detail_code = "NEW_APPROVAL_REQUIRED"

    def __init__(self) -> None:
        super().__init__(
            "WRITE intent changed after approval; a new Approval Request is required"
        )


@runtime_checkable
class ApprovalIdentityEvidence(Protocol):
    @property
    def payload_hash(self) -> PayloadHash: ...

    @property
    def write_intent_hash(self) -> str: ...


def _required_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WriteIntentError(f"{name} must be a non-empty string")
    ensure_no_credential_material(value, name)
    return value


def _sha256_text(value: object, name: str) -> str:
    text = _required_text(value, name)
    digest = text.removeprefix("sha256:")
    if (
        not text.startswith("sha256:")
        or len(digest) != 64
        or any(character not in "0123456789abcdef" for character in digest)
    ):
        raise WriteIntentError(f"{name} must be a lowercase SHA-256 value")
    return text


def canonical_write_arguments(
    arguments: Mapping[str, ValueEnvelope],
) -> bytes:
    """Return the strict typed canonical JSON used for WRITE payload identity."""

    if not isinstance(arguments, Mapping):
        raise CanonicalWriteValueError("WRITE arguments must be a mapping")
    try:
        encoded = _encode_argument_mapping(arguments, set())
    except RecursionError as error:
        raise CanonicalWriteValueError(
            "WRITE arguments exceed the supported nesting depth"
        ) from error
    return canonical_json(encoded)


def compute_payload_hash(
    arguments: Mapping[str, ValueEnvelope],
) -> PayloadHash:
    canonical = canonical_write_arguments(arguments)
    digest = sha256(_PAYLOAD_HASH_DOMAIN + canonical).hexdigest()
    return PayloadHash("sha256:" + digest)


def compute_write_intent_hash(
    *,
    skill_semantic_hash: str,
    write_node_id: str,
    tool_id: str,
    payload_hash: PayloadHash,
) -> str:
    payload = {
        "skill_semantic_hash": _sha256_text(
            skill_semantic_hash,
            "skill_semantic_hash",
        ),
        "write_node_id": _required_text(write_node_id, "write_node_id"),
        "tool_id": _required_text(tool_id, "tool_id"),
        "payload_hash": _sha256_text(payload_hash, "payload_hash"),
    }
    digest = sha256(
        _WRITE_INTENT_HASH_DOMAIN + canonical_json(payload)
    ).hexdigest()
    return "sha256:" + digest


@dataclass(frozen=True, slots=True)
class WriteIntent:
    tenant_id: str
    business_operation_id: BusinessOperationId
    node_id: str
    tool_ref: str
    tool_id: str
    approval_id: str
    skill_semantic_hash: str
    canonical_arguments: Mapping[str, ValueEnvelope]
    payload_hash: PayloadHash
    write_intent_hash: str

    def __post_init__(self) -> None:
        for name in (
            "tenant_id",
            "business_operation_id",
            "node_id",
            "tool_ref",
            "tool_id",
            "approval_id",
        ):
            _required_text(getattr(self, name), f"WriteIntent.{name}")
        _sha256_text(
            self.skill_semantic_hash,
            "WriteIntent.skill_semantic_hash",
        )
        _sha256_text(self.payload_hash, "WriteIntent.payload_hash")
        _sha256_text(
            self.write_intent_hash,
            "WriteIntent.write_intent_hash",
        )

        expected_payload_hash = compute_payload_hash(self.canonical_arguments)
        if self.payload_hash != expected_payload_hash:
            raise WriteIntentError(
                "WriteIntent.payload_hash does not match canonical arguments"
            )
        expected_intent_hash = compute_write_intent_hash(
            skill_semantic_hash=self.skill_semantic_hash,
            write_node_id=self.node_id,
            tool_id=self.tool_id,
            payload_hash=self.payload_hash,
        )
        if self.write_intent_hash != expected_intent_hash:
            raise WriteIntentError(
                "WriteIntent.write_intent_hash does not match intent semantics"
            )
        object.__setattr__(
            self,
            "canonical_arguments",
            _freeze_arguments(self.canonical_arguments),
        )

    def require_approval_identity(
        self,
        evidence: ApprovalIdentityEvidence,
    ) -> None:
        """Require only hash identity; full Approval validity remains a guard concern."""

        if not isinstance(evidence, ApprovalIdentityEvidence):
            raise TypeError(
                "evidence must provide payload_hash and write_intent_hash"
            )
        approved_payload_hash = PayloadHash(
            _sha256_text(evidence.payload_hash, "Approval payload_hash")
        )
        approved_intent_hash = _sha256_text(
            evidence.write_intent_hash,
            "Approval write_intent_hash",
        )
        if (
            approved_payload_hash != self.payload_hash
            or approved_intent_hash != self.write_intent_hash
        ):
            raise ApprovalRefreshRequiredError()


class WriteIntentBuilder:
    __slots__ = ()

    def build(
        self,
        expression: WriteExpression,
        arguments: Mapping[str, ValueEnvelope],
        ctx: ExecutionContext,
    ) -> WriteIntent:
        if not isinstance(expression, WriteExpression):
            raise TypeError("expression must be a WriteExpression")
        if not isinstance(ctx, ExecutionContext):
            raise TypeError("ctx must be an ExecutionContext")
        business_operation_id = ctx.identity.business_operation_id
        if business_operation_id is None:
            raise WriteIntentError(
                "BusinessOperationId is required to build a WriteIntent"
            )

        required_tools = tuple(
            required
            for required in ctx.skill.required_tools
            if required.tool_ref == expression.tool_ref
        )
        if len(required_tools) != 1:
            raise WriteIntentError(
                "WRITE must resolve exactly one declared tool contract"
            )
        required_tool = required_tools[0]
        if required_tool.capability is not ToolCapability.WRITE:
            raise WriteIntentError("WRITE requires a WRITE-capable tool contract")

        expected_names = tuple(name for name, _ in expression.arguments)
        actual_names = tuple(arguments)
        if set(actual_names) != set(expected_names) or len(actual_names) != len(
            expected_names
        ):
            raise WriteIntentError(
                "evaluated WRITE arguments do not match the IR expression"
            )

        payload_hash = compute_payload_hash(arguments)
        write_intent_hash = compute_write_intent_hash(
            skill_semantic_hash=ctx.skill.semantic_hash,
            write_node_id=expression.node_id,
            tool_id=required_tool.tool_id,
            payload_hash=payload_hash,
        )
        return WriteIntent(
            tenant_id=ctx.tenant_id,
            business_operation_id=business_operation_id,
            node_id=expression.node_id,
            tool_ref=expression.tool_ref,
            tool_id=required_tool.tool_id,
            approval_id=expression.approval_ref,
            skill_semantic_hash=ctx.skill.semantic_hash,
            canonical_arguments=arguments,
            payload_hash=payload_hash,
            write_intent_hash=write_intent_hash,
        )


def _encode_argument_mapping(
    arguments: Mapping[object, object],
    active_containers: set[int],
) -> dict[str, Any]:
    encoded: dict[str, Any] = {}
    container_id = _enter_container(arguments, "$", active_containers)
    try:
        for key, envelope in arguments.items():
            if not isinstance(key, str):
                raise CanonicalWriteValueError(
                    "WRITE argument object keys must be strings at $"
                )
            if not isinstance(envelope, ValueEnvelope):
                raise CanonicalWriteValueError(
                    f"WRITE argument {key!r} must be a ValueEnvelope"
                )
            encoded[key] = _encode_write_value(
                envelope.value,
                f"$.{key}",
                active_containers,
            )
    finally:
        active_containers.remove(container_id)
    return encoded


def _encode_write_value(
    value: object,
    path: str,
    active_containers: set[int],
) -> Any:
    if isinstance(value, StrEnum):
        return value.value
    if value is None or isinstance(value, (str, bool)) or type(value) is int:
        return value
    if isinstance(value, Money):
        return {
            "$type": "Money",
            "amount": format(value.amount, "f"),
            "currency": value.currency,
        }
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise CanonicalWriteValueError(
                f"WRITE Decimal must be finite at {path}"
            )
        return {"$type": "Decimal", "value": format(value, "f")}
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise CanonicalWriteValueError(
                f"WRITE DateTime must be timezone-aware at {path}"
            )
        return {"$type": "DateTime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"$type": "Date", "value": value.isoformat()}
    if isinstance(value, Mapping):
        container_id = _enter_container(value, path, active_containers)
        try:
            encoded: dict[str, Any] = {}
            for key, item in value.items():
                if not isinstance(key, str):
                    raise CanonicalWriteValueError(
                        f"WRITE object keys must be strings at {path}"
                    )
                encoded[key] = _encode_write_value(
                    item,
                    f"{path}.{key}",
                    active_containers,
                )
            return encoded
        finally:
            active_containers.remove(container_id)
    if isinstance(value, (list, tuple)):
        container_id = _enter_container(value, path, active_containers)
        try:
            return [
                _encode_write_value(item, f"{path}[{index}]", active_containers)
                for index, item in enumerate(value)
            ]
        finally:
            active_containers.remove(container_id)
    raise CanonicalWriteValueError(
        f"unsupported WRITE value type at {path}: {type(value).__name__}"
    )


def _enter_container(
    value: object,
    path: str,
    active_containers: set[int],
) -> int:
    container_id = id(value)
    if container_id in active_containers:
        raise CanonicalWriteValueError(
            f"cyclic WRITE value is forbidden at {path}"
        )
    active_containers.add(container_id)
    return container_id


def _freeze_arguments(
    arguments: Mapping[str, ValueEnvelope],
) -> Mapping[str, ValueEnvelope]:
    return MappingProxyType(
        {
            name: ValueEnvelope(
                value=_freeze_value(envelope.value),
                type_info=envelope.type_info,
                presence=envelope.presence,
                completeness=envelope.completeness,
                classification=envelope.classification,
                provenance_refs=tuple(envelope.provenance_refs),
            )
            for name, envelope in arguments.items()
        }
    )


def _freeze_value(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: _freeze_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    return value
