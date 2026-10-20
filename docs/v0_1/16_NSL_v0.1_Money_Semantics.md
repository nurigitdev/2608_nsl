# NSL v0.1 Money Semantics

- **Slice:** `0009`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 Money를 통화가 결합된 불변 Decimal 값으로 고정하고, 모든 금액 계산에서 Binary Floating Point와 자동 환율 변환을 차단한다. Core, Compiler, Runtime이 같은 통화 규칙을 사용하며 Mixed Currency는 Skill PASS로 진행되지 않고 명시적인 오류가 된다.

## 2. Architecture 결정

```text
Money<Currency> Source / Tool Contract
  -> ISO 4217 code-form validation
  -> TypeRef(kind=money, currency=C)
  -> Money(amount=Decimal, currency=C)
  -> add / subtract / compare / sum_money
  -> typed IR / Runtime ValueEnvelope
  -> explicit MoneyError on semantic failure
```

1. `Money`는 frozen, slotted value object이며 `amount`와 `currency`를 함께 보유한다.
2. amount는 유한한 Python `decimal.Decimal`만 허용한다. `float`, `int`, 문자열, NaN, Infinity는 생성 경계에서 거부한다.
3. currency는 ISO 4217 code 형태인 3자 대문자 ASCII 알파벳으로 검증한다. v0.1은 전체 ISO 코드 목록 Registry까지 내장하지 않는다.
4. `money_type()`과 Source `Money<C>`가 같은 통화 형식 규칙을 사용한다. 잘못된 Source 통화는 위치가 있는 `NSL-E1103` Parser 오류가 된다.
5. 덧셈, 뺄셈, ordering comparison은 동일 Currency에서만 수행한다. 자동 환율 변환은 없다.
6. Money collection 합계는 Core `sum_money()`가 소유하며 모든 원소가 선언 Currency와 일치해야 한다.
7. 빈 Money collection은 정적 `Money<C>`의 Currency를 사용해 `Money(Decimal("0"), C)`를 반환한다.
8. `MoneyError`는 예상 가능한 사용자 안전 오류이고 Mixed Currency는 `CurrencyMismatchError`로 구분한다. Runtime은 이를 `NSL-E8001`로 반환한다.
9. Money와 Decimal codec은 숫자를 문자열로 직렬화해 Binary Floating Point 변환을 피한다.

## 3. Slice 0009 Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-MNY-001` | `IMPLEMENTED` | `TEST-MNY-001` |
| `NSL-MNY-002` | `IMPLEMENTED` | `TEST-MNY-002` |
| `NSL-MNY-006` | `IMPLEMENTED` | `TEST-MNY-006` |
| `NSL-MNY-008` | `IMPLEMENTED` | `TEST-MNY-008` |
| `NSL-PY-005` | `IMPLEMENTED` | `TEST-PY-DECIMAL` |
| `NSL-TST-005` | `IMPLEMENTED` | `TEST-TST-MONEY` |

Slice 0009에 미완료 상태로 할당됐던 6개 Requirement는 모두 구현되었으며 남은 `PARTIAL`은 없다.

## 4. 기존 Money Requirement 재검증

| Requirement | Status | Verification |
|---|---|---|
| `NSL-MNY-003` | `IMPLEMENTED` | `TEST-MNY-003` |
| `NSL-MNY-004` | `IMPLEMENTED` | `TEST-MNY-004` |
| `NSL-MNY-005` | `IMPLEMENTED` | `TEST-MNY-005` |
| `NSL-MNY-007` | `IMPLEMENTED` | `TEST-MNY-007` |

동일 Currency 덧셈/뺄셈, Mixed Currency 직접 연산 금지, 자동 변환 금지, 명시적 오류 반환을 새 경계 및 Runtime 통합 테스트와 함께 다시 검증했다. 전체 Baseline 상태는 `IMPLEMENTED 144`, `PARTIAL 136`, `PLANNED 45`이다.

## 5. Boundary와 Robustness 검증

- `0.1 + 0.2 == 0.3`을 Decimal Money로 정확히 계산한다.
- 0, 음수 0, `1E-999999`, `-1E+999999` 값을 codec 왕복한다.
- NaN, signaling NaN, 양/음 Infinity를 거부한다.
- 빈, 단일, 다중, 음수 포함 Money collection 합계를 검증한다.
- non-Money 원소와 Mixed Currency 원소를 합계에서 거부한다.
- 2자, 4자, 소문자, 숫자 포함, 공백 포함, 비-ASCII Currency를 거부한다.
- Mixed Currency Tool 결과가 `PROJECT_BUDGET_CHECK`의 CHECK/EMIT 이전에 `NSL-E8001`로 실패하는지 검증한다.

## 6. Slice 0003 PARTIAL 재평가

- `NSL-ERR-002`: 잘못된 Money Currency Source는 Line/Column과 Logical Path를 제공한다. 일부 Tool 및 Resource Semantic 오류의 Source 위치 연결이 남아 `PARTIAL`을 유지한다.
- `NSL-ERR-003`: 잘못된 Money Currency Source는 Source Snippet을 제공한다. 같은 잔여 Semantic 오류의 Snippet 연결이 남아 `PARTIAL`을 유지한다.

## 7. Acceptance

구조 리팩터링 전후와 각 Requirement 구현 직후 `tools/run_quality.py`로 Traceability, 전체 Regression, Statement/Branch Coverage를 검증했다.

- Slice 0009의 6개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0009 할당 Requirement의 `PARTIAL` 0개
- 기존 Money Requirement 4개 회귀 검증 완료
- Regression failure 0
- Statement Coverage가 Slice 0008 최종값 이상
- Branch Coverage가 Slice 0008 최종값 이상
- Quality Gate 통과 전 Commit/Push 금지
