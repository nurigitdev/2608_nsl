from __future__ import annotations

from pathlib import Path

import pytest

from nsl import (
    CompileError,
    DiagnosticCode,
    DiagnosticPhase,
    NslCompiler,
    SourceFile,
    SourceLocation,
)
from nsl.includes import (
    IncludeOptions,
    MemoryIncludeResolver,
    SourceBundleBuilder,
    SourceComposer,
)
from nsl.vertical_slice import build_tool_catalog


ROOT = Path(__file__).resolve().parents[1]
SOURCE_TEXT = (ROOT / "examples" / "project_budget_check.ns").read_text(
    encoding="utf-8"
)


def _root_with_includes(*paths: str) -> SourceFile:
    declarations = "\n".join(f'    include "{path}";' for path in paths)
    text = SOURCE_TEXT.replace("    requires {", declarations + "\n\n    requires {")
    return SourceFile.from_text("skills/root.ns", text)


def _without_root_limits(source: SourceFile) -> SourceFile:
    start = source.text.index("    limits {")
    end = source.text.index("    input {")
    return SourceFile.from_text(
        source.logical_path, source.text[:start] + source.text[end:]
    )


def test_inc_001_compiler_frontend_resolves_include_sources() -> None:
    root = _root_with_includes("common/finance.ns")
    nested = SourceFile.from_text(
        "skills/common/base.ns", 'context { region: String from "user.region"; }'
    )
    finance = SourceFile.from_text(
        "skills/common/finance.ns",
        'include "base.ns";\ncontext { currency: String from "tenant.currency"; }',
    )
    bundle = SourceBundleBuilder(
        MemoryIncludeResolver((finance, nested))
    ).build(root)

    assert tuple(source.logical_path for source in bundle.sources) == (
        "skills/root.ns",
        "skills/common/finance.ns",
        "skills/common/base.ns",
    )
    assert tuple(item.ast.contexts[0].name for item in bundle.fragments) == (
        "currency",
        "region",
    )

    missing = _root_with_includes("missing.ns")
    with pytest.raises(CompileError) as captured:
        SourceBundleBuilder(MemoryIncludeResolver(())).build(missing)
    assert captured.value.code == DiagnosticCode.INC_RESOLUTION_FAILED


def test_inc_002_include_sources_are_composed_as_ast_not_text() -> None:
    root = _root_with_includes("common/context.ns")
    fragment = SourceFile.from_text(
        "skills/common/context.ns",
        'context { region: String from "user.region" classification INTERNAL; }',
    )
    resolver = MemoryIncludeResolver((fragment,))
    bundle = SourceBundleBuilder(resolver).build(root)
    composed = SourceComposer().compose(bundle)

    assert composed.includes == ()
    assert tuple(item.name for item in composed.contexts) == ("team_id", "region")
    assert composed.contexts[-1].span is not None
    assert composed.contexts[-1].span.source_id == fragment.source_id

    compilation = NslCompiler(build_tool_catalog(), resolver).compile(root)
    assert tuple(item.name for item in compilation.skill.contexts) == (
        "team_id",
        "region",
    )
    assert compilation.source_bundle_hash == bundle.bundle_hash


def test_inc_003_circular_include_is_compile_error() -> None:
    root = _root_with_includes("common/a.ns")
    source_a = SourceFile.from_text(
        "skills/common/a.ns", 'include "b.ns";'
    )
    source_b = SourceFile.from_text(
        "skills/common/b.ns", 'include "a.ns";'
    )
    resolver = MemoryIncludeResolver((source_a, source_b))

    with pytest.raises(CompileError) as captured:
        SourceBundleBuilder(resolver).build(root)

    assert captured.value.code == DiagnosticCode.INC_CYCLE
    assert "a.ns" in captured.value.public_message


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "/absolute.ns",
        "C:/windows.ns",
        "\\\\server\\share.ns",
        "..\\\\secret.ns",
        "../secret.ns",
        "common/not_nsl.txt",
        "common/evil\0.ns",
    ],
)
def test_inc_004_include_path_cannot_escape_configured_root(
    unsafe_path: str,
) -> None:
    root = _root_with_includes(unsafe_path)
    with pytest.raises(CompileError) as captured:
        SourceBundleBuilder(
            MemoryIncludeResolver(()), IncludeOptions(include_root="skills")
        ).build(root)
    assert captured.value.code == DiagnosticCode.INC_PATH_OUTSIDE_ROOT


