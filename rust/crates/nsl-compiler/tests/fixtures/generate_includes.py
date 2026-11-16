"""Record include composition and diagnostics from the frozen Python Oracle."""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(sys.argv[1]).resolve()))
from nsl.diagnostics import CompileError
from nsl.includes import IncludeOptions, SourceBundleBuilder, SourceComposer, canonical_include_path
from nsl.source import SourceFile, SourceId


LIMITS = "limits { tool_calls 1; loop_iterations 2; emitted_rows 3; collection_size 4; }"


def field(text):
    return text.encode("utf-8").hex() or "-"


def coords(location):
    return f"{location.offset},{location.line},{location.column}"


def span_fields(span):
    return span.source_id, coords(span.start), coords(span.end)


def type_fields(ty):
    depth = 0
    while ty.kind == "list":
        depth += 1
        ty = ty.item
    return depth, ty.kind, field(ty.currency or ty.name)


class Resolver:
    def __init__(self, responses, root):
        self.responses = responses
        self.root = root
        self.calls = []

    def resolve(self, including_source, include_path):
        key = canonical_include_path(including_source.logical_path, include_path, self.root)
        self.calls.append((including_source.logical_path, key))
        return self.responses[key]


def emit(label, root, sources=(), *, scope="skills", depth=16, files=100, size=10485760):
    print("case", label, field(scope), depth, files, size, sep="\t")
    print("root", field(root.logical_path), field(root.text), sep="\t")
    responses = {}
    for key, path, text, source_id in sources:
        print("source", field(key), field(path), field(text), field(source_id or ""), sep="\t")
        responses[key] = SourceFile.from_text(path, text, SourceId(source_id) if source_id else None)
    print("expected")
    resolver = Resolver(responses, scope)
    stage = "build"
    try:
        bundle = SourceBundleBuilder(resolver, IncludeOptions(scope, depth, files, size)).build(root)
        print("bundle", field(root.logical_path), bundle.bundle_hash, sep="\t")
        for entry in bundle.manifest:
            print("manifest", entry.source_id, field(entry.logical_path), entry.content_hash,
                  entry.size_bytes, str(entry.is_root).lower(), sep="\t")
        for edge in bundle.edges:
            print("edge", edge.from_source, edge.to_source, *span_fields(edge.include_span), sep="\t")
        stage = "compose"
        skill = SourceComposer().compose(bundle)
        print("skill", skill.language_version, field(skill.skill_id), len(skill.includes),
              len(skill.inputs), len(skill.outputs), len(skill.body), *span_fields(skill.span), sep="\t")
        for tool in skill.requires:
            print("tool", field(tool.tool_id), field(tool.version), *span_fields(tool.span), sep="\t")
        for item in skill.contexts:
            print("context", field(item.name), item.classification, *type_fields(item.type_info),
                  *span_fields(item.span), *(field(part) for part in item.path), sep="\t")
        block = skill.limits
        print("limits", block.tool_calls, block.loop_iterations, block.emitted_rows,
              block.collection_size, block.duration_ms, *span_fields(block.span), sep="\t")
    except CompileError as error:
        location = f"{error.location.line},{error.location.column}" if error.location else "-"
        print("error", stage, error.code, field(error.logical_path or ""), location, sep="\t")
    for parent, target in resolver.calls:
        print("call", field(parent), field(target), sep="\t")


def include(path):
    quoted = path.replace("\\", "\\\\").replace('"', '\\"')
    return f'include "{quoted}";'


def source(name, text="", *, path=None, source_id=None):
    return name, path or name, text, source_id


