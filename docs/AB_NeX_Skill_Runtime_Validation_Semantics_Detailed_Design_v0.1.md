# NSL v0.1 Validation Semantics Detailed Design
## PASS / FAIL / UNKNOWN + Partial Data — Review Draft

**대상:** NSL v0.1 / NSL Runtime v1.0  
**연계 설계:** `.nso` IR, ExecutionContext, Tool Contract  
**기본 Profile:** `NSL-0.1-STRICT`

---

## 1. 설계 목적

NSL Validation의 가장 중요한 목표는 단순히 Boolean Expression을 평가하는 것이 아니다.

다음 문제를 방지해야 한다.

```text
ERP 일부 조회 실패
        ↓
일부 데이터만 집계
        ↓
관측된 합계는 예산 이하
        ↓
TRUE
        ↓
PASS
```

이와 같은 결과는 실제 Enterprise 회계·정산 업무에서는 치명적인 **False PASS**가 된다.

따라서 NSL의 CHECK는 다음 세 가지를 동시에 고려해야 한다.

```text
업무 조건의 계산 결과
        +
사용된 데이터의 완전성
        +
실행 자체의 정상 여부
```

---

## 2. 반드시 분리할 네 가지 상태

다음 네 가지를 하나의 Status로 표현하면 안 된다.

```text
① Tool Execution Status
② Data Completeness
③ Predicate Result
④ CHECK Result
```

예:

```text
Tool
    SUCCESS

Data
    COMPLETE

Predicate
    TRUE

CHECK
    PASS
```

또는:

```text
Tool
    SUCCESS

Data
    PARTIAL

Predicate
    TRUE

CHECK
    UNKNOWN
```

그리고:

```text
Tool
    TIMEOUT

Data
    생성되지 않음

Predicate
    평가되지 않음

CHECK
    생성되지 않음

Execution
    TOOL_ERROR
```

---

## 3. Presence와 Completeness 분리

기존 설계:

```text
COMPLETE
EMPTY
PARTIAL
UNKNOWN
```

대신 다음과 같이 분리한다.

### Presence

```text
PRESENT
EMPTY
```

### Completeness

```text
COMPLETE
PARTIAL
UNKNOWN
```

따라서 정상적으로 0건 조회된 Collection은:

```text
Presence
    EMPTY

Completeness
    COMPLETE
```

이다.

---

## 4. ValueEnvelope 수정안

```python
@dataclass(frozen=True)
class ValueEnvelope:
    value: object
    type_info: TypeInfo
    presence: Presence
    completeness: Completeness
    provenance_ref: str
```

예:

```text
children

value
    []

type
    List<ChildProject>

presence
    EMPTY

completeness
    COMPLETE
```

---

## 5. Tool Failure는 Data State가 아니다

다음은 허용하지 않는다.

```text
Tool Timeout
     ↓
ValueEnvelope(
    value = [],
    presence = EMPTY
)
```

Tool Failure에서는 ValueEnvelope 자체가 생성되지 않아야 한다.

```text
ToolExecutionOutcome

 ├─ ToolSuccess
 │      ↓
 │   ValueEnvelope
 │
 └─ ToolFailure
        ↓
     ToolError
```

---

## 6. Tool 결과 → ValueEnvelope 변환

### 정상 List 조회

```text
ToolSuccess
Rows = 3
Completeness = COMPLETE
```

결과:

```text
Presence     = PRESENT
Completeness = COMPLETE
```

### 정상 Empty 조회

```text
ToolSuccess
Rows = 0
Cardinality = MANY
Empty = ALLOW
```

결과:

```text
Presence     = EMPTY
Completeness = COMPLETE
```

### Pagination 일부 실패

Partial을 허용하는 Tool이라면:

```text
Presence     = PRESENT 또는 EMPTY
Completeness = PARTIAL
```

### 전체 여부 판단 불가능

```text
Presence     = PRESENT 또는 EMPTY
Completeness = UNKNOWN
```

### Tool 자체 실패

```text
ValueEnvelope 없음
Execution → TOOL_ERROR
```

---

## 7. 핵심 Validation Pipeline

```text
Tool Result
    │
    ▼
ValueEnvelope
    │
    ├─ value
    ├─ type
    ├─ presence
    ├─ completeness
    └─ provenance
    │
    ▼
Expression Evaluation
    │
    ▼
Predicate Evaluation
    │
    ├─ observed truth
    └─ assurance
    │
    ▼
CHECK Policy
    │
    ▼
PASS / FAIL / UNKNOWN
```

---

## 8. Predicate는 Boolean 값만으로 표현하지 않음

