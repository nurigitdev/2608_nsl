# NSL v0.1 CHECK and False PASS Prevention

- **Slice:** `0020`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 10 Requirements

## 1. 목적

이 Slice는 CHECK의 판정 책임과 Runtime 실행 책임을 분리하고, 누락되거나 불완전한 Tool Data가 PASS로 승격되는 경로를 차단한다. 판정 결과에는 사용된 Fact의 provenance와 Data Completeness를 보존해 결과와 Audit이 같은 의미를 갖도록 한다.

## 2. 내부 Part

| Part | 범위 | Requirement |
|---|---|---|
| A. CHECK Semantics | Bool 평가, PASS/FAIL/UNKNOWN, Audit, Message, Evaluator 경계 | `NSL-VAL-001..004`, `NSL-VAL-007..010` |
| B. False PASS Prevention | Required Tool Result 누락 차단, Completeness Audit | `NSL-FP-002`, `NSL-FP-005` |

Slice 0001에서 이미 구현된 `NSL-VAL-005`, `NSL-VAL-006`, `NSL-VAL-011..013`, `NSL-FP-001`, `NSL-FP-003..004`, `NSL-FP-006`, `NSL-TST-008`은 전체 회귀 테스트로 계속 검증한다.

## 3. 구조 경계

```text
CHECK expression
    -> ValueEnvelope
    -> PredicateEvaluation
    -> StrictRuleEvaluator
    -> CheckResult
    -> ExecutionResult / Audit
```

`RuntimeEngine`은 표현식 평가와 실행 흐름을 담당하고 `CheckEvaluator`는 CHECK의 판정만 담당한다. v0.1 실행 경로는 `StrictRuleEvaluator`로 고정하며, 향후 추론 기능은 typed `ReasoningValidatorAdapter` 경계 뒤에 추가할 수 있다. Adapter는 이번 Slice의 판정 경로에 연결하지 않아 현재 의미론과 결정성을 바꾸지 않는다.

## 4. CHECK Semantics

| Predicate | Completeness | Result |
|---|---|---|
| exact `True` | `COMPLETE` | `PASS` |
| exact `False` | `COMPLETE` | `FAIL` |
| `True` 또는 `False` | `PARTIAL` | `UNKNOWN` |
| `True` 또는 `False` | `UNKNOWN` | `UNKNOWN` |

Python의 `0`, `1` 또는 문자열 같은 truthy/falsy 값은 Bool로 인정하지 않는다. 변조된 IR이 Bool TypeRef와 non-bool 값을 함께 제공해도 Runtime Contract 오류로 종료하며 CHECK 결과를 생성하지 않는다.

`CheckResult`는 check ID, status, severity, message, condition node ID, Presence, Completeness, provenance와 안정적인 reason code를 보존한다. `PARTIAL_INPUT`과 `UNKNOWN_COMPLETENESS`는 UNKNOWN의 원인을 구분한다.

## 5. False PASS Prevention

- Required READ 결과가 `EMPTY`이고 Tool Contract가 empty를 유효한 결과로 선언하지 않으면 `REQUIRED_TOOL_RESULT_MISSING` Tool 오류로 종료한다.
- 누락 오류 뒤에는 CHECK와 output을 생성하지 않는다.
- `PARTIAL` 또는 `UNKNOWN` Tool 결과는 CHECK `UNKNOWN`으로 전파한다.
- Tool과 CHECK Audit Event 모두 Data Completeness를 기록한다.
- CHECK Audit은 condition node, Presence, Completeness, reason과 provenance를 함께 기록한다.

## 6. Boundary와 Robustness

- exact Bool `False`, `True`와 non-bool `0`, `1`, 문자열, `None`을 검증했다.
- 변조된 truthy Bool IR이 PASS를 만들지 못하는지 검증했다.
- COMPLETE+TRUE, COMPLETE+FALSE, PARTIAL과 UNKNOWN의 모든 truth 조합을 검증했다.
- Required Tool Result 누락 시 CHECK와 output이 모두 비어 있는지 검증했다.
- PARTIAL과 UNKNOWN 각각이 Tool Audit과 CHECK Audit에 같은 Completeness로 기록되는지 검증했다.
- Unicode CHECK Message가 Runtime Result와 semantic view에서 손실되지 않는지 검증했다.

## 7. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-VAL-001..004` | `IMPLEMENTED` | `TEST-VAL-001..004` |
| `NSL-VAL-007..010` | `IMPLEMENTED` | `TEST-VAL-007..010` |
| `NSL-FP-002` | `IMPLEMENTED` | `TEST-FP-002` |
| `NSL-FP-005` | `IMPLEMENTED` | `TEST-FP-005` |

Slice 0020에서 새로 구현한 10개 Requirement는 모두 `IMPLEMENTED`이며 남은 `PARTIAL`은 없다. 전체 Baseline 상태는 `IMPLEMENTED 248`, `PARTIAL 32`, `PLANNED 45`다.

## 8. Slice 0003 PARTIAL 재평가

CHECK Audit은 IR node와 판정 사유를 구조적으로 제공하지만 `.ns` Source의 Line/Column과 Snippet은 아니다. Unsupported language/risk와 일부 EMIT schema/classification 오류의 SourceSpan 공백도 남아 있으므로 `NSL-ERR-002`와 `NSL-ERR-003`은 `PARTIAL`을 유지한다.

## 9. 품질 결과

각 Requirement 변경 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 결과는 다음과 같다.

- Regression: `583 passed`
- Statement Coverage: `99.13%`
- Branch Coverage: `97.40%`
- Slice 시작값: Statement `99.12%`, Branch `97.39%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 10. Acceptance

- 10개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0020 범위의 `PARTIAL` 없음
- Runtime 실행과 CHECK 판정의 구조적 책임이 분리됨
- COMPLETE+TRUE만 PASS를 생성함
- Required Tool Result 누락이 CHECK에 도달하지 못함
- Completeness와 provenance가 Runtime Result 및 Audit에서 보존됨
- 전체 Regression failure 0
- 기존 Statement/Branch Coverage 이상 유지
- Quality Gate 통과 전 Commit/Push 금지
