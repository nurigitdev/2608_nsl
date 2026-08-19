from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from .core import DECIMAL, INT, TypeRef, sum_money


EMPTY_MONEY_SUM_POLICY = "typed-zero"


class BuiltinError(ValueError):
    """A safe, deterministic built-in contract error."""


class UnknownBuiltinError(BuiltinError):
    pass


class BuiltinSignatureError(BuiltinError):
    pass


class BuiltinEvaluationError(BuiltinError):
    pass


@dataclass(frozen=True, slots=True)
class BuiltinSignature:
    name: str
    argument_types: tuple[TypeRef, ...]
    result_type: TypeRef


class BuiltinRegistry:
    """Closed registry for built-ins defined by the NSL v0.1 language profile."""

    __slots__ = ()

    @property
    def names(self) -> frozenset[str]:
        return frozenset({"count", "max", "min", "sum"})

    def resolve(
        self, name: str, argument_types: tuple[TypeRef, ...]
    ) -> BuiltinSignature:
        if name not in self.names:
            raise UnknownBuiltinError(f"unsupported built-in call: {name}")
        if len(argument_types) != 1:
            raise BuiltinSignatureError(f"{name} requires exactly one argument")
        argument_type = argument_types[0]
        if argument_type.kind != "list" or argument_type.item is None:
            raise BuiltinSignatureError(f"{name} requires a List argument")
        item_type = argument_type.item
        if (
            name == "sum"
            and item_type not in {INT, DECIMAL}
            and item_type.kind != "money"
        ):
            raise BuiltinSignatureError("sum requires List<Int|Decimal|Money>")
        result_type = INT if name == "count" else item_type
        return BuiltinSignature(name, argument_types, result_type)

    def evaluate(
        self,
        name: str,
        arguments: tuple[Any, ...],
        argument_types: tuple[TypeRef, ...],
        expected_type: TypeRef,
    ) -> Any:
        signature = self.resolve(name, argument_types)
        if len(arguments) != len(argument_types):
            raise BuiltinSignatureError(
                f"runtime argument count mismatch for built-in: {name}"
            )
        if signature.result_type != expected_type:
            raise BuiltinSignatureError(f"invalid result type for built-in: {name}")
        values = arguments[0]
        if name in {"min", "max"} and not values:
            raise BuiltinEvaluationError(
                f"{name} requires a non-empty collection"
            )
        if name == "sum" and expected_type.kind == "money":
            return sum_money(values, expected_type.currency)
        try:
            if name == "count":
                return len(values)
            if name == "min":
                return min(values)
            if name == "max":
                return max(values)
            if expected_type == DECIMAL:
                return sum(values, Decimal("0"))
            return sum(values)
        except TypeError as error:
            raise BuiltinEvaluationError(
                f"{name} cannot evaluate the supplied collection"
            ) from error


V0_1_BUILTINS = BuiltinRegistry()