```python
@dataclass(frozen=True)
class PredicateEvaluation:
    truth: PredicateTruth
    completeness: Completeness
    provenance_refs: tuple[str, ...]
```

`PredicateTruth`:

```text
TRUE
FALSE
```

CHECK 단계에서 데이터 신뢰도를 함께 고려한다.

---

## 9. 기본 CHECK Mapping

`NSL-0.1-STRICT` Profile은 다음 Rule을 사용한다.

| Predicate | Completeness | CHECK |
|---|---|---|
| TRUE | COMPLETE | PASS |
| FALSE | COMPLETE | FAIL |
| TRUE | PARTIAL | UNKNOWN |
| FALSE | PARTIAL | UNKNOWN |
| TRUE | UNKNOWN | UNKNOWN |
| FALSE | UNKNOWN | UNKNOWN |

가장 중요한 원칙은:

> **TRUE + PARTIAL ≠ PASS**

이다.

마찬가지로:

> **FALSE + PARTIAL도 v0.1에서는 UNKNOWN**

으로 처리한다.

---

## 10. PARTIAL인데 이미 FAIL이 확실한 경우

예:

```text
Budget = 100
현재까지 확보한 지출 = 120
나머지 일부 Data = 미수집
```

이론적으로는 추가 데이터가 무엇이 오더라도 `spent <= budget`은 FALSE이다. 하지만 v0.1에서는 Predicate별 Monotonic Reasoning을 하지 않는다.

```text
PARTIAL
    ↓
UNKNOWN
```

으로 통일한다.

향후 v0.2 이후 다음 기능을 연구할 수 있다.

```text
Conclusive Partial Evidence
Monotonic Constraint Analysis
Proven FAIL
```

---

## 11. COMPLETE 값의 기본 전파

```text
COMPLETE + COMPLETE → COMPLETE
```

---

## 12. PARTIAL 전파

```text
COMPLETE + PARTIAL → PARTIAL
```

계산 성공이 Completeness 승격을 의미하지 않는다.

---

## 13. UNKNOWN 전파

```text
UNKNOWN + anything → UNKNOWN
```

v0.1 전파 우선순위:

```text
UNKNOWN > PARTIAL > COMPLETE
```

---

## 14. Projection Semantics

```nsl
children.expense_amount
```

의 Completeness는 `children`을 그대로 상속한다.

---

## 15. `sum()` Semantics

### Complete + Present

```text
[30, 25, 32.5]
COMPLETE
```

결과:

```text
87.5
COMPLETE
```

### Complete + Empty

Tool Contract가 `List<Money(KRW)>`를 보장하고 Collection이 정상적으로 Empty인 경우:

```text
[]
Presence = EMPTY
Completeness = COMPLETE
```

이면:

```text
sum([])
→ Money(0, KRW)
→ Presence = PRESENT
→ Completeness = COMPLETE
```

---

## 16. Partial Collection의 `sum()`

```text
[30, 25]
Completeness = PARTIAL
```

이면:

```text
sum()
→ 55
→ Completeness = PARTIAL
```

숫자는 계산할 수 있지만 전체 합계가 아니라 현재 관측된 합계이다.

---

## 17. `count()` Semantics

- Complete Collection → COMPLETE
- Empty Complete Collection → 0 / COMPLETE
- Partial Collection → observed count / PARTIAL

---

## 18. `min()` / `max()`와 Empty

```text
min([])
max([])
```

은 값을 결정할 수 없으므로 v0.1에서는 `EMPTY_COLLECTION` Runtime Evaluation Error로 처리한다.

---

## 19. `coalesce()` 주의

현재 Type System에는 명시적인 `Optional<T>`, `Null`, `Missing` Semantics가 아직 없다. 따라서 `coalesce()`는 Built-in 후보로 유지하고 Runtime v1.0에서는 비활성 또는 제한 지원을 권장한다.

---

## 20. Arithmetic Completeness

```text
budget COMPLETE + spent COMPLETE → remaining COMPLETE
budget COMPLETE + spent PARTIAL → remaining PARTIAL
budget UNKNOWN → remaining UNKNOWN
```

---

## 21. Money Validation

`Money(KRW) + Money(USD)`는 UNKNOWN이 아니라 Runtime Error `CURRENCY_MISMATCH`이다.

---

## 22. CHECK Result Model

```python
@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: CheckStatus
    severity: CheckSeverity
    message: str | None
    condition_node_id: str
    completeness: Completeness
    provenance_refs: tuple[str, ...]
    reason_code: str | None
```

CheckStatus:

