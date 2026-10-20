# NeX Skill Runtime Class/Object Model Detailed Design v0.1

**문서 버전:** v0.1 Review Draft  
**작성일:** 2026-08-16  
**대상:** NSL v0.1 Interpreter & Runtime Environment  
**연계 설계:** `.nso` IR Schema, ExecutionContext, Tool Contract, Validation Semantics  
**구현 언어:** Python

---

# 1. 문서 목적

본 문서는 NSL v0.1 Runtime의 전체 Class/Object Model과 Python Module 간 책임 경계를 정의한다.

현재까지 설계한 다음 네 요소를 실제 구현 가능한 Python 구조로 연결하는 것이 목적이다.

```text
.nso IR
   ↓
ExecutionContext
   ↓
Tool Contract
   ↓
Validation Semantics
   ↓
────────────────────
Class / Object Model
Python Module Interface
────────────────────
   ↓
Implementation
```

NSL Runtime은 일반적인 Workflow Engine과 달리 다음 특성을 가진다.

- Runtime 실행 대상은 검증 완료된 `.nso` Typed IR이다.
- Runtime은 LLM을 포함하지 않는다.
- 모든 외부 접근은 Canonical Tool Interface를 통해 수행한다.
- 값은 단순 Python Object가 아니라 `ValueEnvelope`로 전달한다.
- Tool Failure와 Empty Data를 구조적으로 구분한다.
- `PASS / FAIL / UNKNOWN`과 Runtime Failure를 분리한다.
- Production, Mock Test, Replay가 동일 Runtime Code Path를 사용하도록 한다.

---

# 2. 핵심 설계 원칙

## 2.1 IR Object는 Passive Data로 유지

다음과 같이 IR Node가 자기 자신을 실행하는 구조는 사용하지 않는다.

```python
class ReadNode:
    async def execute(self, context):
        ...
```

대신 다음과 같이 구조를 분리한다.

```text
IR Object = Passive Data
Runtime Service = Behavior
```

예:

```text
ReadExpression
        │
        ▼
ExpressionEvaluator

ForeachStatement
        │
        ▼
StatementExecutor

CheckStatement
        │
        ▼
CheckEvaluator
```

이를 통해 Language/IR 구조와 Runtime 실행 동작의 결합도를 낮춘다.

## 2.2 RuntimeEngine은 Stateless하게 유지

`RuntimeEngine`은 현재 User, 현재 Loop, 현재 Symbol과 같은 실행 상태를 보유하지 않는다.

모든 실행 상태는 `ExecutionContext`에 격리한다.

## 2.3 Tool Layer는 ExecutionContext를 알지 않는다

Tool 계층은 전체 `ExecutionContext`를 전달받지 않는다.

Tool 호출에 필요한 최소 정보만 `ToolCallContext`로 전달한다.

## 2.4 Live / Mock / Replay는 동일 Port를 구현

```text
              동일 RuntimeEngine

               ToolExecutionPort
                 /      |      \
                /       |       \
             Live      Mock     Replay
```

Production 실행, 자동화 테스트, Deterministic Replay가 동일 Runtime Code Path를 사용하도록 한다.

---

# 3. Runtime 전체 Object Model

```text
                         SkillObject (.nso)
                               │
                               ▼
                       RuntimePreflight
                               │
                               ▼
                         RuntimeEngine
                               │
                 ┌─────────────┼──────────────┐
                 ▼             ▼              ▼
        ExecutionContext  StatementExecutor  ResourceGuard
                 │             │
       ┌─────────┼──────┐      ▼
       │         │      │  ExpressionEvaluator
       ▼         ▼      ▼      │
   FrameStack  Checks  Emits    ├── BuiltinRegistry
       │                        │
       │                        └── ToolExecutionPort
       ▼                                  │
 ValueEnvelope                            ▼
       │                          CanonicalToolExecutor
       ├─ value                            │
       ├─ TypeInfo                  ┌──────┼───────┐
       ├─ Presence                  ▼      ▼       ▼
       ├─ Completeness          Resolver Contract Provider
       └─ Provenance                │            Invoker
                                    │              │
                                    ▼              ▼
                              Tool Contract       MCP

                CheckEvaluator
                     │
                     ▼
                 CheckResult
                     │
                     ▼
             ValidationSummary

Runtime 전체
   │
   ├── TraceSink
   ├── ProvenanceStore
   └── ReplayRecorder
```

