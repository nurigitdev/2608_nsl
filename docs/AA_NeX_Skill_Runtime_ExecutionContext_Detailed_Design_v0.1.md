# NeX Skill Runtime ExecutionContext Detailed Design v0.1

**문서 상태:** Review Draft  
**대상 Language:** NSL v0.1  
**대상 Runtime:** NSL Interpreter & Runtime Environment v1.0  
**관련 문서:** NeX Skill Object (`.nso`) IR Schema Detailed Design v0.1  
**작성일:** 2026-08-15

---

## 1. 목적

본 문서는 NSL Runtime에서 하나의 Skill 실행 인스턴스의 상태를 관리하는 `ExecutionContext`의 상세 설계를 정의한다.

`ExecutionContext`는 단순한 변수 저장소가 아니라 다음 정보를 일관되게 관리하는 실행 상태 객체이다.

- 입력값(Input)
- NeX-AE가 주입한 Runtime Context
- Immutable Symbol Binding
- Value Type
- Data Completeness
- Provenance
- CHECK 결과
- EMIT 결과
- Resource 사용량
- Audit / Trace
- Cancellation 및 Execution Status

핵심 원칙은 다음과 같다.

> **Runtime에서 전달되는 값은 단순 Value가 아니라 `Value + Type + Completeness + Provenance`로 취급한다.**

---

## 2. `.nso`와 ExecutionContext의 관계

`.nso`는 실행할 Logic을 정의하고, `ExecutionContext`는 그 Logic의 개별 실행 인스턴스를 나타낸다.

```text
                  .nso
            Execution Logic
                  │
                  ▼
        ┌───────────────────┐
        │ ExecutionContext  │
        │                   │
        │ execution_id      │
        │ input             │
        │ context           │
        │ symbols           │
        │ completeness      │
        │ provenance        │
        │ checks            │
        │ emitted results   │
        │ resource meter    │
        └─────────┬─────────┘
                  │
                  ▼
             NSL Runtime
```

동일한 `.nso`를 여러 사용자가 실행하는 경우 각각 독립적인 `ExecutionContext`가 생성된다.

---

## 3. Top-Level Object Model

권장 구조는 다음과 같다.

```text
ExecutionContext
├─ identity
├─ skill
├─ input_values
├─ context_values
├─ frames
├─ check_results
├─ emit_buffer
├─ resource_meter
├─ trace_sink
├─ cancellation
└─ execution_state
```

개념적인 Python Model은 다음과 같다.

```python
@dataclass
class ExecutionContext:
    execution_id: str
    nso_hash: str
    skill_id: str
    skill_version: str
    runtime_version: str

    inputs: Mapping[str, ValueEnvelope]
    contexts: Mapping[str, ValueEnvelope]

    frames: FrameStack
    checks: dict[str, CheckResult]
    emits: list[EmitRecord]

    resources: ResourceMeter
    trace_sink: TraceSink

    status: ExecutionStatus
```

`inputs`, `contexts`는 Runtime 중 변경되지 않는 Immutable Namespace로 취급한다.

---

## 4. ValueEnvelope

Runtime Value는 Raw Python Object로만 저장하지 않는다.

```python
@dataclass(frozen=True)
class ValueEnvelope:
    value: object
    type_info: TypeInfo
    completeness: Completeness
    provenance_ref: str
```

예:

```python
ValueEnvelope(
    value=Money(
        amount=Decimal("87500000"),
        currency="KRW"
    ),
    type_info=MoneyType("KRW"),
    completeness=Completeness.COMPLETE,
    provenance_ref="prov-00127"
)
```

이 구조를 통해 Runtime은 같은 숫자라 하더라도 데이터의 출처와 완전성을 구분할 수 있다.

---

## 5. Presence / Completeness Model

> **정합성 개정:** 기존 `Completeness.EMPTY` 정의는 폐기한다. `00_NSL_v0.1_Normative_Security_and_Semantics_Baseline.md`와 Validation Semantics Detailed Design을 기준으로 Presence와 Completeness를 별도 축으로 사용한다.

| 축 | 상태 | 의미 |
|---|---|---|
| Presence | `PRESENT` | 하나 이상의 값이 존재함 |
| Presence | `EMPTY` | 정상 조회됐으나 값이 0건임 |
| Completeness | `COMPLETE` | 필요한 데이터가 정상적으로 모두 확보됨 |
| Completeness | `PARTIAL` | 일부 데이터만 확보됨 |
| Completeness | `UNKNOWN` | 데이터 완전성 자체를 판단할 수 없음 |

`ERROR`는 Completeness 상태가 아니다. Tool 호출 자체가 실패했다면 Value를 정상 생성하지 않고 Tool Execution Error로 처리한다.

