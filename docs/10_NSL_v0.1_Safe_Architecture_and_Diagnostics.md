# NSL v0.1 Safe Architecture and Diagnostics

- **Slice:** `0003`
- **상태:** Accepted with two documented partial requirements
- **작성일:** 2026-08-18
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 NSL Core와 Runtime이 NeX-AE, LLM, Web Framework, 직접 Infrastructure 접근으로부터 독립된 결정적 실행 커널이라는 경계를 코드와 회귀 검사로 고정한다. 또한 컴파일 진단과 Runtime 실패가 사용자에게 안전하게 전달되고, 상세 traceback은 명시적인 debug 채널에서만 제공되도록 한다.

## 2. Architecture 결정

1. NSL package는 Python 3.12 이상에서 동작하며 Runtime dependency를 갖지 않는다.
2. NSL Core는 LLM, NeX-AE, Web Framework에 의존하지 않는다.
3. Runtime의 업무 시스템 접근은 `ToolExecutionPort`를 통해서만 수행한다.
4. Runtime Kernel은 filesystem, network, database/SQL infrastructure를 직접 사용하지 않는다.
5. AST Node는 `syntax.py`에 명시적인 frozen, slotted dataclass로 정의한다.
6. `eval()`, `exec()`, 동적 import와 검토되지 않은 외부 module import는 architecture regression gate에서 차단한다.

Runtime Kernel 범위는 `audit.py`, `builtins.py`, `core.py`, `ir.py`, `replay.py`, `runtime.py`, `runtime_models.py`, `security.py`이다. 개발용 `vertical_slice.py`의 fixture 및 파일 로딩은 Runtime Kernel에 포함하지 않는다.

## 3. Diagnostic 계약

컴파일 오류는 다음 불변 구조를 사용한다.

```text
Diagnostic
  code: DiagnosticCode
  phase: LEXER | PARSER | INCLUDE | SEMANTIC
  message: public message
  location: optional line/column
  snippet: optional source line
  logical_path: optional logical Source path
```

- 모든 현재 Lexer, Parser, Semantic, Runtime 오류는 SRS Error Category에 맞는 안정된 `NSL-E1xxx`~`NSL-E8xxx` 코드를 갖는다.
- Tool provider의 원래 오류 식별자는 표준 NSL 오류 코드와 섞지 않고 `detail_code`로 보존한다.
- 공개 `Diagnostic`에는 내부 Exception 객체를 저장하지 않는다. 내부 원인은 Python exception chaining으로만 연결한다.
- 예상 가능한 Runtime validation 오류만 통제된 공개 문구를 사용한다.
- 예기치 않은 Exception은 일반 사용자와 Audit에 `An unexpected runtime error occurred.`로만 기록한다.
- 상세 traceback은 `debug_mode=True`와 보호된 `debug_trace_sink`가 함께 제공된 경우에만 해당 sink로 전달한다.
- debug sink 실패는 실행 실패 처리 자체를 방해하지 않는다.

## 4. Security 회귀 경계

Architecture test는 문자열 검색이 아니라 Python AST를 사용한다.

- built-in `eval`, `exec`, `__import__` 호출 금지
- production package의 외부 import allowlist 적용
- Runtime Kernel의 filesystem/network/database module import 금지
- Runtime Kernel의 `open()`, cursor/script SQL API 직접 호출 금지
- Runtime의 업무 시스템 호출이 `tools.execute()`에만 존재하는지 확인
- 변조 IR의 미등록 `tool_ref`가 Executor 호출 전에 차단되는지 확인
- 위조된 StaticAnalysis와 관계없이 tool call, loop, emit, collection Runtime limit 집행

## 5. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-ARC-004` | `IMPLEMENTED` | `TEST-ARC-004` |
| `NSL-ARC-005` | `IMPLEMENTED` | `TEST-ARC-005` |
| `NSL-ARC-006` | `IMPLEMENTED` | `TEST-ARC-006` |
| `NSL-ARC-007` | `IMPLEMENTED` | `TEST-ARC-007` |
| `NSL-ERR-001` | `IMPLEMENTED` | `TEST-ERR-001` |
| `NSL-ERR-002` | `PARTIAL` | `TEST-ERR-002-PARTIAL` |
| `NSL-ERR-003` | `PARTIAL` | `TEST-ERR-003-PARTIAL` |
| `NSL-ERR-004` | `IMPLEMENTED` | `TEST-ERR-004` |
| `NSL-ERR-005` | `IMPLEMENTED` | `TEST-ERR-005` |
| `NSL-ERR-006` | `IMPLEMENTED` | `TEST-ERR-006` |
| `NSL-PY-001` | `IMPLEMENTED` | `TEST-PY-001` |
| `NSL-PY-002` | `IMPLEMENTED` | `TEST-PY-002` |
| `NSL-PY-003` | `IMPLEMENTED` | `TEST-PY-003` |
| `NSL-SEC-001` | `IMPLEMENTED` | `TEST-SEC-001` |
| `NSL-SEC-002` | `IMPLEMENTED` | `TEST-SEC-002` |
| `NSL-SEC-003` | `IMPLEMENTED` | `TEST-SEC-003` |
| `NSL-SEC-004` | `IMPLEMENTED` | `TEST-SEC-004` |
| `NSL-SEC-005` | `IMPLEMENTED` | `TEST-SEC-005` |
| `NSL-SEC-006` | `IMPLEMENTED` | `TEST-SEC-006` |
| `NSL-SEC-007` | `IMPLEMENTED` | `TEST-SEC-007` |
| `NSL-SEC-008` | `IMPLEMENTED` | `TEST-SEC-008` |