---

# 4. Class 분류

Runtime Class는 다음 세 종류로 구분한다.

## 4.1 Immutable Value Object

실행 중 전달되는 값과 Contract를 표현한다.

```text
SkillObject
TypeInfo
ValueEnvelope
Money
ToolContract
ToolBinding
ToolResultEnvelope
ToolError
CheckResult
PredicateEvaluation
EmitRecord
```

가능하면 다음 형태를 사용한다.

```python
@dataclass(frozen=True, slots=True)
```

## 4.2 Per-Execution Mutable State

Skill 실행마다 새로 생성된다.

```text
ExecutionContext
FrameStack
Frame
ResourceMeter
EmitBuffer
CheckStore
```

## 4.3 Stateless / Reusable Service

여러 Execution에서 재사용한다.

```text
RuntimeEngine
StatementExecutor
ExpressionEvaluator
CheckEvaluator
BuiltinRegistry
RuntimePreflightValidator
NsoLoader
```

이 분리는 동시 실행, Test Isolation, Runtime 재사용성을 높인다.

---

# 5. RuntimeEngine

Runtime의 최상위 Coordinator이다.

```python
class RuntimeEngine:

    async def execute(
        self,
        skill: SkillObject,
        request: ExecutionRequest,
        ports: RuntimePorts,
    ) -> ExecutionResult:
        ...
```

RuntimeEngine의 책임은 다음으로 제한한다.

```text
Preflight Validation
        ↓
ExecutionContext 생성
        ↓
EXECUTION_STARTED
        ↓
StatementExecutor 실행
        ↓
Validation Summary 생성
        ↓
ExecutionResult 생성
        ↓
EXECUTION_COMPLETED
```

RuntimeEngine이 직접 Tool 호출, Expression 계산, CHECK 판단을 수행하지 않는다.

권장 생성자 구조:

```python
class RuntimeEngine:

    def __init__(
        self,
        statement_executor: StatementExecutor,
        preflight: RuntimePreflightValidator,
    ):
        ...

    async def execute(
        self,
        skill: SkillObject,
        request: ExecutionRequest,
        ports: RuntimePorts,
    ) -> ExecutionResult:
        ...
```

---

# 6. ExecutionRequest

NeX-AE와 Runtime 사이의 입력 Contract이다.

```python
@dataclass(frozen=True)
class ExecutionRequest:

    execution_id: str

    inputs: Mapping[str, object]

    runtime_context: RuntimeInputContext

    options: ExecutionOptions
```

예:

```text
inputs

    year = 2026
```

Runtime Context 예:

```text
user.id
user.team_id
organization.id
session.id
```

Runtime은 `.nso contexts`에 선언된 Context Path만 사용할 수 있다.

---

# 7. ExecutionContext

ExecutionContext는 한 번의 Skill 실행 상태를 관리하는 핵심 객체이다.

```python
class ExecutionContext:

    identity: ExecutionIdentity

    inputs: Mapping[str, ValueEnvelope]

    contexts: Mapping[str, ValueEnvelope]

    frames: FrameStack

    checks: CheckStore

    emits: EmitBuffer

    resources: ResourceMeter

    status: ExecutionStatus
```

중요한 원칙:

> `ExecutionContext`가 Runtime Service Locator가 되어서는 안 된다.

다음 구조는 지양한다.

```python
context.tool_executor
context.check_evaluator
context.trace_sink
```

ExecutionContext에는 실행 상태만 들어가고 Runtime Service는 별도로 주입한다.

---

# 8. Frame / FrameStack

Variable Scope는 Frame Stack으로 관리한다.

```python
class Frame:

    def bind(
        self,
        symbol_id: str,
        value: ValueEnvelope,
    ) -> None:
        ...

    def resolve(
        self,
        symbol_id: str,
    ) -> ValueEnvelope:
        ...
```

재할당은 금지한다.

```python
if symbol_id in self._values:
    raise ImmutableBindingError(...)
```

