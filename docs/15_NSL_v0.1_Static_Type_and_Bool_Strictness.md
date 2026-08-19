# NSL v0.1 Static Type and Bool Strictness

- **Slice:** `0008`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 Source Type을 AST, Symbol Binding, Tool Contract, IR Expression까지 결정적으로 전파하고 잘못된 Type 사용을 Compile 단계에서 차단한다. Python truthiness와 Bool의 Int 호환성이 NSL 의미 규칙에 유입되지 않도록 Bool을 엄격하게 분리한다.

## 2. Architecture 결정

```text
Source Type / Tool Contract
  -> Parser TypeRef
  -> SymbolTable Binding
  -> StaticTypeChecker
       exact type
       List element / projection
       record field
       sum argument
       binary / Bool rule
  -> typed immutable IR
```

1. Type 규칙은 `type_system.py`의 `StaticTypeChecker`가 소유하며 Compiler Lowerer는 이를 호출한다.
2. `semantic_diagnostics.py`의 `SourceDiagnosticContext`를 Symbol과 Type 검사에서 공유해 Include Source의 원래 위치를 보존한다.
3. Source Type은 `String`, `Int`, `Decimal`, `Bool`, `Date`, `DateTime`, `Year`, `Money<CURRENCY>`, Domain Type, `List<T>`, `CheckStatus`를 지원한다.
4. `List<T>`는 재귀적으로 구문 분석하고 foreach iterator, collection field Projection, `sum` 결과까지 원소 Type을 유지한다.
5. `Float`와 `Double`은 Source Type으로 제공하지 않으며 Decimal literal은 Python `decimal.Decimal`로 생성한다.
6. Tool input은 Contract Type과 정확히 일치해야 하고 Tool result Type은 Read IR과 let binding에 보존된다.
7. `sum`은 `List<Int>`, `List<Decimal>`, `List<Money<CURRENCY>>`만 허용한다.
8. CHECK condition은 정확히 `Bool`이어야 하며 Int, String, List truthiness를 허용하지 않는다.
9. Bool은 `==`와 `!=`만 허용하고 ordering 및 arithmetic operator를 거부한다.
10. v0.1은 Bool 변환 Built-in이나 암묵 변환을 제공하지 않는다.
11. Int 나눗셈은 Decimal을 반환하고 Python `Decimal` 연산으로 실행한다. Decimal 산술은 Decimal을 유지하며 Money는 같은 Type의 덧셈과 뺄셈만 허용한다.
12. Ordering은 Int, Decimal, 동일 Currency Money에만 허용하며 equality는 정확히 같은 Type 사이에서만 허용한다.

## 3. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-TYP-001` | `IMPLEMENTED` | `TEST-TYP-001` |
| `NSL-TYP-002` | `IMPLEMENTED` | `TEST-TYP-002` |
| `NSL-TYP-003` | `IMPLEMENTED` | `TEST-TYP-003` |
| `NSL-TYP-004` | `IMPLEMENTED` | `TEST-TYP-004` |
| `NSL-TYP-005` | `IMPLEMENTED` | `TEST-TYP-005` |
| `NSL-TYP-006` | `IMPLEMENTED` | `TEST-TYP-006` |
| `NSL-TYP-007` | `IMPLEMENTED` | `TEST-TYP-007` |
| `NSL-TYP-008` | `IMPLEMENTED` | `TEST-TYP-008` |
| `NSL-TYP-009` | `IMPLEMENTED` | `TEST-TYP-009` |
| `NSL-TYP-010` | `IMPLEMENTED` | `TEST-TYP-010` |
| `NSL-TYP-011` | `IMPLEMENTED` | `TEST-TYP-011` |
| `NSL-TYP-012` | `IMPLEMENTED` | `TEST-TYP-012` |
| `NSL-TYP-013` | `IMPLEMENTED` | `TEST-TYP-013` |
| `NSL-TST-004` | `IMPLEMENTED` | `TEST-TST-004` |
| `NSL-TST-015` | `IMPLEMENTED` | `TEST-TST-BOOL` |

Slice 0008에 할당된 15개 Requirement는 모두 구현되었으며 할당 범위 안에 남은 `PARTIAL`은 없다. 전체 Baseline 상태는 `IMPLEMENTED 138`, `PARTIAL 142`, `PLANNED 45`이다.

## 4. Boundary와 Robustness 검증

- 모든 v0.1 Type과 재귀 `List<T>` 구문을 Compiler 입력 경계에서 검증한다.
- Tool input mismatch와 unknown record/list field는 원래 Source 위치를 가진 Compile Error로 거부한다.
- `0.1`과 `0.2` literal이 Python `Decimal` 값으로 정확히 보존되는지 확인한다.
- Int 나눗셈의 IR Type과 Runtime 값이 모두 Decimal인지 확인하고 Python float 입력을 Decimal로 허용하지 않는다.
- `true`와 `false` 양쪽 값을 독립적으로 Bool IR에 보존한다.
- Int, String, List를 CHECK condition으로 사용한 truthiness 음성 사례를 검증한다.
- Bool equality/inequality 정상 사례와 네 ordering operator 음성 사례를 검증한다.
- conversion 형태 호출, Bool arithmetic, `sum(List<Bool>)`을 모두 거부한다.
- malformed `List` TypeRef, 비-list foreach/sum 입력, 필드 없는 primitive와 type mismatch를 직접 검사한다.
- `StaticTypeChecker` statement와 branch coverage를 모두 100%로 유지한다.

## 5. Slice 0003 PARTIAL 재평가

- `NSL-ERR-002`: Type 관련 Semantic 오류는 Line/Column과 Logical Path를 제공한다. 일부 Tool 및 Resource Semantic 오류의 Source 위치 연결이 남아 `PARTIAL`을 유지한다.
- `NSL-ERR-003`: Type 관련 Semantic 오류는 Source Snippet을 제공한다. 같은 잔여 Semantic 오류의 Snippet 연결이 남아 `PARTIAL`을 유지한다.

두 요구사항은 모든 Source 기반 Compile Error가 동일한 위치 계약을 만족한 뒤에만 `IMPLEMENTED`로 승격한다.

## 6. Acceptance

구조 리팩터링 전후와 각 Requirement 구현 직후 `tools/run_quality.py`로 Traceability, 전체 Regression, Statement/Branch Coverage를 검증했다.

- 15개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0008 할당 Requirement의 `PARTIAL` 0개
- Regression failure 0
- Statement Coverage가 Slice 0007 최종값 이상
- Branch Coverage가 Slice 0007 최종값 이상
- Quality Gate 통과 전 Commit/Push 금지
