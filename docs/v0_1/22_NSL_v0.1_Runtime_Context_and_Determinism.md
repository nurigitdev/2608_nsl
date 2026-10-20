# NSL v0.1 Runtime Context and Determinism

- **Slice:** `0015`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **확정 범위:** 14 Requirements

## 1. 목적

이 Slice는 Compiler가 생성한 in-memory `SkillObject`와 load가 완료된 `.nso`가 하나의 Runtime 경로를 사용하도록 고정한다. 각 실행은 독립된 `ExecutionContext`를 생성하며, namespace와 frame, statement order, expression evaluation이 ambient state 없이 결정적으로 동작해야 한다.

## 2. 내부 Part

| Part | 범위 | Requirement |
|---|---|---|
| A. Entry and Lifecycle | Source/NSO 진입, Context 생성, Execution ID | `NSL-RT-001..003`, `NSL-RT-011` |
| B. Namespace and Order | 공간 분리, Source-order 실행 | `NSL-RT-004..005` |
| C. Determinism | 실행 및 Expression 의미 결정성 | `NSL-RT-006`, `NSL-REL-001` |
| D. Safe Evaluator Boundary | 동적 실행/Reflection/AST 경로 금지, async 의존성 | `NSL-RT-007..010`, `NSL-RT-012`, `NSL-PY-006` |

## 3. 단일 Runtime 경로

```text
.ns Source -> Compiler -> in-memory SkillObject
                                  |
.nso bytes -> strict NSO load ----+
                                  v
                         RuntimeEngine.execute()
```

Runtime은 Source, Lexer, Parser, Compiler를 호출하지 않는다. `.ns` 실행은 Compiler가 만든 `SkillObject`를 전달하는 integration 경로이고 `.nso` 실행은 `NsoCodec.decode()` 결과를 전달하는 artifact 경로다. 두 경로는 같은 `RuntimeEngine`을 사용한다.

## 4. ExecutionContext Namespace

```text
ExecutionContext
  input_values       write-once execution input
  context_values     write-once declared runtime context
  frames[]           lexical variable frames
  check_frames[]     lexical CHECK result frames
  checks[]           ordered CHECK result log
  outputs[]          ordered EMIT buffer
  resources          per-execution resource meter
  invocation_counter deterministic Tool invocation sequence
```

Input, Context, Variable, Check, Emit 공간은 별도 collection으로 유지한다. 모든 Symbol ID는 namespace 전체에서 write-once이며 Variable과 Check frame은 foreach iteration마다 함께 push/pop된다. `finally`에서 frame을 회수하므로 iteration 오류가 발생해도 실행 Context의 frame stack은 손상되지 않는다.

기존 내부 import 호환을 위해 `_ExecutionContext`는 `ExecutionContext` alias로 유지한다.

## 5. Execution Identity

Execution ID는 Runtime이 시간, random 또는 UUID에서 암묵적으로 만들지 않는다. 호출자가 비어 있지 않은 문자열을 제공해야 하며 정상 및 실패 `ExecutionResult`가 같은 ID를 보존한다.

Execution ID는 개별 실행 identity이고 expression semantics에는 포함되지 않는다. 서로 다른 Execution ID를 사용해도 동일 `.nso`, Input, Context, Tool Result의 `semantic_view()`는 동일하다.

## 6. Determinism Contract

동일 Skill, Execution ID, Input, Context, Tool Result를 10회 실행해 다음을 비교한다.

- 전체 immutable `ExecutionResult`
- Statement/Tool/Check/Emit Audit event 순서와 payload
- `inv0001`부터 시작하는 Tool invocation sequence
- Resource usage와 output/check order

별도 Reliability 검증은 Execution ID만 변경하고 expression-derived `semantic_view()`를 비교한다. 시간, random, UUID는 Skill semantic input으로 노출하지 않는다. 시간이 필요한 Skill은 선언된 Runtime Context로 값을 받아야 한다.

## 7. Safe Evaluator Boundary

- Runtime kernel에서 Python `eval()`과 `exec()` 호출 금지
- `getattr`, `setattr`, `hasattr`, `vars`, `locals`, `globals`, `dir` 등 동적 reflection API 금지
- 7개 IR Expression class를 `_evaluate()`가 명시적 `isinstance` closed dispatch로 처리
- Runtime의 `compiler`, `source`, `syntax`, `Ast*` 참조 금지
- Runtime Core는 native `async def/await`를 사용하고 별도 async framework dependency를 요구하지 않음

## 8. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-RT-001` | `IMPLEMENTED` | `TEST-RT-001` |
| `NSL-RT-002` | `IMPLEMENTED` | `TEST-RT-002` |
| `NSL-RT-003` | `IMPLEMENTED` | `TEST-RT-003` |
| `NSL-RT-004` | `IMPLEMENTED` | `TEST-RT-004` |
| `NSL-RT-005` | `IMPLEMENTED` | `TEST-RT-005` |
| `NSL-RT-006` | `IMPLEMENTED` | `TEST-RT-006` |
| `NSL-RT-007` | `IMPLEMENTED` | `TEST-RT-007` |
| `NSL-RT-008` | `IMPLEMENTED` | `TEST-RT-008` |
| `NSL-RT-009` | `IMPLEMENTED` | `TEST-RT-009` |
| `NSL-RT-010` | `IMPLEMENTED` | `TEST-RT-010` |
| `NSL-RT-011` | `IMPLEMENTED` | `TEST-RT-011` |
| `NSL-RT-012` | `IMPLEMENTED` | `TEST-RT-012` |
| `NSL-REL-001` | `IMPLEMENTED` | `TEST-REL-001` |
| `NSL-PY-006` | `IMPLEMENTED` | `TEST-PY-006` |

Slice 0015에 할당된 14개 Requirement는 모두 구현됐으며 남은 `PARTIAL`은 없다.

## 9. Slice 0003 PARTIAL 재평가

Runtime 오류의 Execution ID와 구조화된 결과는 완성됐지만 `.ns` Source Error의 Line/Column과 Snippet 요구사항과는 별도 영역이다. Unsupported language/risk와 일부 EMIT schema/classification 오류에 AST SourceSpan 연결이 남아 있으므로 `NSL-ERR-002`와 `NSL-ERR-003`은 `PARTIAL`을 유지한다.

## 10. 품질 결과

각 Requirement 변경 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 코드 결과는 다음과 같다.

- Regression: `427 passed`
- Statement Coverage: `99.04%`
- Branch Coverage: `97.01%`
- `nsl/runtime_models.py`: `100%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 11. Acceptance

- 14개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0015 할당 Requirement의 `PARTIAL` 0개
- Source와 NSO가 동일 Runtime Engine 경로를 사용함
- 실행마다 독립된 ExecutionContext를 생성함
- Input/Context/Variable/Check/Emit namespace가 물리적으로 분리됨
- 동일 semantic input에서 결과와 event order가 결정적임
- 동적 Python 실행, reflection, Source AST interpreter 경로가 없음
- Regression failure 0
- Quality Gate 통과 전 Commit/Push 금지