FrameStack 개념:

```text
Skill Frame

    ↓ push

Loop Frame #1

    ↓ pop

Loop Frame #2
```

`foreach` 실행마다 Loop Frame을 생성한다.

---

# 9. ValueEnvelope 위치

`ValueEnvelope`은 Runtime 전용 객체가 아니라 다음 계층에서 공통으로 사용한다.

```text
Runtime
Tool
Validation
Replay
```

따라서 다음 위치를 권장한다.

```text
nsl/core/values.py
```

```python
@dataclass(frozen=True, slots=True)
class ValueEnvelope:

    value: object

    type_info: TypeInfo

    presence: Presence

    completeness: Completeness

    provenance_ref: str
```

`TypeInfo`도 다음 위치에 둔다.

```text
nsl/core/types.py
```

---

# 10. StatementExecutor

`.nso.body`를 순차적으로 실행한다.

```python
class StatementExecutor:

    async def execute(
        self,
        statement: Statement,
        ctx: ExecutionContext,
        services: RuntimeServices,
    ) -> None:
        ...
```

v0.1 지원 Statement:

```text
LET
FOREACH
CHECK
EMIT
```

개념적 구현:

```python
match statement:

    case LetStatement():
        ...

    case ForeachStatement():
        ...

    case CheckStatement():
        ...

    case EmitStatement():
        ...
```

v0.1에서는 별도의 복잡한 Visitor Framework를 도입하지 않는다.

---

# 11. ExpressionEvaluator

`READ`가 I/O를 포함하므로 Expression Evaluator는 async Interface를 사용하는 것이 단순하다.

```python
class ExpressionEvaluator:

    async def evaluate(
        self,
        expression: Expression,
        ctx: ExecutionContext,
        services: RuntimeServices,
    ) -> ValueEnvelope:
        ...
```

지원 Expression:

```text
Literal
SymbolRef
Field
Project
Binary
Builtin Call
READ
```

Pure Expression은 동기 계산이지만 전체 Interface를 async로 통일한다.

예:

```nsl
let parents =
    read PROJECT.LIST_PARENT_PROJECTS(...);
```

---

# 12. BuiltinRegistry

Builtin을 Runtime 코드에 분산 구현하지 않는다.

```python
class BuiltinRegistry:

    def resolve(
        self,
        name: str,
        version: str,
    ) -> BuiltinFunction:
        ...
```

v0.1 Builtin:

```text
sum
count
min
max
```

각 Builtin은 다음을 일관되게 처리해야 한다.

```text
Value 계산
+
Presence 전파
+
Completeness 전파
+
Provenance 생성
```

특히 `sum()`의 Empty Semantics를 Builtin 내부에 중앙화한다.

---

# 13. CompletenessAlgebra

Expression 구현마다 Completeness Rule을 반복하지 않도록 별도 객체를 둘 수 있다.

```python
class CompletenessAlgebra:

    def combine(
        self,
        *values: ValueEnvelope,
    ) -> Completeness:
        ...
```

기본 규칙:

```text
COMPLETE + COMPLETE
→ COMPLETE

COMPLETE + PARTIAL
→ PARTIAL

anything + UNKNOWN
→ UNKNOWN
```

향후 v0.2 Semantics 변경에도 유리하다.

---

# 14. Tool Runtime Interface

Runtime이 알아야 하는 Tool Interface는 최소화한다.

```python
class ToolExecutionPort(Protocol):

    async def execute(
        self,
        request: ToolCallRequest,
    ) -> ToolExecutionOutcome:
        ...
```

Runtime은 다음 구현을 알지 않는다.

```text
MCP
SAP
Oracle
Legacy ERP
```

Runtime은 오직 다음만 사용한다.

```text
Canonical Tool ID
Canonical Input
Canonical Outcome
```

---

# 15. Tool 영역 Class Model

```text
ToolExecutionPort
       ▲
       │
CanonicalToolExecutor
       │
 ┌─────┼───────────┐
 ▼     ▼           ▼
Resolver Contract  ProviderInvoker
 │     Validator       │
 │                     ├─ MCPProviderInvoker
 │                     ├─ MockProviderInvoker
 │                     └─ ReplayProviderInvoker
 ▼
ResolvedTool
 ├─ ToolContract
 └─ ToolBinding
```

