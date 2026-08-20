from __future__ import annotations

from pathlib import Path, PurePosixPath

from ..includes import canonical_include_path
from ..source import SourceFile


class FileSystemIncludeResolver:
    """Resolve include files beneath one fixed filesystem root."""

    def __init__(self, root: Path) -> None:
        resolved_root = root.resolve(strict=True)
        if not resolved_root.is_dir():
            raise FileNotFoundError("include root not found")
        self._root = resolved_root

    def resolve(self, including_source: SourceFile, include_path: str) -> SourceFile:
        logical_path = canonical_include_path(
            including_source.logical_path, include_path
        )
        relative_path = Path(*PurePosixPath(logical_path).parts)
        target = (self._root / relative_path).resolve(strict=True)
        try:
            target.relative_to(self._root)
        except ValueError as error:
            raise FileNotFoundError(logical_path) from error
        if not target.is_file():
            raise FileNotFoundError(logical_path)
        return SourceFile.from_bytes(logical_path, target.read_bytes())
