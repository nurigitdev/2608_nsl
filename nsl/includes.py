from __future__ import annotations

from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import PurePosixPath, PureWindowsPath
from typing import Protocol

from .diagnostics import (
    CompileError,
    DiagnosticCode,
    DiagnosticPhase,
    SourceLocation,
    compile_error,
)
from .source import SourceFile, SourceId, SourceSpan
from .syntax import (
    AstIncludeDeclaration,
    AstIncludeFragment,
    AstSkill,
    Lexer,
    ParseMode,
    Parser,
)


class IncludeResolver(Protocol):
    def resolve(
        self, including_source: SourceFile, include_path: str
    ) -> SourceFile: ...


@dataclass(frozen=True, slots=True)
class IncludeOptions:
    include_root: str = "."
    max_include_depth: int = 16
    max_include_files: int = 100
    max_total_source_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        if self.max_include_depth < 0:
            raise ValueError("max_include_depth must be non-negative")
        if self.max_include_files < 0:
            raise ValueError("max_include_files must be non-negative")
        if self.max_total_source_bytes < 0:
            raise ValueError("max_total_source_bytes must be non-negative")


def canonical_include_path(
    including_logical_path: str,
    include_path: str,
    include_root: str = ".",
) -> str:
    windows_path = PureWindowsPath(include_path)
    if (
        not include_path
        or "\0" in include_path
        or "\\" in include_path
        or PurePosixPath(include_path).is_absolute()
        or windows_path.is_absolute()
        or bool(windows_path.drive)
        or not include_path.endswith(".ns")
    ):
        raise ValueError("unsafe include path")

    def normalized_parts(path: str) -> list[str]:
        parts: list[str] = []
        for part in PurePosixPath(path).parts:
            if part in {"", "."}:
                continue
            if part == "..":
                if not parts:
                    raise ValueError("path escapes include root")
                parts.pop()
            else:
                parts.append(part)
        return parts

    root_parts = normalized_parts(include_root)
    base_parts = normalized_parts(
        PurePosixPath(including_logical_path).parent.as_posix()
    )
    if base_parts[: len(root_parts)] != root_parts:
        raise ValueError("including source is outside include root")

    target_parts = list(base_parts)
    for part in PurePosixPath(include_path).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if len(target_parts) <= len(root_parts):
                raise ValueError("path escapes include root")
            target_parts.pop()
        else:
            target_parts.append(part)
    return "/".join(target_parts)


@dataclass(frozen=True, slots=True)
class ParsedSourceUnit:
    source: SourceFile
    ast: AstSkill | AstIncludeFragment


@dataclass(frozen=True, slots=True)
class IncludeEdge:
    from_source: SourceId
    to_source: SourceId
    include_span: SourceSpan


@dataclass(frozen=True, slots=True)
class SourceManifestEntry:
    source_id: SourceId
    logical_path: str
    content_hash: str
    size_bytes: int
    is_root: bool


def manifest_entry(source: SourceFile, *, is_root: bool) -> SourceManifestEntry:
    content = source.text.encode("utf-8")
    return SourceManifestEntry(
        source.source_id,
        source.logical_path,
        "sha256:" + sha256(content).hexdigest(),
        len(content),
        is_root,
    )


def include_error(
    code: DiagnosticCode,
    message: str,
    source: SourceFile,
    declaration: AstIncludeDeclaration | None = None,
) -> CompileError:
    location = None
    snippet = None
    if declaration is not None and declaration.span is not None:
        start = declaration.span.start
        location = SourceLocation(start.line, start.column)
        lines = source.text.splitlines()
        if start.line <= len(lines):
            snippet = lines[start.line - 1]
    return compile_error(
        code,
        DiagnosticPhase.INCLUDE,
        message,
        location,
        snippet,
        source.logical_path,
    )