`ProviderInvoker`는 실제 외부 호출만 담당한다.

`CanonicalToolExecutor`는 Contract, Mapping, Result Semantics를 담당한다.

---

# 16. ToolResolver

```python
class ToolResolver(Protocol):

    def resolve(
        self,
        tool_ref: RequiredTool,
        tenant_context: TenantContext,
    ) -> ResolvedTool:
        ...
```

반환 객체:

```python
@dataclass(frozen=True)
class ResolvedTool:

    contract: ToolContract

    binding: ToolBinding
```

Runtime 실행 전 `.nso contract_hash`와 현재 Contract를 검증한다.

---

# 17. ProviderInvoker

```python
class ProviderInvoker(Protocol):

    async def invoke(
        self,
        binding: ToolBinding,
        payload: Mapping[str, object],
        call_context: ToolCallContext,
    ) -> ProviderOutcome:
        ...
```

구현체:

```text
MCPProviderInvoker
MockProviderInvoker
ReplayProviderInvoker
```

향후 필요하면 HTTP Provider를 Adapter 내부에서 추가할 수 있다.

단, NSL Runtime 자체는 임의 HTTP를 호출하지 않는다.

---

# 18. Replay 설계

Replay를 Runtime의 별도 실행모드로 구현하지 않고 Tool Adapter 교체로 구현하는 것을 권장한다.

실행:

```text
RuntimeEngine
    ↓
CanonicalToolExecutor
    ↓
MCPProviderInvoker
```

Replay:

```text
RuntimeEngine
    ↓
CanonicalToolExecutor
    ↓
ReplayProviderInvoker
```

또는 다음 구조도 가능하다.

```text
ToolExecutionPort
    ├─ LiveToolExecutor
    ├─ MockToolExecutor
    └─ ReplayToolExecutor
```

중요한 점은 RuntimeEngine 코드가 변경되지 않는다는 것이다.

---

# 19. CheckEvaluator

```python
class CheckEvaluator:

    def evaluate(
        self,
        spec: CheckStatement,
        predicate: ValueEnvelope,
    ) -> CheckResult:
        ...
```

기본 Semantics:

```text
Bool TRUE + COMPLETE
→ PASS

Bool FALSE + COMPLETE
→ FAIL

PARTIAL
→ UNKNOWN

UNKNOWN
→ UNKNOWN
```

`StatementExecutor`는 Validation 정책을 직접 판단하지 않는다.

---

# 20. PredicateEvaluation

다음 두 접근이 가능하다.

```text
A.
ValueEnvelope[Bool]
    ↓
CheckEvaluator
```

```text
B.
ValueEnvelope[Bool]
    ↓
PredicateEvaluation
    ↓
CheckEvaluator
```

B를 권장한다.

```python
@dataclass(frozen=True)
class PredicateEvaluation:

    truth: bool

    completeness: Completeness

    presence: Presence

    provenance_refs: tuple[str, ...]
```

향후 다음 Validator를 동일 추상화에 연결하기 쉽다.

```text
Rule Predicate
SMT Predicate
Graph Constraint
```

---

# 21. Validation 확장 Interface

v0.1:

```text
CheckEvaluator
   ↓
Rule Semantics
```

향후:

```text
ValidationEngine
       │
       ├─ RuleValidator
       ├─ SMTValidator
       └─ GraphValidator
```

확장 Interface 후보:

```python
class ConstraintValidator(Protocol):

    async def validate(
        self,
        request: ValidationRequest,
    ) -> ValidationOutcome:
        ...
```

v0.1에서 구현하지 않더라도 Interface 위치는 고려한다.

---

# 22. EmitBuffer

```python
class EmitBuffer:

    def append(
        self,
        record: EmitRecord,
    ) -> None:
        ...
```

Append 전에 다음을 검증한다.

```text
Output Schema Validation
emitted_rows Limit
Value Type
Money Currency
```

`emit`은 외부 시스템으로 직접 전송하지 않는다.

```text
EMIT
  ↓
ExecutionContext EmitBuffer
  ↓
ExecutionResult
  ↓
NeX-AE
```