def test_inc_004_configured_root_is_enforced_through_compiler() -> None:
    shared = SourceFile.from_text("skills/shared.ns", "")
    nested_root = SourceFile.from_text(
        "skills/apps/root.ns",
        _root_with_includes("../shared.ns").text,
    )
    with pytest.raises(CompileError) as captured:
        NslCompiler(
            build_tool_catalog(),
            MemoryIncludeResolver((shared,)),
            IncludeOptions(include_root="skills/apps"),
        ).compile(nested_root)
    assert captured.value.code == DiagnosticCode.INC_PATH_OUTSIDE_ROOT

    safe_bundle = SourceBundleBuilder(
        MemoryIncludeResolver((shared,)), IncludeOptions(include_root="skills")
    ).build(nested_root)
    assert safe_bundle.fragments[0].source == shared

    class MisroutingResolver:
        def resolve(self, including_source, include_path):
            return SourceFile.from_text("outside/secret.ns", "")

    valid_root = _root_with_includes("common.ns")
    with pytest.raises(CompileError) as captured:
        SourceBundleBuilder(
            MisroutingResolver(), IncludeOptions(include_root="skills")
        ).build(valid_root)
    assert captured.value.code == DiagnosticCode.INC_PATH_OUTSIDE_ROOT


def test_inc_005_diamond_include_composes_canonical_source_once() -> None:
    root = _root_with_includes("common/b.ns", "common/c.ns")
    source_b = SourceFile.from_text(
        "skills/common/b.ns", 'include "shared/d.ns";'
    )
    source_c = SourceFile.from_text(
        "skills/common/c.ns", 'include "shared/d.ns";'
    )
    source_d = SourceFile.from_text(
        "skills/common/shared/d.ns",
        'context { shared: String from "tenant.shared"; }',
    )
    resolver = MemoryIncludeResolver((source_b, source_c, source_d))
    bundle = SourceBundleBuilder(resolver).build(root)

    assert tuple(item.source.logical_path for item in bundle.fragments) == (
        "skills/common/b.ns",
        "skills/common/shared/d.ns",
        "skills/common/c.ns",
    )
    assert sum(
        item.source.source_id == source_d.source_id for item in bundle.fragments
    ) == 1
    composed = SourceComposer().compose(bundle)
    assert tuple(item.name for item in composed.contexts).count("shared") == 1


def test_inc_006_include_depth_default_boundary_is_sixteen() -> None:
    sources = tuple(
        SourceFile.from_text(
            f"skills/chain/{index}.ns",
            f'include "{index + 1}.ns";' if index < 16 else "",
        )
        for index in range(1, 17)
    )
    resolver = MemoryIncludeResolver(sources)
    root = _root_with_includes("chain/1.ns")

    allowed = SourceBundleBuilder(
        resolver, IncludeOptions(max_include_depth=16)
    ).build(root)
    assert len(allowed.fragments) == 16

    with pytest.raises(CompileError) as captured:
        SourceBundleBuilder(
            resolver, IncludeOptions(max_include_depth=15)
        ).build(root)
    assert captured.value.code == DiagnosticCode.INC_DEPTH_LIMIT

    with pytest.raises(ValueError, match="non-negative"):
        IncludeOptions(max_include_depth=-1)


def test_inc_007_include_file_count_default_boundary_is_one_hundred() -> None:
    paths = tuple(f"fragments/{index}.ns" for index in range(100))
    root = _root_with_includes(*paths)
    sources = tuple(
        SourceFile.from_text(f"skills/{path}", "") for path in paths
    )
    resolver = MemoryIncludeResolver(sources)

    allowed = SourceBundleBuilder(resolver).build(root)
    assert len(allowed.fragments) == 100

    with pytest.raises(CompileError) as captured:
        SourceBundleBuilder(
            resolver, IncludeOptions(max_include_files=99)
        ).build(root)
    assert captured.value.code == DiagnosticCode.INC_FILE_LIMIT

    with pytest.raises(ValueError, match="non-negative"):
        IncludeOptions(max_include_files=-1)


