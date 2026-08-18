# NeX Skill Language

NSL은 NeX-AE가 선택한 업무 Skill을 제한된 규칙과 Canonical Tool Contract로 실행하는 Deterministic Execution Kernel이다.

현재 저장소에는 `FINANCE.PROJECT_BUDGET_CHECK` Vertical Slice가 구현되어 있다.

```text
.ns Source
  -> Lexer / Parser
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

Commits and remote synchronization must not proceed when the quality command fails.

## Security Boundary

`InMemorySnapshotStore` is a development-only implementation. Production must provide an encrypted, tenant-isolated Snapshot Store with retention and deletion enforcement.

NSL Runtime never stores credentials in `.nso`, ExecutionContext, Trace, or Replay Bundle. It receives a validated `ExecutionPrincipal` and records only Authorization Decision references.

## Current Scope

The current parser and IR implement the acceptance slice needed by `PROJECT_BUDGET_CHECK`. They are not yet the complete NSL v0.1 language implementation. The next language milestone is a complete normative EBNF, JSON Schema for `.nso`, stable diagnostics, and conformance fixtures.