---

# 23. ResourceMeter / ResourceGuard

실행상태와 제한판단을 분리한다.

```python
class ResourceMeter:

    tool_calls: int

    loop_iterations: int

    emitted_rows: int
```

```python
class ResourceGuard:

    def before_tool_call(...): ...

    def before_loop_iteration(...): ...

    def before_emit(...): ...

    def check_deadline(...): ...
```

이 구조가 Unit Test와 Boundary Test에 유리하다.

---

# 24. RuntimeClock

Runtime 내부에서 `datetime.now()`를 직접 분산 호출하지 않는다.

```python
class RuntimeClock(Protocol):

    def monotonic(self) -> float:
        ...
```

구현:

```text
SystemClock
FakeClock
```

이를 통해 Timeout Boundary Test를 결정론적으로 수행할 수 있다.

Skill 자체에는 현재 시각을 직접 노출하지 않는다.

필요한 경우:

```text
context.execution_date
```

처럼 Runtime Context로 명시적으로 주입한다.

---

# 25. CancellationToken

NeX-AE Job Queue와 Runtime Core를 직접 결합하지 않는다.

```python
class CancellationToken(Protocol):

    def is_cancelled(self) -> bool:
        ...
```

Production:

```text
JobCancellationToken
→ PostgreSQL cancel_requested 확인
```

Test:

```text
FakeCancellationToken
```

Runtime Safe Point:

```text
Statement 시작 전
Tool Call 전
Loop Iteration 전
```

---

# 26. TraceSink

```python
class TraceSink(Protocol):

    def emit(
        self,
        event: TraceEvent,
    ) -> None:
        ...
```

구현 후보:

```text
NullTraceSink
MemoryTraceSink
JsonTraceSink
PostgresTraceSink
```

Runtime은 PostgreSQL 구현을 직접 알지 않는다.

---

# 27. ProvenanceStore

```python
class ProvenanceStore(Protocol):

    def record(
        self,
        record: ProvenanceRecord,
    ) -> str:
        ...
```

반환된 `provenance_ref`만 `ValueEnvelope`이 보유한다.

큰 Lineage Graph를 Value마다 복사하지 않는다.

---

# 28. RuntimeServices / RuntimePorts

내부 Service를 하나의 Bundle로 전달할 수 있다.

```python
@dataclass(frozen=True)
class RuntimeServices:

    tools: ToolExecutionPort

    builtins: BuiltinRegistry

    checks: CheckEvaluator

    resources: ResourceGuard

    trace: TraceSink

    provenance: ProvenanceStore

    clock: RuntimeClock
```

다만 이를 동적인 Service Locator로 사용하지 않는다.

외부 Port만 분리하는 다음 구조를 더 권장한다.

```python
@dataclass(frozen=True)
class RuntimePorts:

    tools: ToolExecutionPort

    trace: TraceSink

    provenance: ProvenanceStore

    clock: RuntimeClock

    cancellation: CancellationToken
```

`ExpressionEvaluator`, `CheckEvaluator`, `BuiltinRegistry` 같은 내부 Service는 RuntimeEngine 생성자에서 명시적으로 주입한다.

---

# 29. RuntimePreflightValidator

실행 전에 검출 가능한 오류는 Runtime 시작 전에 확인한다.

```text
IR Version
Language Version
Semantics Profile
Required Feature
Symbol Integrity
Node Integrity
Tool Contract Hash
Risk / Capability
Resource Bound
```

Interface:

```python
class RuntimePreflightValidator:

    def validate(
        self,
        skill: SkillObject,
        tool_catalog: ToolContractCatalog,
    ) -> PreflightResult:
        ...
```

Preflight를 통과해야 ExecutionContext를 생성한다.

---

# 30. ExecutionResult

Runtime 최종 결과는 구조화된 객체로 반환한다.

```python
@dataclass(frozen=True)
class ExecutionResult:

    execution_id: str

    skill_id: str

    skill_version: str

    semantic_hash: str

    status: ExecutionStatus

    validation: ValidationSummary

    outputs: tuple[EmitRecord, ...]

    resources: ResourceUsage

    error: RuntimeErrorInfo | None
```