전체 Baseline 상태는 `IMPLEMENTED 62`, `PARTIAL 202`, `PLANNED 61`이다.

## 6. PARTIAL 사유

`NSL-ERR-002`는 Lexer와 Parser 오류에 구조화된 Line/Column을 제공하고, Slice 0005에서 Parser가 생성하는 모든 AST Node도 SourceSpan을 보존한다. Slice 0006의 Include 오류와 Slice 0007의 Symbol/Scope 오류도 원래 Source 위치와 Logical Path를 보존한다. 다만 type, tool, resource 등 일반 Semantic 오류 전체가 아직 해당 AST 위치를 Diagnostic으로 전달하지 않는다.

`NSL-ERR-003`은 Lexer, SourceFile을 제공받은 Parser, Include, Symbol/Scope 오류에 Source Snippet을 제공한다. 일반 Semantic 단계 전체에는 아직 SourceSpan 기반 Snippet 전파가 적용되지 않았다.

두 요구사항은 미완료 상태를 숨기지 않고 `PARTIAL`로 유지한다. Source, Token, Parser AST, Include, Symbol/Scope까지의 위치 보존은 완료했으며, 남은 Semantic Diagnostic 연결은 후속 Semantic Slice에서 완료한다.

### Slice 0008 재평가

Slice 0008에서 `SourceDiagnosticContext`를 공통 진단 구성 요소로 추출하고 `StaticTypeChecker`가 이를 사용하도록 했다. 이에 따라 foreach collection, CHECK condition, output expression, record/list field, `sum`, binary expression, Tool input type 오류는 원래 Source의 Line/Column, Snippet, Logical Path를 보존한다.

미선언 Tool, Tool 인자 이름 집합, 일부 Tool Contract 및 resource bound 오류는 여전히 AST SourceSpan을 진단에 전달하지 않는다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0008 종료 시점에도 `PARTIAL`을 유지한다.

### Slice 0009 재평가

Slice 0009에서 잘못된 `Money<CURRENCY>` 선언은 통화 Token의 Line/Column, Snippet, Logical Path를 보존하는 Parser 오류로 반환된다. Mixed Currency는 `MoneyError` 계층을 통해 사용자에게 안전한 Runtime 오류로 반환되지만 Source 기반 Compile Diagnostic은 아니다.

Slice 0008에서 확인한 Tool 선언, Tool 인자 이름 집합, 일부 Tool Contract 및 resource bound 오류의 SourceSpan 공백은 그대로 남아 있다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0009 종료 시점에도 `PARTIAL`을 유지한다.

### Slice 0010 재평가

Slice 0010에서 Built-in 이름과 Signature 오류가 `StaticTypeChecker`의 `SourceDiagnosticContext`를 통과하도록 통합되었다. 비활성 `coalesce()`를 포함한 Built-in Compile 오류는 원래 Source의 Line/Column, Snippet, Logical Path를 보존한다.

그러나 미선언 Tool, Tool 인자 이름 집합, 일부 Tool Contract 및 resource bound 오류에는 여전히 SourceSpan이 연결되지 않는다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0010 종료 시점에도 `PARTIAL`을 유지한다.

### Slice 0011 재평가

Slice 0011에서 `requires`의 각 Tool 선언을 독립적인 `AstRequiredTool` Source Node로 승격했다. 중복 Tool, 존재하지 않는 Canonical Contract, 호환되지 않는 Version, non-READ Capability 오류는 해당 선언의 Line/Column, Snippet, Logical Path를 보존한다. 미선언 `read`, Tool 인자 이름 집합, Tool 인자 Type 오류도 원래 `AstRead` 또는 인자 SourceSpan을 사용한다.

Tool 계약 영역의 Source Diagnostic 공백은 해소됐지만, static resource bound와 일부 상위 Semantic/EMIT 오류는 아직 SourceSpan을 전달하지 않는다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0011 종료 시점에도 `PARTIAL`을 유지한다.