def test_inc_008_source_bundle_size_is_limited_by_utf8_bytes() -> None:
    root = _root_with_includes("fragment.ns")
    fragment = SourceFile.from_text("skills/fragment.ns", "//한")
    resolver = MemoryIncludeResolver((fragment,))
    exact_size = len(root.text.encode("utf-8")) + len(
        fragment.text.encode("utf-8")
    )

    assert IncludeOptions().max_total_source_bytes == 10 * 1024 * 1024
    allowed = SourceBundleBuilder(
        resolver, IncludeOptions(max_total_source_bytes=exact_size)
    ).build(root)
    assert len(allowed.fragments) == 1

    with pytest.raises(CompileError) as captured:
        SourceBundleBuilder(
            resolver, IncludeOptions(max_total_source_bytes=exact_size - 1)
        ).build(root)
    assert captured.value.code == DiagnosticCode.INC_BUNDLE_SIZE_LIMIT

    with pytest.raises(CompileError) as captured:
        SourceBundleBuilder(
            resolver, IncludeOptions(max_total_source_bytes=0)
        ).build(root)
    assert captured.value.code == DiagnosticCode.INC_BUNDLE_SIZE_LIMIT

    with pytest.raises(ValueError, match="non-negative"):
        IncludeOptions(max_total_source_bytes=-1)


def test_inc_009_compatible_requires_are_set_merged() -> None:
    root = _root_with_includes("requirements.ns")
    compatible = SourceFile.from_text(
        "skills/requirements.ns",
        "requires { "
        'tool PROJECT.LIST_PARENT_PROJECTS version "1.0.0"; '
        'tool PROJECT.LIST_CHILD_PROJECTS version "1.0.0"; '
        "}",
    )
    resolver = MemoryIncludeResolver((compatible,))
    composed = SourceComposer().compose(
        SourceBundleBuilder(resolver).build(root)
    )
    assert tuple((item.tool_id, item.version) for item in composed.requires) == (
        ("PROJECT.LIST_PARENT_PROJECTS", "1.0.0"),
        ("PROJECT.LIST_CHILD_PROJECTS", "1.0.0"),
    )
    NslCompiler(build_tool_catalog(), resolver).compile(root)

    conflict = SourceFile.from_text(
        "skills/requirements.ns",
        "requires { "
        'tool PROJECT.LIST_PARENT_PROJECTS version "9.0.0"; '
        "}",
    )
    conflict_bundle = SourceBundleBuilder(
        MemoryIncludeResolver((conflict,))
    ).build(root)
    with pytest.raises(CompileError) as captured:
        SourceComposer().compose(conflict_bundle)
    assert captured.value.code == DiagnosticCode.INC_TOOL_VERSION_CONFLICT


def test_inc_009_new_fragment_requirement_preserves_its_source_span() -> None:
    root = _root_with_includes("new_requirement.ns")
    fragment = SourceFile.from_text(
        "skills/new_requirement.ns",
        'requires { tool PROJECT.NEW_TOOL version "1.0.0"; }',
    )

    composed = SourceComposer().compose(
        SourceBundleBuilder(MemoryIncludeResolver((fragment,))).build(root)
    )

    added = next(
        item for item in composed.requires if item.tool_id == "PROJECT.NEW_TOOL"
    )
    assert added.version == "1.0.0"
    assert added.span is not None
    assert added.span.source_id == fragment.source_id
    assert added.span.start.line == 1
    assert added.span.start.column == 12


@pytest.mark.parametrize(
    ("fragment_text", "expected_code"),
    [
        (
            'context { team_id: TeamId from "duplicate.team"; }',
            DiagnosticCode.INC_DUPLICATE_CONTEXT,
        ),
        (
            "limits { tool_calls 1; loop_iterations 1; emitted_rows 1; "
            "collection_size 1; }",
            DiagnosticCode.INC_DUPLICATE_LIMIT,
        ),
    ],
)
def test_inc_010_duplicate_context_and_limits_never_override(
    fragment_text: str, expected_code: DiagnosticCode
) -> None:
    root = _root_with_includes("conflict.ns")
    fragment = SourceFile.from_text("skills/conflict.ns", fragment_text)
    bundle = SourceBundleBuilder(
        MemoryIncludeResolver((fragment,))
    ).build(root)

    with pytest.raises(CompileError) as captured:
        SourceComposer().compose(bundle)
    assert captured.value.code == expected_code


