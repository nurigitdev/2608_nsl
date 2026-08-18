from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any


class Presence(StrEnum):
    PRESENT = "PRESENT"
    EMPTY = "EMPTY"


class Completeness(StrEnum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"


class CheckStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class ExecutionStatus(StrEnum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TOOL_ERROR = "TOOL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    CANCELLED = "CANCELLED"


class DataClassification(StrEnum):
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"
    RESTRICTED = "RESTRICTED"


_CLASSIFICATION_RANK = {
    DataClassification.PUBLIC: 0,
    DataClassification.INTERNAL: 1,
    DataClassification.CONFIDENTIAL: 2,
    DataClassification.RESTRICTED: 3,
}


def highest_classification(
    *values: DataClassification,
) -> DataClassification:
    return max(values, key=_CLASSIFICATION_RANK.__getitem__)


def classification_allows(
    actual: DataClassification,
    maximum: DataClassification,
) -> bool:
    return _CLASSIFICATION_RANK[actual] <= _CLASSIFICATION_RANK[maximum]


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not self.currency:
            raise ValueError("currency is required")

    def _require_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(
                f"currency mismatch: {self.currency} != {other.currency}"
            )

    def __add__(self, other: Money) -> Money:
        self._require_currency(other)
        return Money(self.amount + other.amount, self.currency)

    def __sub__(self, other: Money) -> Money:
        self._require_currency(other)
        return Money(self.amount - other.amount, self.currency)

    def __le__(self, other: Money) -> bool:
        self._require_currency(other)
        return self.amount <= other.amount

    def __lt__(self, other: Money) -> bool:
        self._require_currency(other)
        return self.amount < other.amount


@dataclass(frozen=True, slots=True)
class TypeRef:
    kind: str
    name: str | None = None
    currency: str | None = None
    item: TypeRef | None = None
    fields: tuple[tuple[str, TypeRef], ...] = ()
    enum_values: tuple[str, ...] = ()

    def field(self, name: str) -> TypeRef:
        for field_name, field_type in self.fields:
            if field_name == name:
                return field_type
        raise KeyError(name)

    def to_data(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind}
        if self.name is not None:
            result["name"] = self.name
        if self.currency is not None:
            result["currency"] = self.currency
        if self.item is not None:
            result["item"] = self.item.to_data()
        if self.fields:
            result["fields"] = [
                {"name": name, "type": field_type.to_data()}
                for name, field_type in self.fields
            ]
        if self.enum_values:
            result["values"] = list(self.enum_values)
        return result

    @classmethod
    def from_data(cls, data: dict[str, Any]) -> TypeRef:
        return cls(
            kind=data["kind"],
            name=data.get("name"),
            currency=data.get("currency"),
            item=cls.from_data(data["item"]) if "item" in data else None,
            fields=tuple(
                (field["name"], cls.from_data(field["type"]))
                for field in data.get("fields", [])
            ),
            enum_values=tuple(data.get("values", [])),
        )


def primitive(name: str) -> TypeRef:
    return TypeRef(kind="primitive", name=name)


def domain(name: str) -> TypeRef:
    return TypeRef(kind="domain", name=name)


def money_type(currency: str) -> TypeRef:
    return TypeRef(kind="money", currency=currency)


def list_type(item: TypeRef) -> TypeRef:
    return TypeRef(kind="list", item=item)


def record_type(name: str, **fields: TypeRef) -> TypeRef:
    return TypeRef(kind="record", name=name, fields=tuple(fields.items()))


def enum_type(name: str, *values: str) -> TypeRef:
    return TypeRef(kind="enum", name=name, enum_values=tuple(values))


BOOL = primitive("Bool")
INT = primitive("Int")
STRING = primitive("String")
YEAR = primitive("Year")
CHECK_STATUS = enum_type("CheckStatus", "PASS", "FAIL", "UNKNOWN")
CHECK_RESULT = record_type("CheckResult", status=CHECK_STATUS)


@dataclass(frozen=True, slots=True)
class ValueEnvelope:
    value: Any
    type_info: TypeRef
    presence: Presence
    completeness: Completeness
    classification: DataClassification
    provenance_refs: tuple[str, ...] = ()

    @classmethod
    def complete(
        cls,
        value: Any,
        type_info: TypeRef,
        classification: DataClassification = DataClassification.INTERNAL,
        provenance_refs: tuple[str, ...] = (),
    ) -> ValueEnvelope:
        is_empty = value is None or value == [] or value == {}
        return cls(
            value=value,
            type_info=type_info,
            presence=Presence.EMPTY if is_empty else Presence.PRESENT,
            completeness=Completeness.COMPLETE,
            classification=classification,
            provenance_refs=provenance_refs,
        )


def encode_value(value: Any) -> Any:
    if isinstance(value, Money):
        return {
            "$type": "Money",
            "amount": format(value.amount, "f"),
            "currency": value.currency,
        }
    if isinstance(value, Decimal):
        return {"$type": "Decimal", "value": format(value, "f")}
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, dict):
        return {key: encode_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [encode_value(item) for item in value]
    return value


def decode_value(value: Any) -> Any:
    if isinstance(value, dict) and value.get("$type") == "Money":
        return Money(Decimal(value["amount"]), value["currency"])
    if isinstance(value, dict) and value.get("$type") == "Decimal":
        return Decimal(value["value"])
    if isinstance(value, dict):
        return {key: decode_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [decode_value(item) for item in value]
    return value

