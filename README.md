# NeX Skill Language

NSL은 NeX-AE가 선택한 업무 Skill을 제한된 규칙과 Canonical Tool Contract로 실행하는 Deterministic Execution Kernel이다.

현재 저장소에는 `FINANCE.PROJECT_BUDGET_CHECK` Vertical Slice가 구현되어 있다.

```text
.ns Source
  -> Lexer / Parser
  -> Secure Include Resolver / AST Composer
  -> Static name, type, tool, resource validation
  -> Immutable SkillObject / Canonical .nso
  -> Runtime
  -> Mock Tool
  -> CHECK / EMIT
  -> Protected Audit Snapshot
  -> Replay Tool Port
```

## Requirements

- Python 3.12+
- Runtime dependency 없음

## Run

```powershell
python -m nsl.vertical_slice
```

Expected business result:

```text
budget       100,000,000 KRW
spent         87,500,000 KRW
remaining     12,500,000 KRW
check         PASS
execution     COMPLETED
replay_equal  true
```

## Test

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools\run_quality.py
```

`tools/run_quality.py` executes one pytest regression run with statement and branch coverage. Pytest uses the repository-local `test/` directory for temporary files and coverage reports. The quality gate requires statement coverage >= 95% and branch coverage >= 90%.

Before pytest, the command validates all 325 SRS Requirement IDs against `requirements/nsl_v0_1_traceability.json`. Requirement text or priority changes, missing Slice/Test mappings, and unsupported status claims fail the same quality gate.

The suite covers:

- deterministic compile and `.nso` round-trip
- IR tamper rejection
- required Scope enforcement before provider invocation
- Partial Data false-PASS prevention
- Tool Failure and Empty separation
- confidential Audit redaction
- secure Snapshot reference recording
- replay-specific Scope and tenant isolation
- Live/Replay semantic equality
- boundary, robustness, and worst-case resource conditions
- stable compile diagnostic codes and safe runtime error disclosure
- AST-enforced architecture and security boundaries
- deterministic UTF-8 Source Model and token-level SourceSpan tracking
- explicit lexer token kinds, recovery diagnostics, booleans, and durations
- source-spanned Parser AST with Root Skill and Include Fragment modes
- parser golden snapshot and structured negative cases
- resolver-injected Include composition with canonical path enforcement
- include cycle, depth, file-count, UTF-8 bundle-size, and merge boundaries
- deterministic Include dependency graph, Source Manifest, and bundle hash

Commits and remote synchronization must not proceed when the quality command fails.

## Security Boundary

`InMemorySnapshotStore` is a development-only implementation. Production must provide an encrypted, tenant-isolated Snapshot Store with retention and deletion enforcement.

NSL Runtime never stores credentials in `.nso`, ExecutionContext, Trace, or Replay Bundle. It receives a validated `ExecutionPrincipal` and records only Authorization Decision references.

The Runtime has no direct filesystem, network, SQL, Web Framework, NeX-AE, or LLM dependency. Business-system access is performed only through `ToolExecutionPort`. Unexpected Python exceptions are returned as a generic user-safe error; detailed traceback is available only through an explicitly enabled protected debug trace sink.

## Current Scope

The current parser and IR implement the acceptance slice needed by `PROJECT_BUDGET_CHECK`. They are not yet the complete NSL v0.1 language implementation. Slice 0002 establishes the verifiable SRS baseline, Slice 0003 fixes the Safe Architecture and Diagnostics boundary, Slice 0004 fixes the Source Model and Lexer contract, Slice 0005 fixes Parser and AST conformance, and Slice 0006 adds secure Include resolution and structured composition. SourceSpan propagation is complete through included Source diagnostics; general Semantic Diagnostic SourceSpan and Source Snippet propagation remains `PARTIAL` for a later Semantic Slice.

Include Sources are supplied through an injected `IncludeResolver`. Compiler Core performs canonical path validation and enforces configurable depth, file-count, and UTF-8 byte limits; it does not read the filesystem directly. Include dependency metadata remains in `CompilationResult` and is removed before immutable Runtime IR is produced.

Workflow Language, multi-Skill orchestration, WRITE, and APPROVAL are outside the current scope. Scheduled execution is a NeX Platform extension that repeatedly submits one registered Skill through the same execution path; it is not NSL syntax or IR.