@dataclass(frozen=True, slots=True)
class SourceBundle:
    root: ParsedSourceUnit
    fragments: tuple[ParsedSourceUnit, ...]
    edges: tuple[IncludeEdge, ...]
    manifest: tuple[SourceManifestEntry, ...]

    @property
    def sources(self) -> tuple[SourceFile, ...]:
        return (self.root.source,) + tuple(item.source for item in self.fragments)

    @property
    def bundle_hash(self) -> str:
        digest = sha256()
        for source in self.sources:
            path = source.logical_path.encode("utf-8")
            content = source.text.encode("utf-8")
            digest.update(len(path).to_bytes(8, "big"))
            digest.update(path)
            digest.update(len(content).to_bytes(8, "big"))
            digest.update(content)
        return "sha256:" + digest.hexdigest()


class MemoryIncludeResolver:
    def __init__(self, sources: tuple[SourceFile, ...]) -> None:
        self._sources = {source.logical_path: source for source in sources}
        if len(self._sources) != len(sources):
            raise ValueError("include source logical paths must be unique")

    def resolve(self, including_source: SourceFile, include_path: str) -> SourceFile:
        target = canonical_include_path(including_source.logical_path, include_path)
        try:
            return self._sources[target]
        except KeyError as error:
            raise FileNotFoundError(target) from error


class SourceBundleBuilder:
    def __init__(
        self,
        resolver: IncludeResolver,
        options: IncludeOptions = IncludeOptions(),
    ) -> None:
        self.resolver = resolver
        self.options = options

    def build(self, root_source: SourceFile) -> SourceBundle:
        total_source_bytes = len(root_source.text.encode("utf-8"))
        if total_source_bytes > self.options.max_total_source_bytes:
            raise include_error(
                DiagnosticCode.INC_BUNDLE_SIZE_LIMIT,
                "maximum source bundle size exceeded",
                root_source,
            )
        root_ast = Parser(
            Lexer().tokenize(root_source), root_source
        ).parse(ParseMode.ROOT_SKILL)
        if not isinstance(root_ast, AstSkill):
            raise TypeError("root parse mode must produce AstSkill")

        fragments: list[ParsedSourceUnit] = []
        edges: list[IncludeEdge] = []
        visited: set[SourceId] = {root_source.source_id}
        active: set[SourceId] = {root_source.source_id}

        def visit(
            including_source: SourceFile,
            declarations: tuple[AstIncludeDeclaration, ...],
            depth: int,
        ) -> None:
            nonlocal total_source_bytes
            for declaration in declarations:
                child_depth = depth + 1
                if child_depth > self.options.max_include_depth:
                    raise include_error(
                        DiagnosticCode.INC_DEPTH_LIMIT,
                        "maximum include depth exceeded",
                        including_source,
                        declaration,
                    )
                try:
                    expected_path = canonical_include_path(
                        including_source.logical_path,
                        declaration.path,
                        self.options.include_root,
                    )
                except ValueError as error:
                    raise include_error(
                        DiagnosticCode.INC_PATH_OUTSIDE_ROOT,
                        f"unsafe include path: {declaration.path}",
                        including_source,
                        declaration,
                    ) from error
                try:
                    source = self.resolver.resolve(
                        including_source, declaration.path
                    )
                except (FileNotFoundError, KeyError) as error:
                    raise include_error(
                        DiagnosticCode.INC_RESOLUTION_FAILED,
                        f"include source could not be resolved: {declaration.path}",
                        including_source,
                        declaration,
                    ) from error
                if source.logical_path != expected_path:
                    raise include_error(
                        DiagnosticCode.INC_PATH_OUTSIDE_ROOT,
                        "include resolver returned a source outside the expected path",
                        including_source,
                        declaration,
                    )
                if declaration.span is None:
                    raise ValueError("include declarations must have source spans")
                edges.append(
                    IncludeEdge(
                        including_source.source_id,
                        source.source_id,
                        declaration.span,
                    )
                )
                if source.source_id in active:
                    raise include_error(
                        DiagnosticCode.INC_CYCLE,
                        f"circular include dependency detected: {declaration.path}",
                        including_source,
                        declaration,
                    )
                if source.source_id in visited:
                    continue
                if len(fragments) >= self.options.max_include_files:
                    raise include_error(
                        DiagnosticCode.INC_FILE_LIMIT,
                        "maximum include file count exceeded",
                        including_source,
                        declaration,
                    )
                source_bytes = len(source.text.encode("utf-8"))
                if (
                    total_source_bytes + source_bytes
                    > self.options.max_total_source_bytes
                ):
                    raise include_error(
                        DiagnosticCode.INC_BUNDLE_SIZE_LIMIT,
                        "maximum source bundle size exceeded",
                        including_source,
                        declaration,
                    )
                total_source_bytes += source_bytes
                visited.add(source.source_id)
                active.add(source.source_id)
                fragment = Parser(
                    Lexer().tokenize(source), source
                ).parse(ParseMode.INCLUDE_FRAGMENT)
                if not isinstance(fragment, AstIncludeFragment):
                    raise TypeError(
                        "include fragment mode must produce AstIncludeFragment"
                    )
                unit = ParsedSourceUnit(source, fragment)
                fragments.append(unit)
                visit(source, fragment.includes, child_depth)
                active.remove(source.source_id)

        visit(root_source, root_ast.includes, 0)
        manifest = (manifest_entry(root_source, is_root=True),) + tuple(
            manifest_entry(item.source, is_root=False) for item in fragments
        )
        return SourceBundle(
            ParsedSourceUnit(root_source, root_ast),
            tuple(fragments),
            tuple(edges),
            manifest,
        )


