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

Runtime Kernel 범위는 `audit.py`, `core.py`, `ir.py`, `replay.py`, `runtime.py`, `runtime_models.py`, `security.py`이다. 개발용 `vertical_slice.py`의 fixture 및 파일 로딩은 Runtime Kernel에 포함하지 않는다.

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

`NSL-ERR-002`는 Lexer와 Parser 오류에 구조화된 Line/Column을 제공하고, Slice 0005에서 Parser가 생성하는 모든 AST Node도 SourceSpan을 보존한다. Slice 0006의 Include 해석 및 합성 오류도 원래 Source 위치와 Logical Path를 보존한다. 다만 일반 Semantic 오류 전체가 아직 해당 AST 위치를 Diagnostic으로 전달하지 않는다.

`NSL-ERR-003`은 Lexer, SourceFile을 제공받은 Parser, Include 해석 및 합성 오류에 Source Snippet을 제공한다. 일반 Semantic 단계 전체에는 아직 SourceSpan 기반 Snippet 전파가 적용되지 않았다.

두 요구사항은 미완료 상태를 숨기지 않고 `PARTIAL`로 유지한다. Source, Token, Parser AST까지의 위치 보존은 완료했으며, 남은 Semantic Diagnostic 연결은 후속 Semantic Slice에서 완료한다.

## 7. Acceptance

각 Requirement 변경 후 `tools/run_quality.py`로 Traceability, 전체 Regression, Statement/Branch Coverage를 반복 검증했다. Slice 완료 기준은 다음과 같다.

- 21개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- 완료되지 않은 Requirement가 `PARTIAL`로 명시됨
- Regression failure 0
- Statement Coverage 95% 이상
- Branch Coverage 90% 이상
- Quality Gate 통과 전 Commit/Push 금지
