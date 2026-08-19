# NSL v0.1 Built-in Aggregation Semantics

- **Slice:** `0010`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 NSL v0.1 Built-in 집계 함수의 정적 Signature와 Runtime 실행을 하나의 폐쇄형 Registry로 통합한다. Compiler와 Runtime의 중복 분기를 제거하고, 빈 Collection 및 실행 오류가 결정적이고 안전한 결과를 갖도록 고정한다.

## 2. Registry 경계

`BuiltinRegistry`는 `sum`, `count`, `min`, `max`만 제공한다. 생성자 주입이나 `register` API를 제공하지 않고 인스턴스 속성 추가도 허용하지 않는다. Compiler는 Registry에서 Signature를 해석하며 Runtime은 같은 Registry에서 실행 의미를 얻는다.

```text
sum(List<Int>)      -> Int
sum(List<Decimal>)  -> Decimal
sum(List<Money(C)>) -> Money(C)
count(List<T>)      -> Int
min(List<T>)        -> T
max(List<T>)        -> T
```

`coalesce()`는 Optional/Null/Missing 의미가 확정될 때까지 v0.1 Registry에 포함하지 않는다.

## 3. 집계 정책

| Built-in | Collection 정책 | Empty 정책 |
|---|---|---|
| `sum(Int)` | 정수 합계 | `int(0)` |
| `sum(Decimal)` | Decimal 정밀도 유지 | `Decimal("0")` |
| `sum(Money(C))` | 동일 Currency만 허용 | `Money(Decimal("0"), C)` |
| `count(T)` | Collection 원소 수 | `0` |
| `min(T)` | 최소 원소 | 명시적 Runtime 오류 |
| `max(T)` | 최대 원소 | 명시적 Runtime 오류 |

모든 Built-in은 입력 Collection을 변경하지 않고 같은 입력에 같은 결과를 반환한다. 잘못된 이름, arity, Signature, IR 결과 타입은 `BuiltinSignatureError`로 닫히며, 빈 `min/max`와 비교 불가능한 Collection은 `BuiltinEvaluationError`로 닫힌다. Runtime은 이러한 오류를 `NSL-E8001` Runtime 실패로 반환하고 Check나 Output을 생성하지 않는다.

## 4. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-BLT-001` | `IMPLEMENTED` | `TEST-BLT-001` |
| `NSL-BLT-002` | `IMPLEMENTED` | `TEST-BLT-002` |
| `NSL-BLT-003` | `IMPLEMENTED` | `TEST-BLT-003` |
| `NSL-BLT-004` | `IMPLEMENTED` | `TEST-BLT-004` |
| `NSL-BLT-005` | `IMPLEMENTED` | `TEST-BLT-005` |
| `NSL-BLT-006` | `IMPLEMENTED` | `TEST-BLT-006` |
| `NSL-BLT-007` | `IMPLEMENTED` | `TEST-BLT-007` |
| `NSL-BLT-008` | `IMPLEMENTED` | `TEST-BLT-008` |
| `NSL-BLT-009` | `IMPLEMENTED` | `TEST-BLT-009` |

Slice 0010에 할당된 9개 Requirement는 모두 구현되었으며 남은 `PARTIAL`은 없다.

## 5. Slice 0003 PARTIAL 재평가

Built-in 이름과 Signature 오류는 AST SourceSpan을 사용해 Line/Column, Snippet, Logical Path를 보존한다. 다만 미선언 Tool, Tool 인자 이름 집합, 일부 Tool Contract 및 resource bound 오류의 SourceSpan 연결은 아직 남아 있다. 따라서 `NSL-ERR-002`와 `NSL-ERR-003`은 `PARTIAL`을 유지한다.

## 6. 품질 결과

각 Requirement 구현 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 결과는 다음과 같다.

- Regression: `321 passed`
- Statement Coverage: `98.84%`
- Branch Coverage: `96.01%`
- `nsl/builtins.py`: `100%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 7. Acceptance

- 9개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0010 할당 Requirement의 `PARTIAL` 0개
- Compiler와 Runtime이 동일한 폐쇄형 Registry를 사용함
- Built-in Error가 정상 완료나 Skill PASS로 변환되지 않음
- Regression failure 0
- Quality Gate 통과 전 Commit/Push 금지
