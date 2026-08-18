from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import NewType


SourceId = NewType("SourceId", str)


@dataclass(frozen=True, slots=True, order=True)
class SourcePosition:
    offset: int
    line: int
    column: int

    def __post_init__(self) -> None:
        if self.offset < 0:
            raise ValueError("source offset must be non-negative")
        if self.line < 1 or self.column < 1:
            raise ValueError("source line and column must be positive")


@dataclass(frozen=True, slots=True)
class SourceSpan:
    source_id: SourceId
    start: SourcePosition
    end: SourcePosition

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id is required")
        if self.end.offset < self.start.offset:
            raise ValueError("source span end must not precede start")


@dataclass(frozen=True, slots=True)
class SourceFile:
    source_id: SourceId
    logical_path: str
    text: str
    encoding: str = "utf-8"

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("source_id is required")
        if not self.logical_path.endswith(".ns"):
            raise ValueError("NSL source logical_path must end with .ns")
        if self.encoding.lower().replace("_", "-") != "utf-8":
            raise ValueError("NSL source encoding must be UTF-8")

    @classmethod
    def from_text(
        cls,
        logical_path: str,
        text: str,
        source_id: SourceId | None = None,
    ) -> SourceFile:
        resolved_id = source_id or SourceId(
            "source:"
            + sha256(f"{logical_path}\0{text}".encode("utf-8")).hexdigest()
        )
        return cls(resolved_id, logical_path, text)

    @classmethod
    def from_bytes(
        cls,
        logical_path: str,
        content: bytes,
        source_id: SourceId | None = None,
    ) -> SourceFile:
        try:
            text = content.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ValueError("NSL source bytes must be valid UTF-8") from error
        return cls.from_text(logical_path, text, source_id)


def coerce_source(source: str | SourceFile) -> SourceFile:
    if isinstance(source, SourceFile):
        return source
    return SourceFile(SourceId("memory:root"), "memory/root.ns", source)