```text
Tool SUCCESS + 0 rows
        ↓
Presence = EMPTY
Completeness = COMPLETE

Tool SUCCESS + all rows
        ↓
Presence = PRESENT
Completeness = COMPLETE

Tool SUCCESS + pagination incomplete
        ↓
Completeness = PARTIAL

Tool SUCCESS but completeness unknown
        ↓
Completeness = UNKNOWN

Tool FAILURE
        ↓
ToolExecutionError
```

---

## 6. EMPTY와 Tool Failure의 구분

자 프로젝트가 실제로 존재하지 않는 경우:

```text
GET_CHILD_PROJECTS
MCP Success
Rows = 0
Completeness = EMPTY
```

이 경우 `sum([])`은 정상적으로 0을 반환할 수 있다.

반면 ERP 또는 MCP 호출 실패를 `[]`로 변환해서는 안 된다.

```text
Tool Failure
    ↓
[]
    ↓
sum([]) = 0
    ↓
0 <= budget
    ↓
False PASS
```

따라서 Tool Failure → Empty Collection 변환은 금지한다.

---

## 7. Provenance

모든 Value가 어디에서 생성되었는지 추적할 수 있어야 한다.

예:

```python
Provenance(
    kind="TOOL_RESULT",
    sources=["invocation-0007"]
)
```

Derived Value:

```python
Provenance(
    kind="DERIVED",
    sources=[
        "value:child-A-expense",
        "value:child-B-expense",
        "value:child-C-expense"
    ],
    expression_node="expr0040"
)
```

대량 데이터의 Lineage 복제를 방지하기 위해 `ValueEnvelope`에는 전체 Provenance가 아니라 `provenance_ref`만 저장하는 구조를 권장한다.

```text
ValueEnvelope
    │
    └─ provenance_ref
            │
            ▼
      ProvenanceStore
```

---

## 8. Completeness 전파 규칙

Derived Value는 Source의 Completeness를 임의로 개선해서는 안 된다.

예:

```text
children = COMPLETE
      ↓
Projection = COMPLETE
      ↓
SUM = COMPLETE
```

```text
children = PARTIAL
      ↓
Projection = PARTIAL
      ↓
SUM = PARTIAL
```

v0.1 기본 전파 규칙은 다음과 같이 단순화할 수 있다.

```text
COMPLETE + COMPLETE
        → COMPLETE

PARTIAL + anything
        → PARTIAL

UNKNOWN + anything
        → UNKNOWN
```

`EMPTY`는 연산 의미에 따라 처리한다.

향후에는 Presence와 Completeness를 별도 축으로 분리하는 방안을 검토할 수 있다.

---

## 9. `sum([])` Semantics

정상 조회 결과가 Empty인 경우:

```text
List<Money(KRW)>
Completeness = EMPTY
```

에 대해:

```text
sum([])
    ↓
Money(0, KRW)
Completeness = COMPLETE
```

로 변환할 수 있다.

단, Collection Type에 Currency가 확정되어 있지 않다면 임의 Currency를 생성해서는 안 된다.

---

## 10. Frame Stack

Variable Scope는 Frame Stack으로 관리한다.

```text
ExecutionContext
     │
     ▼
Frame Stack

┌──────────────────────┐
│ Loop Frame           │
│ parent = ...         │
│ children = ...       │
│ spent = ...          │
└──────────────────────┘

┌──────────────────────┐
│ Skill Frame          │
│ parents = ...        │
└──────────────────────┘
```

Input과 Runtime Context는 별도 Immutable Namespace로 유지한다.

---

## 11. Symbol Binding

`.nso`가 Runtime Symbol을 `s0001`, `s0002`와 같은 Deterministic Symbol ID로 표현하므로 ExecutionContext 역시 Source Name이 아니라 Symbol ID를 사용한다.

```python
frame.bind(
    symbol_id="s0007",
    value=ValueEnvelope(...)
)
```

```python
frame.resolve("s0007")
```

Runtime은 `spent`와 같은 Source Identifier String을 다시 해석하지 않는다.

---

## 12. Immutable Binding

`let`은 Write-once Semantic을 갖는다.

```python
class Frame:
    def bind(self, symbol_id, value):
        if symbol_id in self.values:
            raise ImmutableBindingError(...)
        self.values[symbol_id] = value
```

Python 내부 구현은 Mutable Dictionary를 사용할 수 있지만 Language Semantic은 Immutable이어야 한다.

---

## 13. Loop Frame

각 `foreach` Iteration마다 별도의 Frame을 생성한다.

```text
Skill Frame
   │
   ├─ parents
   │
   ▼
Loop Iteration #1 Frame
   ├─ parent
   ├─ children
   └─ spent

Loop Iteration #2 Frame
   ├─ parent
   ├─ children
   └─ spent
```

