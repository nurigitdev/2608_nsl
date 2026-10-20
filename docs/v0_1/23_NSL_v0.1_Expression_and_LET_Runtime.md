# NSL v0.1 Expression and LET Runtime

- **Slice:** `0016`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **확정 범위:** 5 Requirements

## 1. 목적

이 Slice는 IR Expression 평가와 LET binding의 Runtime 계약을 고정한다. LET은 Expression을 먼저 평가한 뒤 현재 lexical frame에 값을 한 번만 binding한다. Expression은 선언된 Type을 보존하며, 결정적 계산 오류는 NSL v0.1 STRICT 정책에 따라 구조화된 실행 실패로 처리한다.

## 2. 실행 경계

```text
LetStatement
    -> Expression evaluation
    -> Type preservation check
    -> Current frame bind-once
```

Expression 평가가 실패하면 binding은 생성되지 않는다. Binding 이후 같은 Symbol ID에 다시 값을 쓰려는 시도는 `IMMUTABLE_BINDING_ERROR`로 거부되며 기존 값은 보존된다.

## 3. Expression 구조

`_evaluate()`는 다음 공통 계약을 담당한다.

- IR Expression 7개 형상의 폐쇄 검증
- 평가 오류와 effect boundary 구분
- 결과 `ValueEnvelope.type_info`와 IR `expression.type_info` 일치 검증

`_evaluate_ir()`는 Literal, SymbolRef, Field, Projection, Builtin Call, Binary, READ를 명시적으로 dispatch한다. 자식 Expression도 공통 `_evaluate()` 경계를 다시 통과하므로 중첩 깊이와 관계없이 Type 보존 검사가 적용된다.

## 4. Immutable LET

- LET은 평가 완료된 `ValueEnvelope`를 현재 variable frame에 binding한다.
- Input, Context, Variable, Check 전체 namespace에서 Symbol ID는 write-once다.
- 중복 binding은 `ImmutableBindingError`로 실패한다.
- 실패는 `NSL-E8001`과 `IMMUTABLE_BINDING_ERROR` detail code로 반환된다.
- 음수, 0, 양수 경계값에서 동일한 계약을 적용한다.

## 5. Side Effect 경계

Literal, SymbolRef, Field, Projection, Builtin, Binary 계산은 Input, Context, frame, Check, Output, Resource Meter, invocation sequence와 원본 collection/record를 변경하지 않는다.

READ는 문법과 IR에 명시된 데이터 취득 연산이다. 임의의 Side Effect가 아니라 `requires`에 선언되고 승인된 READ-only Tool Port만 호출할 수 있으며 WRITE capability는 Runtime preflight에서 거부된다. Provider 호출과 audit/resource 계측은 이 명시적 effect boundary 안에서만 발생한다.

## 6. Type 보존

각 Expression 결과의 `ValueEnvelope.type_info`는 IR에 선언된 `expression.type_info`와 같아야 한다. 일치하지 않으면 결과를 반환하거나 LET에 binding하지 않고 `EXPRESSION_TYPE_MISMATCH`로 fail-closed 처리한다.

검증 범위는 Literal, SymbolRef, Field, Projection, Builtin Call, Binary, READ 전체다.

## 7. Evaluation Error 정책

NSL v0.1 `NSL-0.1-STRICT`에는 Expression별 UNKNOWN 복구 정책이 없다. 따라서 0 나눗셈, 누락 Field, 잘못된 Projection과 같은 결정적 계산 오류는 다음과 같이 처리한다.

- Execution Status: `FAILED`
- Error Code: `NSL-E8001`
- Detail Code: `EXPRESSION_EVALUATION_ERROR`
- Node: 실패한 IR Expression `node_id`

READ provider가 Tool 계약 밖의 예기치 않은 예외를 발생시키면 계산 오류로 오인하지 않는다. 내부 메시지와 stack trace를 일반 사용자에게 노출하지 않는 기존 `NSL-E8002` 보호 경계를 유지한다.

## 8. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-LET-001` | `IMPLEMENTED` | `TEST-LET-001` |
| `NSL-LET-002` | `IMPLEMENTED` | `TEST-LET-002` |
| `NSL-LET-003` | `IMPLEMENTED` | `TEST-LET-003` |
| `NSL-LET-004` | `IMPLEMENTED` | `TEST-LET-004` |
| `NSL-LET-005` | `IMPLEMENTED` | `TEST-LET-005` |

Slice 0016에 할당된 5개 Requirement는 모두 구현됐으며 남은 `PARTIAL`은 없다.

## 9. Slice 0003 PARTIAL 재평가

Runtime Expression 오류에 IR `node_id`가 추가됐지만 `.ns` Source Error의 Line/Column과 Snippet 요구사항과는 별도 영역이다. Unsupported language/risk와 일부 EMIT schema/classification Source 오류에 AST SourceSpan 연결이 남아 있으므로 `NSL-ERR-002`와 `NSL-ERR-003`은 `PARTIAL`을 유지한다.

## 10. 품질 결과

각 Requirement 변경 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 코드 결과는 다음과 같다.

- Regression: `437 passed`
- Statement Coverage: `99.05%`
- Branch Coverage: `97.03%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 11. Acceptance

- 5개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0016 할당 Requirement의 `PARTIAL` 0개
- LET 평가 후 bind 순서와 write-once 계약이 명시됨
- 순수 Expression이 Runtime Context와 원본 값을 변경하지 않음
- 7개 Expression 결과의 Type 보존을 검사함
- 평가 오류와 READ provider unexpected 오류 경계를 분리함
- Regression failure 0
- 기존 Coverage 지표 이상 유지
- Quality Gate 통과 전 Commit/Push 금지