NeX-AE는 이 결과를 받아 자연어 설명을 생성한다.

Runtime은 자연어 보고서를 만들지 않는다.

---

# 31. ToolCallContext

Tool Layer가 전체 ExecutionContext에 의존하지 않도록 별도 객체를 정의한다.

```python
@dataclass(frozen=True)
class ToolCallContext:

    execution_id: str

    invocation_id: str

    deadline: float | None

    tenant_id: str | None

    user_id: str | None
```

Tool 계층은 이 최소 Context만 사용한다.

---

# 32. Python Module 구조 권장안

```text
nsl/
│
├─ core/
│  ├─ ids.py
│  ├─ types.py
│  ├─ money.py
│  ├─ values.py
│  ├─ completeness.py
│  ├─ states.py
│  └─ errors.py
│
├─ language/
│  ├─ tokens.py
│  ├─ lexer.py
│  ├─ parser.py
│  └─ ast.py
│
├─ semantic/
│  ├─ symbols.py
│  ├─ type_checker.py
│  ├─ safety_checker.py
│  └─ resource_analyzer.py
│
├─ ir/
│  ├─ types.py
│  ├─ expressions.py
│  ├─ statements.py
│  ├─ model.py
│  ├─ generator.py
│  ├─ loader.py
│  ├─ validator.py
│  ├─ serializer.py
│  ├─ canonical.py
│  └─ hashing.py
│
├─ runtime/
│  ├─ engine.py
│  ├─ request.py
│  ├─ result.py
│  ├─ preflight.py
│  ├─ context.py
│  ├─ frame.py
│  ├─ statements.py
│  ├─ expressions.py
│  ├─ builtins.py
│  ├─ resources.py
│  ├─ cancellation.py
│  └─ ports.py
│
├─ tools/
│  ├─ contracts.py
│  ├─ bindings.py
│  ├─ registry.py
│  ├─ resolver.py
│  ├─ requests.py
│  ├─ outcomes.py
│  ├─ executor.py
│  └─ providers/
│      ├─ mcp.py
│      ├─ mock.py
│      └─ replay.py
│
├─ validation/
│  ├─ predicate.py
│  ├─ check.py
│  ├─ semantics.py
│  ├─ summary.py
│  └─ ports.py
│
├─ audit/
│  ├─ events.py
│  ├─ trace.py
│  └─ provenance.py
│
├─ replay/
│  ├─ snapshot.py
│  ├─ recorder.py
│  └─ runner.py
│
├─ package/
│  ├─ manifest.py
│  ├─ builder.py
│  ├─ loader.py
│  └─ verifier.py
│
└─ cli/
   └─ main.py
```

---

# 33. Module Dependency Rule

의존성 방향을 처음부터 제한한다.

```text
core
    아무 NeX Runtime Module에도 의존하지 않음

ir
    → core

tools
    → core

validation
    → core
    → 최소한의 IR Model

runtime
    → core
    → ir
    → tool protocol
    → validation
    → audit protocol

MCP adapter
    → tools
    → 외부 MCP Library

NeX-AE
    → runtime public API
```

중요한 금지사항:

> `tools`가 `runtime.ExecutionContext`에 의존해서는 안 된다.

---

# 34. Object Lifetime

## Application Lifetime

```text
RuntimeEngine
BuiltinRegistry
NsoLoader
ToolRegistry
ToolContractCatalog
```

## Execution Lifetime

```text
ExecutionContext
ResourceMeter
FrameStack
EmitBuffer
CheckStore
Trace Context
```

## Tool Call Lifetime

```text
ToolCallRequest
ToolCallContext
ProviderOutcome
ToolExecutionOutcome
```

이 구조를 통해 Shared Mutable State를 최소화한다.

---

# 35. RuntimeEngine 재사용성

RuntimeEngine 내부에 다음 상태를 보유하지 않는다.

```text
현재 실행 Symbol
현재 User
현재 Loop
현재 Skill
```

따라서 다음과 같은 재사용이 가능해야 한다.

```python
engine = RuntimeEngine(...)

await engine.execute(skill_a, request_a, ports_a)
await engine.execute(skill_b, request_b, ports_b)
```

