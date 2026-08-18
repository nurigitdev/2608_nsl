# NSL Development and Quality Gate v0.1

- **상태:** Mandatory Engineering Process
- **작성일:** 2026-08-18
- **적용 대상:** NSL Core, Compiler, Runtime, Tool Adapter, Workflow/Pack 연계 코드

## 1. 목적

모든 기능 추가와 버그 수정은 구조 검토, 필요한 선행 리팩터링, Unit Test, Regression Test, Coverage Gate를 통과한 후에만 Commit 및 원격 저장소 동기화를 허용한다.

## 2. Mandatory Development Flow

```text
Change Request
    -> Structural Review
    -> Refactor Decision
    -> Refactor First, if required
    -> Source Change
    -> Unit Test Design
    -> Pytest Regression + Coverage
    -> Quality Gate
    -> Commit / Push
```

Gate 실패 시 Commit/Push 단계로 진행하지 않는다.

## 3. Structural Review

Source 수정 전에 최소 다음을 확인한다.

- 변경 책임이 현재 Module의 소유 범위와 일치하는가
- Compiler, Runtime, Tool, Audit, Replay 경계를 침범하는가
- Stable `core/` 또는 `ir/` Contract를 변경하는가
- 순환 Dependency 또는 Infrastructure 직접 의존을 만드는가
- Mutable State와 Object Lifetime이 명확한가
- Test Double 없이 외부 시스템에 결합되는가
- 기존 사용자 변경사항과 충돌하는가

구조 문제로 테스트와 변경이 어려우면 기능 수정 전에 리팩터링한다. 리팩터링 전후에는 동일 Regression Suite가 통과해야 한다.

## 4. Current Structural Review Result

초기 Vertical Slice 검토에서 다음 책임 집중을 확인했다.

```text
compiler.py
  Lexer + AST + Parser + Lowering

runtime.py
  Request/Result Model + Execution State + Engine
```

선행 리팩터링 결과:

```text
syntax.py
  Token / Lexer / AST / Parser

compiler.py
  Semantic Lowering / Static Bound / Public Compiler

runtime_models.py
  Request / Result / ExecutionContext State

runtime.py
  Preflight / Statement / Expression Execution
```

외부 `nsl.compiler`, `nsl.runtime` Import Contract는 유지한다.

## 5. Unit Test Design

Boundary Value Analysis를 기본으로 한다.

```text
Numeric / Limit
  0
  1
  MAX - 1
  MAX
  MAX + 1

Collection
  empty
  one item
  exact maximum
  over maximum

Data State
  COMPLETE
  PARTIAL
  UNKNOWN
  Tool Failure

Security
  valid scope
  missing scope
  wrong tenant
  missing principal field
```

Robustness Test:

- malformed Source/IR
- unknown Identifier/Tool/Node
- wrong runtime type
- contract hash mismatch
- missing context path
- snapshot not found

Worst-case Test:

- declared maximum Tool calls, loops, emits, collection size
- maximum을 한 단계 초과한 실행
- Partial pagination과 upstream timeout
- Replay call count/argument mismatch
- confidential output과 Audit redaction

## 6. Regression and Coverage Command

Development dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
```

Mandatory single command:

```powershell
.\.venv\Scripts\python.exe tools\run_quality.py
```

이 명령은 pytest를 한 번 실행하여 다음을 함께 수행한다.

- Unit/Regression Test
- Statement Coverage
- Branch Coverage
- Terminal missing-line report
- JSON/XML Coverage artifact
- Coverage threshold enforcement

## 7. Temporary Test Directory

Pytest 임시 Directory는 Repository Root의 `test/`를 사용한다.

```text
test/
  coverage.json
  coverage.xml
  pytest temporary files
```

`test/`는 생성 Artifact이며 Git에 포함하지 않는다. Pytest의 `--basetemp=test` 외 목적으로 Production Data를 저장하지 않는다.

## 8. Quality Threshold

```text
Statement Coverage >= 95.00%
Branch Coverage    >= 90.00%
Regression Failure = 0
```

Threshold는 전체 `nsl` Source를 기준으로 계산한다. Critical Module의 Coverage가 낮으면 전체 수치가 통과하더라도 Risk 기반 Test를 추가한다.

## 9. Commit / Synchronization Rule

Commit 또는 Push 직전 다음이 모두 참이어야 한다.

1. Structural Review 결과가 변경사항에 반영됨
2. 필요한 Refactoring이 기능 변경보다 먼저 완료됨
3. 전체 pytest Regression이 성공함
4. Statement와 Branch Coverage Gate가 성공함
5. `test/`, `.venv/`, Coverage Artifact가 Git 대상에서 제외됨
6. 변경 문서와 실행 Contract가 일치함

