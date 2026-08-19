from __future__ import annotations

from hashlib import sha256
import json
from typing import Iterable, Protocol


class SourceManifestItem(Protocol):
    logical_path: str
    content_hash: str
    size_bytes: int
    is_root: bool


def source_manifest_sha256(entries: Iterable[SourceManifestItem]) -> str:
    manifest = [
        {
            "logical_path": item.logical_path,
            "sha256": item.content_hash,
            "size_bytes": item.size_bytes,
            "is_root": item.is_root,
        }
        for item in entries
    ]
    canonical = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "sha256:" + sha256(canonical).hexdigest()