def test_inc_010_fragment_may_supply_the_single_limits_block() -> None:
    root = _without_root_limits(_root_with_includes("limits.ns"))
    fragment = SourceFile.from_text(
        "skills/limits.ns",
        "limits { tool_calls 11; loop_iterations 10; emitted_rows 10; "
        "collection_size 1000; }",
    )
    resolver = MemoryIncludeResolver((fragment,))

    composed = SourceComposer().compose(
        SourceBundleBuilder(resolver).build(root)
    )
    assert composed.limits is not None
    assert composed.limits.tool_calls == 11
    NslCompiler(build_tool_catalog(), resolver).compile(root)


def test_inc_010_composition_requires_one_limits_block() -> None:
    root = _without_root_limits(_root_with_includes("empty.ns"))
    fragment = SourceFile.from_text("skills/empty.ns", "")

    with pytest.raises(CompileError) as captured:
        NslCompiler(
            build_tool_catalog(), MemoryIncludeResolver((fragment,))
        ).compile(root)
    assert captured.value.code == DiagnosticCode.INC_REQUIRED_LIMITS_MISSING


def test_inc_011_dependency_graph_and_source_manifest_are_generated() -> None:
    root = _root_with_includes("common/b.ns", "common/c.ns")
    source_b = SourceFile.from_text(
        "skills/common/b.ns", 'include "shared/d.ns";'
    )
    source_c = SourceFile.from_text(
        "skills/common/c.ns", 'include "shared/d.ns";'
    )
    source_d = SourceFile.from_text("skills/common/shared/d.ns", "")
    resolver = MemoryIncludeResolver((source_b, source_c, source_d))
    bundle = SourceBundleBuilder(resolver).build(root)

    assert len(bundle.edges) == 4
    assert tuple(item.logical_path for item in bundle.manifest) == (
        "skills/root.ns",
        "skills/common/b.ns",
        "skills/common/shared/d.ns",
        "skills/common/c.ns",
    )
    assert bundle.manifest[0].is_root is True
    assert all(item.is_root is False for item in bundle.manifest[1:])
    assert all(item.content_hash.startswith("sha256:") for item in bundle.manifest)
    assert all(item.size_bytes >= 0 for item in bundle.manifest)

    compilation = NslCompiler(build_tool_catalog(), resolver).compile(root)
    assert compilation.source_manifest == bundle.manifest
    assert compilation.include_edges == bundle.edges


def test_inc_012_included_source_diagnostic_preserves_original_location() -> None:
    root = _root_with_includes("broken.ns")
    broken = SourceFile.from_text(
        "skills/broken.ns",
        "// first line\ninput { forbidden: String; }",
    )
    resolver = MemoryIncludeResolver((broken,))

    with pytest.raises(CompileError) as captured:
        SourceBundleBuilder(resolver).build(root)

    error = captured.value
    assert error.diagnostic.phase is DiagnosticPhase.PARSER
    assert error.logical_path == "skills/broken.ns"
    assert error.location == SourceLocation(2, 1)
    assert error.snippet == "input { forbidden: String; }"
    assert "skills/broken.ns" in str(error)


def test_src_008_source_include_keyword_resolves_fragment() -> None:
    root = _root_with_includes("shared/context.ns")
    fragment = SourceFile.from_text(
        "skills/shared/context.ns",
        'context { locale: String from "user.locale"; }',
    )
    result = NslCompiler(
        build_tool_catalog(), MemoryIncludeResolver((fragment,))
    ).compile(root)

    assert tuple(item.name for item in result.skill.contexts) == (
        "team_id",
        "locale",
    )
    assert result.source_manifest[-1].logical_path == "skills/shared/context.ns"


def test_src_010_root_and_fragment_logical_paths_remain_distinct() -> None:
    root = _root_with_includes("common/root.ns")
    fragment = SourceFile.from_text("skills/common/root.ns", "")
    bundle = SourceBundleBuilder(
        MemoryIncludeResolver((fragment,))
    ).build(root)

    root_entry, fragment_entry = bundle.manifest
    assert root_entry.logical_path == "skills/root.ns"
    assert fragment_entry.logical_path == "skills/common/root.ns"
    assert root_entry.source_id != fragment_entry.source_id
    assert (root_entry.is_root, fragment_entry.is_root) == (True, False)
    assert bundle.edges[0].from_source == root_entry.source_id
    assert bundle.edges[0].to_source == fragment_entry.source_id