class SourceComposer:
    def compose(self, bundle: SourceBundle) -> AstSkill:
        root = bundle.root.ast
        if not isinstance(root, AstSkill):
            raise TypeError("source bundle root must be AstSkill")

        requires_by_id = dict(root.requires)
        contexts_by_name = {item.name: item for item in root.contexts}
        limits = root.limits
        sources_by_id = {source.source_id: source for source in bundle.sources}

        def composition_error(
            code: DiagnosticCode,
            message: str,
            node: AstIncludeFragment | object,
        ) -> CompileError:
            span = getattr(node, "span", None)
            if span is None:
                return compile_error(code, DiagnosticPhase.INCLUDE, message)
            source = sources_by_id[span.source_id]
            start = span.start
            lines = source.text.splitlines()
            snippet = lines[start.line - 1] if start.line <= len(lines) else None
            return compile_error(
                code,
                DiagnosticPhase.INCLUDE,
                message,
                SourceLocation(start.line, start.column),
                snippet,
                source.logical_path,
            )
        for unit in bundle.fragments:
            fragment = unit.ast
            if not isinstance(fragment, AstIncludeFragment):
                raise TypeError("source bundle fragments must be AstIncludeFragment")
            for tool_id, version in fragment.requires:
                existing = requires_by_id.get(tool_id)
                if existing is not None and existing != version:
                    raise composition_error(
                        DiagnosticCode.INC_TOOL_VERSION_CONFLICT,
                        f"conflicting include tool versions: {tool_id}",
                        fragment,
                    )
                requires_by_id[tool_id] = version
            for context in fragment.contexts:
                if context.name in contexts_by_name:
                    raise composition_error(
                        DiagnosticCode.INC_DUPLICATE_CONTEXT,
                        f"duplicate include context is forbidden: {context.name}",
                        context,
                    )
                contexts_by_name[context.name] = context
            if fragment.limits:
                if limits is not None or len(fragment.limits) > 1:
                    raise composition_error(
                        DiagnosticCode.INC_DUPLICATE_LIMIT,
                        "duplicate include limit fields are forbidden",
                        fragment,
                    )
                limits = fragment.limits[0]

        if limits is None:
            raise composition_error(
                DiagnosticCode.INC_REQUIRED_LIMITS_MISSING,
                "composed skill must define exactly one limits block",
                root,
            )

        return replace(
            root,
            includes=(),
            requires=tuple(requires_by_id.items()),
            contexts=tuple(contexts_by_name.values()),
            limits=limits,
        )