def main():
    print("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a")
    for version in ("0.1", "0.2"):
        def root(*declarations, limits=LIMITS, path="skills/app/root.ns", other=""):
            text = (f'// 주석😀\r\nlanguage NSL "{version}";\r\n'
                    f'skill TEST.ROOT {{ version "1"; risk READ_ONLY;\n{limits}\n'
                    f'{other}\n' + "\n".join(map(include, declarations)) + "\n}")
            return SourceFile.from_text(path, text)

        def run(label, root_source, *responses, **options):
            emit(f"{version}:{label}", root_source, responses, **options)

        run("no_includes_at_zero", root(), depth=0, files=0)
        run("root_only_other_scope", root(), scope="other")
        run("root_supplies_metadata", root(other='input { x: Int; } output { out: Int; } '
                                           'let y = x; emit { out: y; }'))
        extra = ('requires { tool SAME version "old"; tool BASE version "1"; tool SAME version "2"; } '
                 'context { duplicate: Int from "before"; base: Bool from "p"; duplicate: Int from "after"; }')
        embedded = root("./sub//left.ns", "sub/./right.ns", other=extra, path="skills/./app/root.ns")
        run("diamond_and_order", embedded,
            source("skills/app/sub/left.ns", include("../공유.ns") +
                   'requires { tool SAME version "2"; tool LEFT version "3"; } '
                   'context { left: List<Money<KRW>> from "x..y" classification CONFIDENTIAL; }'),
            source("skills/app/공유.ns", 'context { shared: Int from "부서"; }', source_id="fixed:shared"),
            source("skills/app/sub/right.ns", include("./../공유.ns") +
                   'requires { tool RIGHT version "4"; } context { right: Int from "end"; }'),
            depth=2, files=3)
        run("canonical_duplicate", root("./same.ns", "inner/../same.ns"),
            source("skills/app/same.ns"), depth=1, files=1)
        run("no_files", root("same.ns"), source("skills/app/same.ns"), files=0)
        run("too_many_files", root("first.ns", "second.ns"),
            source("skills/app/first.ns"), source("skills/app/second.ns"), files=1)
        run("zero_depth", root("same.ns"), source("skills/app/same.ns"), depth=0)
        run("depth_at_one", root("same.ns"), source("skills/app/same.ns"), depth=1)
        run("depth_before_resolution", root("same.ns"),
            source("skills/app/same.ns", include("missing.ns")), depth=1)
        run("unsafe_path_after_depth", root("/escape.ns"), depth=0)
        chain = tuple(source(f"skills/app/{i}.ns", include(f"{i+1}.ns") if i < 17 else "")
                      for i in range(1, 18))
        run("default_depth_exceeded", root("1.ns"), *chain)
        run("exact_depth_sixteen", root("1.ns"), *chain[:15], source("skills/app/16.ns"))
        run("root_cycle", root("root.ns"),
            source("skills/app/root.ns", root("root.ns").text))
        run("nested_cycle", root("sub/a.ns"), source("skills/app/sub/a.ns", include("../b.ns")),
            source("skills/app/b.ns", include("./sub/../sub/a.ns")))
        run("depth_before_cycle", root("root.ns"), depth=0)
        run("fragment_limits", root("base.ns", limits=""),
            source("skills/app/base.ns", LIMITS.replace("size 4;", "size 4; duration 1m;")))
        run("limits_missing", root("empty.ns", limits=""), source("skills/app/empty.ns"))
        run("fragment_limits_repeat", root("base.ns", limits=""),
            source("skills/app/base.ns", LIMITS + LIMITS))
        run("duplicate_limits", root("base.ns"), source("skills/app/base.ns", LIMITS))
        run("duplicate_context", root("base.ns", other='context { a: Int from "one"; }'),
            source("skills/app/base.ns", '// 조각\ncontext { a: Int from "two"; }'))
        run("version_conflict", root("base.ns", other='requires { tool T version "1"; }'),
            source("skills/app/base.ns", 'requires { tool T version "2"; }'))
        run("fragment_version", root("base.ns"),
            source("skills/app/base.ns", 'requires { tool write version "1"; }'))
        run("fragment_parser_error", root("base.ns"), source("skills/app/base.ns", 'emit {}'))
        run("invalid_root_header", SourceFile.from_text("skills/root.ns", "skill TEST"))
        run("missing_source", root("missing.ns"))
        run("misrouting", root("expected.ns"), source("skills/app/expected.ns", path="elsewhere.ns"))
        run("alias_response_is_not_canonical", root("expected.ns"),
            source("skills/app/expected.ns", path="skills/app/./expected.ns"))

        for label, path in (("absolute", "/other.ns"), ("drive", "C:/other.ns"),
                            ("unc", "//server/share.ns"), ("backslash", "sub\\file.ns"),
                            ("escape", "../../top.ns"), ("escape_reenter", "../../skills/ok.ns"),
                            ("nul", "sub/invalid\0.ns"), ("extension", "sub/wrong.txt"), ("empty", "")):
            run(label, root(path))
        run("root_outside_scope", root("base.ns", path="outside/root.ns"))
        run("root_prefix_is_not_segment", root("base.ns", path="skills/app2/root.ns"), scope="skills/app")
        run("scoped_parent_escape", root("../base.ns"), scope="skills/app")
        run("half_normalized_scope", root("../base.ns"), source("skills/base.ns"), scope="./skills//")

        root_source = root("bytes.ns", "./bytes.ns")
        response = source("skills/app/bytes.ns", "// 해😀\r\n")
        root_bytes = len(root_source.text.encode("utf-8"))
        total_bytes = root_bytes + len(response[2].encode("utf-8"))
        run("root_bytes_under", root_source, response, size=root_bytes - 1)
        run("root_exact_budget", root_source, response, size=root_bytes)
        run("utf8_bundle_under", root_source, response, size=total_bytes - 1)
        run("utf8_exact_budget", root_source, response, size=total_bytes)
        run("utf8_bundle_extra_byte", root_source, response, size=total_bytes + 1)
        run("file_before_bytes", root_source, response, size=root_bytes, files=0)


if __name__ == "__main__":
    main()