### Slice 0012 재평가

Slice 0012에서 Tool Call, Loop, Emit의 static resource bound 초과 오류가 `limits` 선언의 Line/Column, Snippet, Logical Path를 보존하도록 통합됐다. 분석 불가능한 실행 구조도 안정적인 `NSL-E6104`와 Source 위치를 가진 Compile 오류로 닫힌다.

Resource Bound 영역의 Source Diagnostic 공백은 해소됐지만, unsupported language/risk와 일부 EMIT schema/classification 오류는 아직 해당 AST SourceSpan을 전달하지 않는다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0012 종료 시점에도 `PARTIAL`을 유지한다.

### Slice 0013 재평가

Slice 0013의 `.nso` Schema 오류는 `NsoSchemaError.path`로 누락되거나 잘못된 JSON 필드의 정확한 위치를 제공한다. 이는 Source Language Diagnostic이 아닌 artifact load validation 영역이다.

Unsupported language/risk와 일부 EMIT schema/classification Source 오류에는 여전히 해당 AST SourceSpan이 연결되지 않는다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0013 종료 시점에도 `PARTIAL`을 유지한다.

### Slice 0014 재평가

Slice 0014는 비신뢰 `.nso`의 UTF-8/JSON/Schema/Integrity 오류를 `NsoSchemaError`와 `NsoIntegrityError`로 구분해 fail-closed 처리한다. Artifact 오류는 JSON path 또는 안정적인 무결성 사유를 제공하지만 `.ns` Source Diagnostic은 아니다.

Unsupported language/risk와 일부 EMIT schema/classification Source 오류에는 여전히 해당 AST SourceSpan이 연결되지 않는다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0014 종료 시점에도 `PARTIAL`을 유지한다.

### Slice 0015 재평가

Slice 0015는 Runtime Contract 오류를 구조화된 `ExecutionResult`로 반환하고 Execution ID를 보존한다. 이 오류는 이미 Compile된 `SkillObject` 또는 `.nso`의 실행 오류이며 `.ns` Source Diagnostic은 아니다.

Unsupported language/risk와 일부 EMIT schema/classification Source 오류에는 여전히 해당 AST SourceSpan이 연결되지 않는다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0015 종료 시점에도 `PARTIAL`을 유지한다.

### Slice 0016 재평가

Slice 0016은 Expression 평가 오류에 안정적인 `NSL-E8001`, IR Expression `node_id`, detail code를 부여한다. 이는 Compile된 IR의 Runtime 위치이며 `.ns` Source의 Line/Column 또는 Snippet은 아니다. READ provider의 미계약 예외는 내부 정보를 노출하지 않는 `NSL-E8002` 경계를 유지한다.

Unsupported language/risk와 일부 EMIT schema/classification Source 오류에는 여전히 해당 AST SourceSpan이 연결되지 않는다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0016 종료 시점에도 `PARTIAL`을 유지한다.

### Slice 0017 재평가

Slice 0017은 Tool Registry의 중복·Version·Schema 오류와 Runtime의 required Tool resolution 및 contract mismatch를 안정적인 Registry/Runtime 오류로 처리한다. 이 오류들은 Canonical Tool Contract 또는 실행 중 IR 위치에 관한 것이며 `.ns` Source의 Line/Column 또는 Snippet을 새로 제공하지 않는다.

Unsupported language/risk와 일부 EMIT schema/classification Source 오류에는 여전히 해당 AST SourceSpan이 연결되지 않는다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0017 종료 시점에도 `PARTIAL`을 유지한다.

### Slice 0018 재평가

Slice 0018은 미등록 READ tool reference를 예상하지 못한 예외가 아닌 안정적인 Runtime Contract 오류로 전환하고, parameter·result·timeout 오류를 명시적인 Tool 오류로 구조화한다. 정상 Source의 중복 READ parameter는 기존 AST SourceSpan을 사용해 Compiler 진단 위치를 제공한다.

그러나 변조된 IR의 READ 오류는 `.ns` Source가 아니라 IR node에 관한 오류이며, Unsupported language/risk와 일부 EMIT schema/classification Source 오류에도 AST SourceSpan 연결이 남아 있다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 Slice 0018 종료 시점에도 `PARTIAL`을 유지한다.

## 7. Acceptance

각 Requirement 변경 후 `tools/run_quality.py`로 Traceability, 전체 Regression, Statement/Branch Coverage를 반복 검증했다. Slice 완료 기준은 다음과 같다.

- 21개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- 완료되지 않은 Requirement가 `PARTIAL`로 명시됨
- Regression failure 0
- Statement Coverage 95% 이상
- Branch Coverage 90% 이상
- Quality Gate 통과 전 Commit/Push 금지
