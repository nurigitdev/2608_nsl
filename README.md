# NeX Skill Language v0.2

NSL은 NeX-AE가 선택한 업무 Skill을 제한된 규칙과 Canonical Tool Contract로 실행하는 Deterministic Execution Kernel이다.

NSL v0.2 release는 이전 v0.1 READ 실행과의 호환성을 유지하면서 Approval,
controlled WRITE, Commit, Verification, Audit, Replay 실행 경계를 제공한다.

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
- `cryptography>=49.0.0,<50.0.0` (wheel 설치 시 자동 설치)

## Install

NeX-Platform은 승인된 내부 Artifact Repository의 NSL wheel을 정확한 버전으로
설치하고 framework-neutral API인 `nsl.api`를 통해 연동한다.

```powershell
python -m pip install nex_skill_language-0.2.0-py3-none-any.whl
python -c "import nsl.api; print(nsl.api.__version__)"
```

Release wheel 생성과 무결성 검증은 다음 명령으로 수행한다.

```powershell
.\.venv\Scripts\python.exe tools\wheel_release.py
```

검증된 wheel과 동일 이름의 `.sha256` 파일이 `dist`에 생성된다.

## Source Checkout Demo

The repository example files are intentionally not bundled in the Runtime
wheel. From a Source checkout, run the v0.1 compatibility demonstration with:

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

CLI의 JSON stdout과 stderr는 key 순서를 고정한 2-space pretty JSON으로
표시한다. `--result-out`, `--audit-out`과 process isolation wire는 기계 처리와
무결성 검증을 위해 기존 canonical JSON 형식을 유지한다.

## Test

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe tools\run_quality.py
```

`tools/run_quality.py` executes one pytest regression run with statement and branch coverage. Pytest uses the repository-local `test/` directory for temporary files and coverage reports. The v0.2 quality gate requires statement coverage >= 99% and branch coverage >= 98%.

Before pytest, the command validates the v0.1 and v0.2 SRS traceability baselines,
the v0.2 readiness and acceptance baselines, and all 15 Slice 0031 extension
requirements. Requirement text or priority changes, missing Slice/Test mappings,
and unsupported status claims fail the same quality gate.

## Business Controls Showcase

`examples/nsl_showcase_pack`은 직무별 업무 통제를 독립 Skill로 제공하는 고객
시연용 Pack이다. 첫 구현인 Corporate Card Control은 CHECK 없는 팀별 월 합계
Summary와 6개 정책을 검증하는 Policy Check를 함께 제공한다.

```powershell
python -m nsl test --suite examples\nsl_showcase_pack\corporate_card\summary.scenarios.json
python -m nsl test --suite examples\nsl_showcase_pack\corporate_card\policy.scenarios.json
```

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
- backward-compatible NSL v0.1 source type parsing, including recursive `List<T>`
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

NSL v0.2 supports deterministic compile and execution for READ validation and
single controlled WRITE flows with Approval suspend/resume, idempotency,
preflight policy enforcement, commit outcome handling, post-write verification,
persistent Audit, and no-live-write Replay. The wheel exposes the stable
NeX-Platform integration contracts through `nsl.api`.

Include Sources are supplied through an injected `IncludeResolver`. Compiler
Core performs canonical path validation and enforces depth, file-count, and
UTF-8 byte limits without direct filesystem access. Runtime business access is
performed only through injected ports.

Workflow Language and multi-Skill orchestration remain outside NSL. Scheduled
execution is a NeX-Platform extension that repeatedly submits one registered
Skill through the same execution path; it is not NSL syntax or IR.