```text
PASS
FAIL
UNKNOWN
```

---

## 23. UNKNOWN Reason Code

권장 Reason:

```text
PARTIAL_INPUT
UNKNOWN_COMPLETENESS
INCOMPLETE_SOURCE
OPTIONAL_DATA_UNAVAILABLE
VALIDATOR_UNAVAILABLE
```

v0.1에서는 주로 `PARTIAL_INPUT`, `UNKNOWN_COMPLETENESS`를 사용한다.

---

## 24. FAIL과 UNKNOWN의 의미

- FAIL: 충분한 데이터를 기반으로 업무 Rule 위반이 확인됨
- UNKNOWN: 업무 Rule 위반 여부를 신뢰성 있게 결정할 수 없음

---

## 25. CHECK Severity와 Status 분리

Severity:

```text
INFO
WARNING
ERROR
```

Status:

```text
PASS
FAIL
UNKNOWN
```

은 서로 다른 개념이다.

---

## 26. `on_fail`과 UNKNOWN 분리

권장 IR:

```json
{
  "data_policy": {
    "require_complete": true,
    "on_partial": "UNKNOWN",
    "on_unknown": "UNKNOWN"
  }
}
```

`NSL-0.1-STRICT`에서는 `UNKNOWN → PASS` 설정 자체를 허용하지 않는다.

---

## 27. `.nso` CHECK Node 수정 권고

기존 `unknown_on_partial`, `unknown_to_pass` 대신 다음을 권장한다.

```json
{
  "data_policy": {
    "require_complete": true,
    "on_partial": "UNKNOWN",
    "on_unknown": "UNKNOWN"
  }
}
```

---

## 28. Execution Status와 CHECK Status 분리

Execution Status:

```text
CREATED
RUNNING
COMPLETED
FAILED
TOOL_ERROR
VALIDATION_ERROR
LIMIT_EXCEEDED
CANCELLED
```

CHECK Status:

```text
PASS
FAIL
UNKNOWN
```

`CHECK FAIL ≠ Runtime Failure`이다.

---

## 29. UNKNOWN이 있어도 Execution은 완료 가능

```text
Execution = COMPLETED
BUDGET_LIMIT = UNKNOWN
```

도 가능하다. Execution Status에 `COMPLETED_WITH_UNKNOWN`을 추가하기보다 별도 Validation Summary를 둔다.

---

## 30. Validation Summary

```python
ValidationSummary:
    total_checks
    pass_count
    fail_count
    unknown_count
    overall
```

Overall:

```text
ALL_PASS
HAS_FAIL
HAS_UNKNOWN
HAS_FAIL_AND_UNKNOWN
NO_CHECK
```

---

## 31. 정상 예산검증 사례

```text
Budget = 100,000,000 / COMPLETE
Spent  = 87,500,000 / COMPLETE
Predicate = TRUE / COMPLETE
CHECK = PASS
```

---

## 32. 예산 초과 사례

```text
Budget = 100,000,000 / COMPLETE
Spent  = 112,000,000 / COMPLETE
Predicate = FALSE / COMPLETE
CHECK = FAIL
Execution = COMPLETED
```

---

## 33. 자 프로젝트가 실제로 없는 사례

```text
Cardinality = MANY
Empty = ALLOW
Rows = 0
Presence = EMPTY
Completeness = COMPLETE
sum([]) = 0 KRW / COMPLETE
0 <= Budget → PASS
```

---

## 34. ERP Timeout 사례

```text
PROJECT.LIST_CHILD_PROJECTS
→ UPSTREAM_TIMEOUT
→ ToolFailure
→ Execution = TOOL_ERROR
```

`children=[]`, `spent=0`, CHECK를 생성하지 않는다.

---

## 35. Partial Pagination 사례

```text
Expected Pages = 5
Received = 4
partial_allowed = true
```

결과:

```text
Presence = PRESENT
Completeness = PARTIAL
Observed Spent = 73,000,000 / PARTIAL
Observed Predicate = TRUE / PARTIAL
CHECK = UNKNOWN
```

---

## 36. Partial을 허용하지 않는 Tool

```text
partial_allowed = false
Pagination 일부 실패
→ PAGINATION_INCOMPLETE
→ ToolFailure
→ Execution = TOOL_ERROR
```

---

## 37. UNKNOWN Completeness 사례

Provider가 전체 건수나 Pagination 완료 여부를 보장하지 못하면 Derived Value 및 CHECK는 UNKNOWN을 유지한다.

---

## 38. False PASS Safety Invariants

### FP-01
ToolFailure → Value 생성 금지

### FP-02
PARTIAL → PASS 금지

