# NSL Repository Engineering Rules

These rules are mandatory for every feature, bug fix, refactor, and release change.

## Before Source Changes

1. Review the affected source structurally before implementation.
2. Check module ownership, dependency direction, mutable state, public contracts, and test seams.
3. If the current structure makes the change unsafe or difficult to test, refactor first.
4. Run the existing regression suite before and after a behavior-preserving refactor.
5. Identify affected SRS Requirement IDs and update their Traceability status, Slice, Verification ID, and evidence when behavior changes.

## Test Design

- Add unit tests for every behavior change.
- Use Boundary Value Analysis by default: `0`, `1`, `MAX-1`, `MAX`, `MAX+1` where applicable.
- Include empty, single-item, maximum-size, and over-limit collections.
- Include robustness cases for malformed input, invalid state, missing data, and dependency failure.
- Include worst-case resource, authorization, tenant-isolation, partial-data, and replay cases when relevant.

## Mandatory Quality Gate

Use the project virtual environment and run exactly one pytest regression/coverage command through:

```powershell
.\.venv\Scripts\python.exe tools\run_quality.py
```

The command must report:

- regression failures: `0`
- statement coverage: `>= 95.00%`
- branch coverage: `>= 90.00%`

Pytest temporary files and coverage artifacts must use the repository-local `test/` directory configured by `--basetemp=test`.

Do not commit or push when the mandatory quality gate fails.

The quality command also validates `requirements/nsl_v0_1_traceability.json`. Do not mark a Requirement `IMPLEMENTED` without repository evidence, and do not change a Requirement row without explicitly rebaselining its fingerprint.

## Architecture Boundaries

- `core` must not depend on compiler, runtime, tools, or infrastructure.
- `syntax` must not depend on IR, runtime, tools, audit, or replay.
- Logical NSL input must use `SourceFile`; plain strings are normalized only at public compatibility boundaries.
- Every lexer token must retain its `SourceSpan`, raw lexeme, explicit `TokenKind`, and normalized value.
- Use `Lexer.tokenize()` for fail-fast compilation and `Lexer.scan()` when collecting recovery diagnostics.
- Every Parser-created AST node must retain the SourceSpan derived from its token range.
- Parse Root Skills and Include Fragments with explicit `ParseMode` values; do not infer a mode from content.
- Resolve Include Sources only through an injected `IncludeResolver`; Compiler Core must not access the filesystem directly.
- Canonicalize and validate every Include path against `IncludeOptions.include_root` before trusting Resolver output.
- Enforce Include depth, unique file-count, and total UTF-8 Source byte limits before composition.
- Compose Include Fragments as AST nodes, never by concatenating Source text, and remove Include declarations before IR lowering.
- Keep Include graph, Source Manifest, and bundle hash in compile metadata; they must not enter Runtime IR.
- Keep collection field access source-oriented in AST and lower it to Projection only after type analysis.
- `ir` must not depend on syntax, compiler, runtime, audit, or replay.
- `compiler` must not depend on runtime, audit, or replay.
- `runtime` must not depend on syntax or compiler.
- NSL Core and Runtime must not depend on LLMs, NeX-AE, or Web Frameworks.
- Runtime business-system access must use `ToolExecutionPort`; direct filesystem, network, and SQL access is forbidden.
- `eval()`, `exec()`, dynamic import, and unreviewed external module imports are forbidden in production NSL modules.
- Unexpected Runtime exceptions must use a generic user-safe message. Detailed traceback may be sent only to an explicitly enabled protected debug sink.
- Keep credentials and customer-specific endpoints outside NSL Source, IR, Trace, and Replay bundles.