Iteration 종료 후 Loop Frame은 해제할 수 있으며, Audit에 필요한 실행정보는 Trace Sink로 전달한다.

---

## 14. CHECK Result

CHECK 결과는 별도 Control Flag가 아니라 Runtime Value로 표현하는 것을 권장한다.

```python
CheckResult(
    status=PASS,
    severity=ERROR,
    message="...",
    condition_node_id="expr0030"
)
```

CHECK Result도 Symbol ID에 Binding하여 다음 표현을 지원한다.

```text
BUDGET_LIMIT.status
```

---

## 15. CHECK Result 결정 규칙

```text
Bool(TRUE)
Completeness = COMPLETE
        ↓
PASS
```

```text
Bool(FALSE)
Completeness = COMPLETE
        ↓
FAIL
```

```text
Bool(?)
Completeness = PARTIAL / UNKNOWN
        ↓
UNKNOWN
```

중요한 원칙:

> `TRUE + PARTIAL`은 PASS가 아니다.

부분 데이터에서 우연히 조건이 참이더라도 CHECK 결과는 UNKNOWN이어야 한다.

---

## 16. ToolResultEnvelope

ToolExecutor는 Raw Python Object가 아닌 구조화된 Envelope를 반환한다.

```python
ToolResultEnvelope(
    invocation_id=...,
    tool_id=...,
    status=...,
    value=...,
    completeness=...,
    result_hash=...,
    metadata=...
)
```

예:

```text
ToolResultEnvelope

Tool
    ERP.GET_CHILD_PROJECTS

Status
    SUCCESS

Value
    List<ChildProject>

Completeness
    COMPLETE

Row Count
    3

Result Hash
    sha256:...
```

---

## 17. Tool Status와 Data Completeness 분리

Tool Status:

```text
SUCCESS
TIMEOUT
ERROR
CANCELLED
```

Data Completeness:

```text
COMPLETE
EMPTY
PARTIAL
UNKNOWN
```

두 축은 서로 분리한다.

예를 들어 Pagination 일부만 수신한 경우 Adapter 정책에 따라:

```text
status = SUCCESS
completeness = PARTIAL
```

이 가능하다.

---

## 18. `.nso result_policy` 연계

`.nso`의 READ Node는 다음 Policy를 가질 수 있다.

```json
{
  "result_policy": {
    "required": true,
    "accept_partial": false,
    "empty_is_valid": true
  }
}
```

Runtime은 Tool Result와 Policy를 결합하여 실행 여부를 결정한다.

```text
Tool Result
    │
    ▼
Result Policy
    │
    ├─ required
    ├─ accept_partial
    └─ empty_is_valid
    │
    ▼
Runtime Decision
```

예:

```text
PARTIAL
+
accept_partial = false

→ Execution Failure
```

향후 `accept_partial=true`를 허용할 경우 해당 Value는 `PARTIAL` 상태를 유지한 채 전달한다.

---

## 19. ResourceMeter

Resource 사용량은 ExecutionContext가 관리한다.

```python
ResourceMeter(
    tool_calls=0,
    loop_iterations=0,
    emitted_rows=0,
    collection_items=0,
    started_at=...,
    deadline=...
)
```

검사는 Statement 실행 전 수행한다.

```text
Before READ
    tool_calls + 1 <= limit ?

Before FOREACH iteration
    loop_iterations + 1 <= limit ?

Before EMIT
    emitted_rows + 1 <= limit ?
```

Limit 초과는 CHECK FAIL과 구분하여 `LIMIT_EXCEEDED` Execution Status로 처리한다.

---

## 20. Execution Status

권장 상태:

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

CHECK 결과와 Execution Status는 분리한다.

예:

```text
Skill Execution = COMPLETED
BUDGET_LIMIT     = FAIL
```

은 정상적인 실행이다. Skill은 문제없이 완료되었지만 업무 Rule이 실패한 것이다.

---

## 21. Emit Buffer

`emit` 결과는 즉시 외부 시스템으로 전송하지 않고 ExecutionContext의 Emit Buffer에 저장한다.

```text
Emit Buffer
 ├─ Record 1
 ├─ Record 2
 └─ Record 3
```

각 Record는 Output Schema Validation을 통과해야 한다.

---

## 22. TraceSink

Audit 전체를 ExecutionContext 내부 메모리에 보관하기보다 `TraceSink` Interface를 둔다.

```text
Runtime
   │
   └── TraceSink
          │
          ├─ MemoryTraceSink
          ├─ FileTraceSink
          └─ DatabaseTraceSink
```

Development에서는 Memory/JSON, Production에서는 PostgreSQL 등의 구현으로 교체할 수 있다.