또는 여러 Worker에서 독립적으로 실행할 수 있어야 한다.

---

# 36. v0.1 Skill 내부 Execution Ordering

ToolExecutor Interface가 async이더라도 한 Skill 내부 Statement는 순차적으로 실행한다.

```text
READ A
   ↓ await
READ B
   ↓ await
CHECK
```

v0.1에서는 Skill 내부 Parallel Tool Execution을 지원하지 않는다.

다만 여러 Skill Execution 간 동시성은 허용한다.

이를 통해 Replay와 Audit을 단순화한다.

---

# 37. Runtime Class 관계 최종 요약

```text
                 RuntimeEngine
                      │
          ┌───────────┼────────────┐
          ▼           ▼            ▼
      Preflight  ExecutionContext StatementExecutor
                                      │
                                      ▼
                              ExpressionEvaluator
                               │             │
                               ▼             ▼
                         BuiltinRegistry  ToolPort

                    StatementExecutor
                           │
                           ▼
                     CheckEvaluator
                           │
                           ▼
                      CheckResult

ExecutionContext
   │
   ├─ FrameStack
   ├─ CheckStore
   ├─ EmitBuffer
   └─ ResourceMeter

Runtime Ports
   │
   ├─ ToolExecutionPort
   ├─ TraceSink
   ├─ ProvenanceStore
   ├─ RuntimeClock
   └─ CancellationToken
```

---

# 38. 핵심 Architecture Decision

| 결정 | 이유 |
|---|---|
| IR Object는 Passive Data | Runtime과 Language 구조 분리 |
| ValueEnvelope을 `core`에 배치 | Tool/Runtime/Validation 공통 사용 |
| RuntimeEngine은 Stateless | 병렬 실행과 테스트 용이 |
| Tool Layer는 ExecutionContext를 모름 | Layer Dependency 유지 |
| Live / Mock / Replay가 동일 Tool Port 구현 | 동일 Runtime으로 개발·시험·재현 |
| Skill 내부 실행은 Sequential | Determinism, Audit, Replay 단순화 |
| 외부 Port는 Protocol로 추상화 | MCP/Mock/Replay 교체 용이 |
| Execution State와 Runtime Service 분리 | 테스트 및 동시 실행 안정성 |

---

# 39. Replay 설계의 핵심 의미

Replay를 Runtime의 특별한 분기 로직으로 만들지 않는다.

```text
              동일 RuntimeEngine

               ToolExecutionPort
                 /      |      \
                /       |       \
             Live      Mock     Replay
```

이 구조의 장점:

- Production과 Test가 동일 Runtime을 사용한다.
- Replay 전용 Runtime 코드가 필요하지 않다.
- Regression Test의 신뢰성이 높아진다.
- Tool 결과 Snapshot만 교체하여 동일 Skill을 재실행할 수 있다.

---

# 40. 다음 상세 설계 대상

본 Class/Object Model을 기준으로 다음 단계에서는 각 Python Interface/Protocol의 실제 Method Signature와 주요 Dataclass 정의를 구체화한다.

우선 대상:

```text
RuntimeEngine
ExecutionRequest
ExecutionResult
ExecutionContext
Frame / FrameStack
ValueEnvelope
StatementExecutor
ExpressionEvaluator
BuiltinRegistry
ToolExecutionPort
ToolResolver
ProviderInvoker
CheckEvaluator
TraceSink
ProvenanceStore
RuntimeClock
CancellationToken
```

이 수준까지 확정되면 이후 단계는 실제 Python Skeleton Code 구현으로 전환할 수 있다.

---

# 41. 최종 정의

NSL Runtime의 Class/Object Model은 다음 원칙을 지향한다.

> **IR은 실행 의미를 담은 Passive Data이며, Runtime Service가 이를 실행하고, 모든 per-execution 상태는 ExecutionContext에 격리한다.**

> **외부 시스템, Audit, Clock, Cancellation은 Port로 추상화하고, Live / Mock / Replay가 동일 Runtime Code Path를 사용하도록 한다.**

이를 통해 NSL Runtime v1.0은 Deterministic Execution, Testability, Replayability, Extensibility를 동시에 확보한다.
