# NeX Skill Language

NSL은 NeX-AE가 선택한 업무 Skill을 제한된 규칙과 Canonical Tool Contract로 실행하는 Deterministic Execution Kernel이다.

현재 저장소에는 `FINANCE.PROJECT_BUDGET_CHECK` Vertical Slice가 구현되어 있다.

```text
.ns Source
  -> Lexer / Parser
  -> Secure Include Resolver / AST Composer
  -> Symbol Table / Scope Resolution
  -> Static Type Checker / Bool Strictness
  -> Decimal Money / Currency Semantics
  -> Tool and resource validation
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

## Local CLI Profile

`PROJECT_BUDGET_CHECK`는 실행에 필요한 Source, Principal, Tool Contract,
Input, Context, Mock Fixture를 하나의 versioned local profile로 제공한다.

```powershell
python -m nsl check --profile examples\project_budget_check.profile.json
python -m nsl compile --profile examples\project_budget_check.profile.json -o test\project_budget_check.nso
python -m nsl run --profile examples\project_budget_check.profile.json --dry-run
python -m nsl run --profile examples\project_budget_check.profile.json --timing
python -m nsl test --suite examples\project_budget_check.scenarios.json
```

Profile의 모든 경로는 profile 디렉터리 안의 canonical file로 제한된다.
명시적인 CLI option은 profile 값을 override한다. 기본 예제는 child process
isolation을 사용하며 scenario suite는 성공, 예산 초과, Tool 실패, 권한 거부,
입력 오류, resource limit, 반복 결정성을 검증한다.

## Test

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools\run_quality.py
```

`tools/run_quality.py` executes one pytest regression run with statement and branch coverage. Pytest uses the repository-local `test/` directory for temporary files and coverage reports. The quality gate requires statement coverage >= 95% and branch coverage >= 90%.

Before pytest, the command validates all 325 SRS Requirement IDs against `requirements/nsl_v0_1_traceability.json` and all 15 Slice 0031 extension requirements against `requirements/nsl_cli_local_execution_extension.json`. Requirement text or priority changes, missing Slice/Test mappings, and unsupported status claims fail the same quality gate.

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
- deterministic Symbol IDs and explicit foreach Scope frames
- duplicate, use-before-declaration, escaped iterator, and shadowing rejection
- CHECK result resolution only after declaration
- complete NSL v0.1 source type parsing, including recursive `List<T>`
- Tool result, collection element, projection, and binding type propagation
- exact Decimal literals without binary floating point types
- strict Bool assertions, comparisons, and negative truthiness cases
- direct Static Type Checker boundary and robustness tests
- finite Decimal-only Money values and immutable amount/currency fields
- ISO 4217 three-letter currency form at Core and Source boundaries
- same-currency Money arithmetic, aggregation, and explicit mixed-currency failure
- Money boundary, robustness, codec, and Runtime integration tests

Commits and remote synchronization must not proceed when the quality command fails.

## Security Boundary

`InMemorySnapshotStore` is a development-only implementation. Production must provide an encrypted, tenant-isolated Snapshot Store with retention and deletion enforcement.

NSL Runtime never stores credentials in `.nso`, ExecutionContext, Trace, or Replay Bundle. It receives a validated `ExecutionPrincipal` and records only Authorization Decision references.

The Runtime has no direct filesystem, network, SQL, Web Framework, NeX-AE, or LLM dependency. Business-system access is performed only through `ToolExecutionPort`. Unexpected Python exceptions are returned as a generic user-safe error; detailed traceback is available only through an explicitly enabled protected debug trace sink.

## Current Scope

The current parser and IR implement the acceptance slice needed by `PROJECT_BUDGET_CHECK`. They are not yet the complete NSL v0.1 language implementation. Slice 0002 establishes the verifiable SRS baseline, Slice 0003 fixes the Safe Architecture and Diagnostics boundary, Slice 0004 fixes the Source Model and Lexer contract, Slice 0005 fixes Parser and AST conformance, Slice 0006 adds secure Include resolution and structured composition, Slice 0007 fixes Symbol and Scope semantics, Slice 0008 fixes Static Type and Bool Strictness semantics, and Slice 0009 fixes Decimal Money and Currency semantics. SourceSpan propagation is complete through Symbol/Scope and type diagnostics; some tool declaration, tool argument-set, and resource-bound Semantic Diagnostics remain `PARTIAL` for a later Semantic Slice.

Include Sources are supplied through an injected `IncludeResolver`. Compiler Core performs canonical path validation and enforces configurable depth, file-count, and UTF-8 byte limits; it does not read the filesystem directly. Include dependency metadata remains in `CompilationResult` and is removed before immutable Runtime IR is produced.

Workflow Language, multi-Skill orchestration, WRITE, and APPROVAL are outside the current scope. Scheduled execution is a NeX Platform extension that repeatedly submits one registered Skill through the same execution path; it is not NSL syntax or IR.