---

## 23. Trace Event

최소 Event는 다음과 같다.

```text
EXECUTION_STARTED

STATEMENT_STARTED
STATEMENT_COMPLETED

TOOL_STARTED
TOOL_COMPLETED
TOOL_FAILED

VALUE_BOUND

CHECK_COMPLETED

EMIT_COMPLETED

LIMIT_EXCEEDED

EXECUTION_COMPLETED
EXECUTION_FAILED
```

이 Event Model은 향후 NeX-AE의 Progress/SSE와 연계할 수 있다.

---

## 24. Determinism과 Ambient State

Skill 계산결과에 다음 값이 암묵적으로 영향을 주어서는 안 된다.

```text
datetime.now()
random.random()
uuid.uuid4()
```

Runtime 자체가 Execution ID나 Duration 측정에 사용할 수는 있으나 Skill Semantic에 노출하지 않는다.

시간이 필요한 경우:

```text
context.execution_date
```

처럼 외부에서 명시적으로 주입한다.

---

## 25. Replay Snapshot

Replay를 위해 다음 Snapshot을 보관한다.

```text
ExecutionSnapshot

execution_id

nso_semantic_hash
runtime_version

input_snapshot
context_snapshot

tool_result_snapshots

check_results
emit_results

final_execution_status
```

Replay 시 실제 MCP는 호출하지 않는다.

```text
.nso
+
Input Snapshot
+
Context Snapshot
+
Tool Snapshots
        │
        ▼
New ExecutionContext
        │
        ▼
Runtime
        │
        ▼
Result
```

---

## 26. 권장 Python Model

```python
@dataclass
class ExecutionContext:
    execution_id: str

    nso_hash: str
    skill_id: str
    skill_version: str
    runtime_version: str

    inputs: Mapping[str, ValueEnvelope]
    contexts: Mapping[str, ValueEnvelope]

    frames: FrameStack

    checks: dict[str, CheckResult]
    emits: list[EmitRecord]

    resources: ResourceMeter
    trace_sink: TraceSink

    status: ExecutionStatus
```

```python
@dataclass(frozen=True)
class ValueEnvelope:
    type_info: TypeInfo
    value: object
    presence: Presence
    completeness: Completeness
    provenance_ref: str
```

`classification`은 v0.1 Production Baseline에 포함한다. `quality`와 세부 `sensitivity` Label은 향후 확장할 수 있다.

---

## 27. 핵심 설계 원칙 요약

```text
                .nso
                  │
                  ▼
         ExecutionContext
                  │
       ┌──────────┼──────────┐
       ▼          ▼          ▼
     Value     Resource     Audit
    Envelope    Meter       Trace
       │
       ├─ value
       ├─ type
       ├─ completeness
       └─ provenance
       │
       ▼
       Runtime
       │
 ┌─────┼────────────┐
 ▼     ▼            ▼
READ   CHECK        EMIT
 │      │            │
 ▼      ▼            ▼
MCP   PASS/FAIL/    Result
      UNKNOWN
```

`ExecutionContext`는 단순 Variable Store가 아니라 **신뢰 가능한 Runtime Value와 실행 상태를 관리하는 공간**으로 정의한다.

---

## 28. 다음 상세 설계와의 연결

ExecutionContext의 Completeness와 Provenance가 제대로 동작하려면 Tool 호출 결과가 단순 JSON 값이 아니라 명확한 Contract를 가져야 한다.

따라서 다음 상세 설계 대상은 **Tool Contract**가 적절하다.

Tool Contract 상세 설계에서는 최소 다음을 정의해야 한다.

- Canonical Tool ID / Version
- Capability (READ/WRITE 등)
- Input / Output Type Schema
- Result Envelope
- Completeness Semantics
- Pagination Contract
- Empty Result 의미
- Partial Result 처리
- Timeout / Error Taxonomy
- Determinism / Idempotency Metadata
- Risk Level / Security Metadata
- Contract Hash / Compatibility
- Mock Fixture Contract
- MCP Adapter Mapping

---

## 29. 최종 정의

> **ExecutionContext는 하나의 NSL Skill 실행에서 생성되는 모든 Runtime Value를 `Value + Type + Completeness + Provenance` 형태로 관리하고, Scope·CHECK·EMIT·Resource·Audit·Replay 상태를 통합적으로 유지하는 실행 상태 모델이다.**

이 모델을 기반으로 NSL Runtime은 Tool 오류와 실제 Empty Data를 구분하고, Partial Data로부터 False PASS가 발생하지 않도록 하며, 동일 `.nso`와 동일 Tool Snapshot으로 Deterministic Replay를 수행할 수 있어야 한다.