### FP-03
UNKNOWN Completeness → PASS 금지

### FP-04
UNKNOWN Check → PASS 자동변환 금지

### FP-05
Derived Calculation → 원천 Completeness 임의 개선 금지

### FP-06
정상 COMPLETE Empty Collection에 대한 안전한 Operation만 명시적 결과 생성 허용

### FP-07
Currency Mismatch → Runtime Error

### FP-08
Required Tool Result 누락 시 CHECK 진행 금지

---

## 39. Safe Completeness Upgrade

PARTIAL에서 COMPLETE로 자동 승격되는 것은 원칙적으로 금지한다. `sum(complete empty) → complete zero`는 원천 Collection 자체가 이미 COMPLETE이므로 예외적인 승격이 아니다.

---

## 40. Provenance와 Validation

CHECK 결과에는 Status뿐 아니라 어떤 데이터와 Tool Invocation이 사용되었는지 추적할 수 있어야 한다.

---

## 41. Audit Event

```json
{
  "event": "CHECK_COMPLETED",
  "check_id": "BUDGET_LIMIT",
  "status": "UNKNOWN",
  "reason": "PARTIAL_INPUT",
  "condition_node_id": "expr0030",
  "provenance_refs": ["prov-011", "prov-087"]
}
```

---

## 42. Replay Semantics

Replay는 동일 `.nso`, Input, Context, Tool Result Snapshot을 사용해야 하며 Presence/Completeness도 Snapshot에 포함한다.

---

## 43. Tool Snapshot 요구사항

```json
{
  "tool_id": "PROJECT.LIST_CHILD_PROJECTS",
  "status": "SUCCESS",
  "presence": "PRESENT",
  "completeness": "PARTIAL",
  "value": [],
  "result_hash": "sha256:..."
}
```

---

## 44. Runtime Object 관계

```text
ToolExecutionOutcome
        │
        ▼
ValueEnvelope
        │
        ├─ Presence
        ├─ Completeness
        └─ Provenance
        │
        ▼
ExpressionEvaluator
        │
        ▼
PredicateEvaluation
        │
        ▼
CheckEvaluator
        │
        ▼
CheckResult
        │
        ▼
ValidationSummary
```

---

## 45. Python 모델 권장안

```python
class Presence(Enum):
    PRESENT = "PRESENT"
    EMPTY = "EMPTY"

class Completeness(Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"

class CheckStatus(Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
```

---

## 46. Check Evaluator 기본 알고리즘

```python
def evaluate_check(predicate, policy):
    if predicate.completeness == COMPLETE:
        if predicate.truth is True:
            return PASS
        return FAIL

    if predicate.completeness == PARTIAL:
        return UNKNOWN

    if predicate.completeness == UNKNOWN:
        return UNKNOWN
```

실제 구현에서는 Provenance와 Reason Code를 함께 반환한다.

---

## 47. v0.1 Strict Profile

```text
NSL-0.1-STRICT

COMPLETE + TRUE  → PASS
COMPLETE + FALSE → FAIL
PARTIAL          → UNKNOWN
UNKNOWN          → UNKNOWN
Tool Failure     → Execution Error
Currency Error   → Execution Error
UNKNOWN          → PASS 변환 금지
```

---

## 48. 향후 v0.2 Candidate

```text
and / or / not
exists / any / all
Optional<T>
Null / Missing Semantics
Tolerance Comparison
Partial Evidence Reasoning
Monotonic Constraint Analysis
Data Confidence
```

---

## 49. 최종 핵심 원칙

```text
값이 계산되었다
        ≠
업무판단이 가능하다
```

또한:

```text
관측된 조건이 TRUE다
        ≠
PASS할 수 있다
```

PASS를 위해서는:

```text
Condition TRUE
        +
Required Data COMPLETE
        +
Execution 정상
```

이 모두 필요하다.

---

## 50. 최종 정의

NSL의 `PASS / FAIL / UNKNOWN`은 단순 Boolean 결과가 아니다.

> **PASS는 필요한 데이터가 완전하게 확보된 상태에서 업무 Constraint가 만족되었음을 의미한다.**

> **FAIL은 필요한 데이터가 완전하게 확보된 상태에서 업무 Constraint 위반이 확인되었음을 의미한다.**

> **UNKNOWN은 Runtime은 정상 동작했으나 데이터의 완전성 또는 판단 근거가 부족하여 업무 Constraint의 만족 여부를 신뢰성 있게 결정할 수 없음을 의미한다.**

이 정의를 NSL Runtime v1.0의 Validation Semantics 기준으로 사용한다.
