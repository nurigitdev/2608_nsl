# NeX Skill Runtime Python Interface / Protocol Detailed Design v0.1

**문서 버전:** v0.1  
**상태:** Detailed Design Baseline  
**대상:** NSL v0.1 Interpreter & Runtime Environment  
**구현 언어:** Python 3.11+  
**작성 목적:** NSL Runtime 구현 직전 단계의 Python Interface, Protocol, Dataclass 및 Module Dependency 상세 설계 확정

---

# 1. 설계 목적

본 문서는 NSL Runtime 전체 Class/Object Model 설계를 기반으로 실제 Python 구현에 사용할 Interface, Protocol, Method Signature, 핵심 Dataclass 및 Module Dependency를 구체화한다.

핵심 원칙은 다음과 같다.

```text
IR Object = Passive Data
Runtime Service = Behavior
Execution State = ExecutionContext
External Dependency = Runtime Port
```

또한 다음 원칙을 유지한다.

```text
1. IR Object가 스스로 execute()하지 않는다.
2. RuntimeEngine은 Stateless에 가깝게 유지한다.
3. 실행별 Mutable State는 ExecutionContext에만 존재한다.
4. Tool Layer는 ExecutionContext를 직접 알지 않는다.
5. Live / Mock / Replay는 동일 Runtime Code Path를 사용한다.
6. eval(), exec()를 사용하지 않는다.
7. Core Runtime은 FastAPI, SQLAlchemy, MCP SDK 등에 종속되지 않는다.
```

---

# 2. Python 구현 기준

권장 기준:

```text
Python 3.11+
dataclasses
typing.Protocol
Enum
Decimal
async / await
```

Core Runtime에서는 외부 Dependency를 최소화한다. FastAPI, SQLAlchemy, PostgreSQL Driver, MCP SDK, HTTP Client는 Adapter Layer에서만 사용한다.

---

# 3. ID Type

파일: `nsl/core/ids.py`

```python
from typing import NewType

ExecutionId = NewType("ExecutionId", str)
SkillId = NewType("SkillId", str)
SymbolId = NewType("SymbolId", str)
NodeId = NewType("NodeId", str)
ToolId = NewType("ToolId", str)
ToolRef = NewType("ToolRef", str)
ToolInvocationId = NewType("ToolInvocationId", str)
ProvenanceRef = NewType("ProvenanceRef", str)
```

String을 직접 사용하는 것보다 ID 종류별 혼용 오류를 줄일 수 있다.

---

# 4. Runtime 상태 Enum

파일: `nsl/core/states.py`

```python
from enum import Enum

class Presence(str, Enum):
    PRESENT = "PRESENT"
    EMPTY = "EMPTY"

class Completeness(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"

class ExecutionStatus(str, Enum):
    CREATED = "CREATED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TOOL_ERROR = "TOOL_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    LIMIT_EXCEEDED = "LIMIT_EXCEEDED"
    CANCELLED = "CANCELLED"

class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"

class CheckSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
```

중요 원칙: `ExecutionStatus != CheckStatus`.

---

# 5. TypeInfo

파일: `nsl/core/types.py`

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import TypeAlias

@dataclass(frozen=True, slots=True)
class PrimitiveType:
    name: str

@dataclass(frozen=True, slots=True)
class DomainType:
    name: str
    base: str

@dataclass(frozen=True, slots=True)
class MoneyType:
    currency: str | None

@dataclass(frozen=True, slots=True)
class ListType:
    item_type: "TypeInfo"

@dataclass(frozen=True, slots=True)
class NamedType:
    name: str

@dataclass(frozen=True, slots=True)
class EnumType:
    name: str
    values: tuple[str, ...]

TypeInfo: TypeAlias = (
    PrimitiveType | DomainType | MoneyType |
    ListType | NamedType | EnumType
)
```

---

# 6. Money

파일: `nsl/core/money.py`

```python
from dataclasses import dataclass
from decimal import Decimal

@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self) -> None:
        if not self.currency:
            raise ValueError("currency is required")
```

금액 계산은 Binary Floating Point를 사용하지 않는다.

---

# 7. ValueEnvelope

파일: `nsl/core/values.py`

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class ValueEnvelope:
    value: object
    type_info: TypeInfo
    presence: Presence
    completeness: Completeness
    provenance_ref: ProvenanceRef
```

Runtime 내부의 업무 값은 가능한 한 `Value + Type + Presence + Completeness + Provenance`로 전달한다.

---

# 8. Runtime Error Model

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Mapping

class ErrorCategory(str, Enum):
    SYNTAX = "SYNTAX"
    SEMANTIC = "SEMANTIC"
    TYPE = "TYPE"
    TOOL = "TOOL"
    SAFETY = "SAFETY"
    RESOURCE = "RESOURCE"
    RUNTIME = "RUNTIME"

@dataclass(frozen=True)
class RuntimeErrorInfo:
    code: str
    category: ErrorCategory
    message: str
    node_id: NodeId | None = None
    retryable: bool = False
    details: Mapping[str, object] = field(default_factory=dict)

class NslRuntimeError(Exception):
    pass
```

내부 Exception과 외부 Result를 분리하며 RuntimeEngine Boundary에서 구조화한다.

---

# 9. IR Object 원칙

IR Object는 Passive Dataclass로 구현한다.

```python
@dataclass(frozen=True, slots=True)
class SymbolRefExpression:
    node_id: NodeId
    symbol_id: SymbolId
    type_info: TypeInfo

@dataclass(frozen=True, slots=True)
class BinaryExpression:
    node_id: NodeId
    operator: str
    left: "Expression"
    right: "Expression"
    type_info: TypeInfo
```

IR Object에 `execute()` 같은 Behavior를 넣지 않는다.

---

# 10. Statement Model

```python
@dataclass(frozen=True, slots=True)
class LetStatement:
    node_id: NodeId
    target_symbol_id: SymbolId
    value: "Expression"

@dataclass(frozen=True, slots=True)
class ForeachStatement:
    node_id: NodeId
    iterator_symbol_id: SymbolId
    collection: "Expression"
    max_iterations: int
    body: tuple["Statement", ...]

@dataclass(frozen=True, slots=True)
class CheckStatement:
    node_id: NodeId
    check_id: str
    condition: "Expression"
    severity: CheckSeverity
    on_fail: str
    message: str | None
    result_symbol_id: SymbolId
    data_policy: "CheckDataPolicy"

@dataclass(frozen=True, slots=True)
class EmitField:
    name: str
    value: "Expression"

@dataclass(frozen=True, slots=True)
class EmitStatement:
    node_id: NodeId
    fields: tuple[EmitField, ...]
```

---

# 11. SkillObject

`.nso` Loading 결과를 나타낸다.

```python
@dataclass(frozen=True, slots=True)
class SkillObject:
    format: str
    ir_version: str
    language_version: str
    skill_id: SkillId
    skill_version: str
    semantic_hash: str
    semantics_profile: str
    features: frozenset[str]
    required_tools: tuple["RequiredTool", ...]
    limits: "ResourceLimits"
    inputs: tuple["InputSpec", ...]
    contexts: tuple["ContextSpec", ...]
    output_schema: "OutputSchema"
    body: tuple["Statement", ...]
    analysis: "StaticAnalysis"
```

Runtime은 JSON Dictionary를 직접 실행하지 않는다.

---

# 12. NsoLoader

파일: `nsl/ir/loader.py`

```python
from pathlib import Path

class NsoLoader:
    def load_path(self, path: Path) -> SkillObject:
        ...

    def load_bytes(self, data: bytes) -> SkillObject:
        ...

    def load_text(self, text: str) -> SkillObject:
        ...
```

처리 흐름: `JSON Parse → Schema Validation → IR Validation → Python IR Object`.

---

# 13. ExecutionRequest

파일: `nsl/runtime/request.py`

```python
@dataclass(frozen=True)
class ExecutionPrincipal:
    tenant_id: str
    subject_id: str
    actor_type: str
    roles: frozenset[str]
    scopes: frozenset[str]
    auth_context_ref: str
    on_behalf_of: str | None = None

@dataclass(frozen=True)
class DataHandlingPolicy:
    max_trace_classification: str = "INTERNAL"
    snapshot_retention_days: int = 30

@dataclass(frozen=True)
class ExecutionRequest:
    execution_id: ExecutionId
    inputs: Mapping[str, object]
    runtime_context: "RuntimeInputContext"
    principal: ExecutionPrincipal
    data_policy: DataHandlingPolicy
    options: "ExecutionOptions"

@dataclass(frozen=True)
class ExecutionOptions:
    trace_enabled: bool = True
    replay_mode: bool = False
```

---

# 14. RuntimeInputContext

```python
@dataclass(frozen=True)
class RuntimeInputContext:
    user: Mapping[str, object]
    organization: Mapping[str, object]
    session: Mapping[str, object]
    request: Mapping[str, object]
```

NSL Source는 전체 Runtime Context에 임의 접근하지 않고 `.nso contexts`에 선언된 Path만 Binding한다.
Tenant와 실행 주체 정보는 임의 Context 값이 아니라 검증된 `ExecutionPrincipal`에서 전달한다.

---

# 15. Frame

파일: `nsl/runtime/frame.py`

```python
class Frame:
    def __init__(self) -> None:
        self._values: dict[SymbolId, ValueEnvelope] = {}

    def bind(self, symbol_id: SymbolId, value: ValueEnvelope) -> None:
        if symbol_id in self._values:
            raise ImmutableBindingError(symbol_id)
        self._values[symbol_id] = value

    def resolve(self, symbol_id: SymbolId) -> ValueEnvelope:
        return self._values[symbol_id]

    def contains(self, symbol_id: SymbolId) -> bool:
        return symbol_id in self._values
```

Frame은 Write-once Binding Semantics를 제공한다.

---

# 16. FrameStack

```python
class FrameStack:
    def __init__(self) -> None:
        self._frames: list[Frame] = []

    def push(self, frame: Frame) -> None:
        ...

    def pop(self) -> Frame:
        ...

    def resolve(self, symbol_id: SymbolId) -> ValueEnvelope:
        for frame in reversed(self._frames):
            if frame.contains(symbol_id):
                return frame.resolve(symbol_id)
        raise UnknownSymbolError(symbol_id)
```

---

# 17. ExecutionContext

```python
@dataclass
class ExecutionContext:
    execution_id: ExecutionId
    skill_id: SkillId
    skill_version: str
    nso_semantic_hash: str
    runtime_version: str
    inputs: Mapping[SymbolId, ValueEnvelope]
    contexts: Mapping[SymbolId, ValueEnvelope]
    frames: FrameStack
    checks: "CheckStore"
    emits: "EmitBuffer"
    resources: "ResourceMeter"
    status: ExecutionStatus
```

ExecutionContext에는 ToolExecutor, CheckEvaluator, TraceSink, Database, MCP Client를 넣지 않는다.

---

# 18. Runtime External Ports

```python
from typing import Protocol

class ToolExecutionPort(Protocol):
    async def execute(self, request: "ToolCallRequest") -> "ToolExecutionOutcome":
        ...

class TraceSink(Protocol):
    def emit(self, event: "TraceEvent") -> None:
        ...

class ProvenanceStore(Protocol):
    def record(self, record: "ProvenanceRecord") -> ProvenanceRef:
        ...

class RuntimeClock(Protocol):
    def monotonic(self) -> float:
        ...

class CancellationToken(Protocol):
    def is_cancelled(self) -> bool:
        ...
```

---

# 19. RuntimePorts

```python
@dataclass(frozen=True)
class RuntimePorts:
    tools: ToolExecutionPort
    trace: TraceSink
    provenance: ProvenanceStore
    clock: RuntimeClock
    cancellation: CancellationToken
```

Runtime과 Infrastructure 사이의 명시적 경계로 사용한다.

---

# 20. ToolCallRequest

```python
@dataclass(frozen=True)
class ToolCallRequest:
    execution_id: ExecutionId
    invocation_id: ToolInvocationId
    node_id: NodeId
    tool_ref: ToolRef
    arguments: Mapping[str, ValueEnvelope]
    call_context: "ToolCallContext"
```

Tool Layer에 전체 ExecutionContext를 넘기지 않는다.

---

# 21. ToolCallContext

```python
@dataclass(frozen=True)
class ToolCallContext:
    tenant_id: str
    subject_id: str
    actor_type: str
    granted_scopes: frozenset[str]
    authorization_decision_ref: str
    deadline_monotonic: float | None
```

OAuth Credential 원문은 포함하지 않는다. Policy Decision과 Delegation Scope는 검증된 Reference와 Scope로 전달한다.

---

# 22. ToolExecutionOutcome

```python
@dataclass(frozen=True)
class ToolSuccess:
    result: "ToolResultEnvelope"

@dataclass(frozen=True)
class ToolFailure:
    error: "ToolError"

from typing import TypeAlias
ToolExecutionOutcome: TypeAlias = ToolSuccess | ToolFailure
```

ToolFailure에는 value가 존재하지 않는다.

---

# 23. ToolResultEnvelope

```python
@dataclass(frozen=True)
class ToolResultEnvelope:
    invocation_id: ToolInvocationId
    tool_id: ToolId
    tool_version: str
    value: object
    type_info: TypeInfo
    presence: Presence
    completeness: Completeness
    result_hash: str
    metadata: "ToolResultMetadata"
```

Runtime은 이 결과에 Provenance를 부여한 뒤 ValueEnvelope으로 변환한다.

---

# 24. ToolError

```python
@dataclass(frozen=True)
class ToolError:
    code: str
    category: str
    message: str
    retryable: bool
    provider: str | None = None
```

대표 Error: `UPSTREAM_TIMEOUT`, `NOT_FOUND`, `OUTPUT_CONTRACT_VIOLATION`, `PAGINATION_INCOMPLETE`.

---

# 25. ToolContractCatalog

```python
class ToolContractCatalog(Protocol):
    def get_contract(self, tool_id: ToolId, version: str) -> "ToolContract | None":
        ...
```

Runtime Preflight에서 사용한다.

---

# 26. ToolResolver

```python
class ToolResolver(Protocol):
    def resolve(
        self,
        tool_id: ToolId,
        version: str,
        tenant_id: str | None,
    ) -> "ResolvedTool":
        ...

@dataclass(frozen=True)
class ResolvedTool:
    contract: "ToolContract"
    binding: "ToolBinding"
```

---

# 27. ProviderInvoker

```python
class ProviderInvoker(Protocol):
    async def invoke(
        self,
        binding: "ToolBinding",
        payload: Mapping[str, object],
        context: ToolCallContext,
    ) -> "ProviderOutcome":
        ...
```

구현 후보: `MCPProviderInvoker`, `MockProviderInvoker`, `ReplayProviderInvoker`.

---

# 28. CanonicalToolExecutor

```python
class CanonicalToolExecutor:
    def __init__(
        self,
        resolver: ToolResolver,
        invoker: ProviderInvoker,
    ):
        self._resolver = resolver
        self._invoker = invoker

    async def execute(
        self,
        request: ToolCallRequest,
    ) -> ToolExecutionOutcome:
        ...
```

주요 책임:

```text
Resolve
Contract Hash Validation
Input Validation
Canonical Mapping
Provider Invoke
Output Mapping
Output Validation
Completeness Evaluation
Stable Ordering Normalize
ToolExecutionOutcome
```

---

# 29. ExpressionEvaluator

```python
class ExpressionEvaluator:
    def __init__(
        self,
        builtins: "BuiltinRegistry",
        completeness: "CompletenessAlgebra",
    ):
        ...

    async def evaluate(
        self,
        expression: "Expression",
        ctx: ExecutionContext,
        ports: RuntimePorts,
    ) -> ValueEnvelope:
        ...
```

지원 Expression: Literal, SymbolRef, Field, Projection, Binary, BuiltinCall, Read.

---

# 30. READ 평가

```python
async def _evaluate_read(
    self,
    expression: "ReadExpression",
    ctx: ExecutionContext,
    ports: RuntimePorts,
) -> ValueEnvelope:

    request = ...
    outcome = await ports.tools.execute(request)

    if isinstance(outcome, ToolFailure):
        raise ToolExecutionError(outcome.error)

    result = outcome.result

    provenance_ref = ports.provenance.record(
        ToolProvenanceRecord(...)
    )

    return ValueEnvelope(
        value=result.value,
        type_info=result.type_info,
        presence=result.presence,
        completeness=result.completeness,
        provenance_ref=provenance_ref,
    )
```

Tool Failure는 Value로 변환하지 않는다.

---

# 31. BuiltinFunction Protocol

```python
class BuiltinFunction(Protocol):
    @property
    def name(self) -> str:
        ...

    @property
    def version(self) -> str:
        ...

    def invoke(
        self,
        arguments: tuple[ValueEnvelope, ...],
        provenance: ProvenanceStore,
    ) -> ValueEnvelope:
        ...
```

v0.1 구현: SumBuiltin, CountBuiltin, MinBuiltin, MaxBuiltin.

---

# 32. BuiltinRegistry

```python
class BuiltinRegistry:
    def resolve(self, name: str, version: str) -> BuiltinFunction:
        ...
```

Built-in 분기 로직을 Runtime 곳곳에 흩어놓지 않는다.

---

# 33. CompletenessAlgebra

```python
class CompletenessAlgebra:
    def combine(
        self,
        values: tuple[ValueEnvelope, ...],
    ) -> Completeness:
        ...
```

기본 규칙:

```text
UNKNOWN 존재 → UNKNOWN
else PARTIAL 존재 → PARTIAL
else → COMPLETE
```

Operation별 특수 Semantics는 Builtin이 처리한다.

---

# 34. PredicateEvaluation

```python
@dataclass(frozen=True)
class PredicateEvaluation:
    truth: bool
    presence: Presence
    completeness: Completeness
    provenance_refs: tuple[ProvenanceRef, ...]
```

---

# 35. CheckResult

```python
@dataclass(frozen=True)
class CheckResult:
    check_id: str
    status: CheckStatus
    severity: CheckSeverity
    message: str | None
    condition_node_id: NodeId
    completeness: Completeness
    provenance_refs: tuple[ProvenanceRef, ...]
    reason_code: str | None
```

---

# 36. CheckEvaluator

```python
class CheckEvaluator:
    def evaluate(
        self,
        statement: CheckStatement,
        predicate: PredicateEvaluation,
    ) -> CheckResult:
        ...
```

`NSL-0.1-STRICT`:

```text
TRUE + COMPLETE  → PASS
FALSE + COMPLETE → FAIL
PARTIAL          → UNKNOWN
UNKNOWN          → UNKNOWN
```

---

# 37. ValidationSummary

```python
@dataclass(frozen=True)
class ValidationSummary:
    total_checks: int
    pass_count: int
    fail_count: int
    unknown_count: int
    overall: str
```

Overall: `ALL_PASS`, `HAS_FAIL`, `HAS_UNKNOWN`, `HAS_FAIL_AND_UNKNOWN`, `NO_CHECK`.

---

# 38. StatementExecutor

```python
class StatementExecutor:
    def __init__(
        self,
        expressions: ExpressionEvaluator,
        checks: CheckEvaluator,
        resources: "ResourceGuard",
    ):
        ...

    async def execute(
        self,
        statement: "Statement",
        ctx: ExecutionContext,
        ports: RuntimePorts,
    ) -> None:
        ...

    async def execute_block(
        self,
        statements: tuple["Statement", ...],
        ctx: ExecutionContext,
        ports: RuntimePorts,
    ) -> None:
        ...
```

지원: LET, FOREACH, CHECK, EMIT.

---

# 39. LET Execution

```text
ExpressionEvaluator
      ↓
ValueEnvelope
      ↓
Current Frame.bind()
```

이미 Binding된 Symbol이면 `IMMUTABLE_BINDING_ERROR`.

---

# 40. FOREACH Execution

```python
collection = await expression_evaluator.evaluate(...)

for item in collection.value:
    resources.before_loop_iteration(ctx)

    loop_frame = Frame()
    loop_frame.bind(iterator_symbol_id, item_envelope)
    ctx.frames.push(loop_frame)

    try:
        await execute_block(...)
    finally:
        ctx.frames.pop()
```

v0.1에서는 Skill 내부 병렬 실행을 지원하지 않는다.

---

# 41. CHECK Execution

```text
Condition Expression
       ↓
ValueEnvelope[Bool]
       ↓
PredicateEvaluation
       ↓
CheckEvaluator
       ↓
CheckResult
       ↓
CheckStore
       ↓
result_symbol binding
```

---

# 42. EmitRecord

```python
@dataclass(frozen=True)
class EmitRecord:
    values: Mapping[str, ValueEnvelope]
```

---

# 43. EmitBuffer

```python
class EmitBuffer:
    def append(self, record: EmitRecord) -> None:
        ...

    def items(self) -> tuple[EmitRecord, ...]:
        ...
```

Output Schema Validation 후 append한다.

---

# 44. ResourceMeter

```python
@dataclass
class ResourceMeter:
    tool_calls: int = 0
    loop_iterations: int = 0
    emitted_rows: int = 0
    max_collection_size_seen: int = 0
```

---

# 45. ResourceGuard

```python
class ResourceGuard:
    def before_tool_call(self, ctx: ExecutionContext) -> None:
        ...

    def before_loop_iteration(self, ctx: ExecutionContext) -> None:
        ...

    def before_emit(self, ctx: ExecutionContext) -> None:
        ...

    def check_deadline(
        self,
        ctx: ExecutionContext,
        clock: RuntimeClock,
    ) -> None:
        ...
```

Limit 초과는 CHECK FAIL이 아니라 Runtime 상태 `LIMIT_EXCEEDED`이다.

---

# 46. RuntimePreflightValidator

```python
class RuntimePreflightValidator:
    def validate(
        self,
        skill: SkillObject,
        tools: ToolContractCatalog,
    ) -> "PreflightReport":
        ...
```

검사 대상:

```text
NSO Version
NSL Version
Semantics Profile
Features
Symbol Integrity
Node Integrity
Tool Contract
Contract Hash
READ Capability
Resource Bound
```

---

# 47. RuntimeEngine

```python
class RuntimeEngine:
    def __init__(
        self,
        preflight: RuntimePreflightValidator,
        statements: StatementExecutor,
        context_factory: "ExecutionContextFactory",
    ):
        ...

    async def execute(
        self,
        skill: SkillObject,
        request: ExecutionRequest,
        ports: RuntimePorts,
    ) -> "ExecutionResult":
        ...
```

RuntimeEngine은 Tool, Expression, CHECK Logic을 직접 구현하지 않는다.

---

# 48. ExecutionResult

```python
@dataclass(frozen=True)
class ExecutionResult:
    execution_id: ExecutionId
    skill_id: SkillId
    skill_version: str
    semantic_hash: str
    status: ExecutionStatus
    validation: ValidationSummary
    outputs: tuple[EmitRecord, ...]
    resources: "ResourceUsage"
    error: RuntimeErrorInfo | None
```

NeX-AE는 ExecutionResult를 기반으로 자연어 설명 및 보고를 생성한다.

---

# 49. RuntimeEngine 실행 흐름

```text
RuntimeEngine.execute()
       ↓
Preflight
       ↓
ExecutionContextFactory
       ↓
EXECUTION_STARTED
       ↓
StatementExecutor.execute_block()
       ↓
ValidationSummary
       ↓
ExecutionResult
       ↓
EXECUTION_COMPLETED
```

Runtime Exception은 RuntimeEngine Boundary에서 구조화한다.

---

# 50. TraceEvent

```python
@dataclass(frozen=True)
class TraceEvent:
    execution_id: ExecutionId
    sequence: int
    event_type: str
    node_id: NodeId | None
    payload: Mapping[str, object]
```

Sequence는 Execution 단위로 단조 증가한다.

---

# 51. TraceSink 구현 후보

```text
NullTraceSink
MemoryTraceSink
JsonTraceSink
BufferedPostgresTraceSink
```

Runtime Core는 PostgreSQL을 직접 알지 않는다.

---

# 52. ProvenanceRecord

```python
@dataclass(frozen=True)
class ProvenanceRecord:
    kind: str
    node_id: NodeId
    source_refs: tuple[ProvenanceRef, ...]
    tool_invocation_id: ToolInvocationId | None
    value_hash: str | None
```

---

# 53. ProvenanceStore

```python
class ProvenanceStore(Protocol):
    def record(
        self,
        record: ProvenanceRecord,
    ) -> ProvenanceRef:
        ...

    def get(
        self,
        ref: ProvenanceRef,
    ) -> ProvenanceRecord:
        ...
```

---

# 54. ReplayToolExecutor

```python
class ReplayToolExecutor(ToolExecutionPort):
    def __init__(self, snapshots: "ToolSnapshotStore"):
        ...

    async def execute(
        self,
        request: ToolCallRequest,
    ) -> ToolExecutionOutcome:
        ...
```

RuntimeEngine은 Replay 여부를 알 필요가 없다.

---

# 55. MockToolExecutor

```python
class MockToolExecutor(ToolExecutionPort):
    def __init__(self, fixtures: "ToolFixtureStore"):
        ...

    async def execute(
        self,
        request: ToolCallRequest,
    ) -> ToolExecutionOutcome:
        ...
```

Unit / Regression Test의 기본 Tool Runtime으로 사용한다.

---

# 56. Runtime Code Path 통일

```text
Live
RuntimeEngine → ToolExecutionPort → CanonicalToolExecutor → MCPProviderInvoker

Test
RuntimeEngine → ToolExecutionPort → MockToolExecutor

Replay
RuntimeEngine → ToolExecutionPort → ReplayToolExecutor
```

동일한 Runtime Code Path를 사용한다.

---

# 57. Public API Module

NeX-AE가 내부 구현 Module을 직접 사용하지 않도록 `nsl/api/`를 둔다.

```python
from nsl.api import (
    RuntimeEngine,
    ExecutionRequest,
    ExecutionResult,
    RuntimePorts,
    NsoLoader,
)
```

---

# 58. Runtime Bootstrap

```python
builtins = BuiltinRegistry(...)
completeness = CompletenessAlgebra()

expression_evaluator = ExpressionEvaluator(
    builtins=builtins,
    completeness=completeness,
)

check_evaluator = CheckEvaluator()
resource_guard = ResourceGuard()

statement_executor = StatementExecutor(
    expressions=expression_evaluator,
    checks=check_evaluator,
    resources=resource_guard,
)

engine = RuntimeEngine(
    preflight=RuntimePreflightValidator(),
    statements=statement_executor,
    context_factory=ExecutionContextFactory(),
)
```

실행:

```python
skill = nso_loader.load_path(
    Path("project_budget_check.nso")
)

result = await engine.execute(
    skill=skill,
    request=request,
    ports=ports,
)
```

---

# 59. 권장 Python Module 구조

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
├─ api/
│  └─ __init__.py
│
└─ cli/
   └─ main.py
```

---

# 60. Module Dependency Rule

```text
core
 ↑
 ├─ ir
 ├─ tools
 └─ validation
      ↑
    runtime
      ↑
 ├─ audit
 └─ replay
      ↑
     api
      ↑
    NeX-AE
```

금지사항:

```text
core → runtime                금지
tools → ExecutionContext       금지
IR → runtime behavior          금지
Runtime → MCP SDK 직접         금지
```

---

# 61. Object Lifetime

Application Lifetime:

```text
RuntimeEngine
BuiltinRegistry
NsoLoader
ToolRegistry
ToolContractCatalog
```

Execution Lifetime:

```text
ExecutionContext
ResourceMeter
FrameStack
EmitBuffer
CheckStore
Trace Context
```

Tool Call Lifetime:

```text
ToolCallRequest
ToolCallContext
ProviderOutcome
ToolExecutionOutcome
```

---

# 62. RuntimeEngine 재사용성

RuntimeEngine은 `current_user`, `current_symbol`, `current_loop` 등의 실행 상태를 보유하지 않아야 한다.

모든 실행 상태는 ExecutionContext에 격리한다.

---

# 63. Skill 내부 Execution Ordering

v0.1에서는 하나의 Skill 내부를 Sequential Execution으로 유지한다.

```text
READ A
   ↓ await
READ B
   ↓ await
CHECK
```

여러 Skill Execution 간 동시성은 허용하지만 하나의 Skill 내부 Statement 병렬 실행은 지원하지 않는다.

---

# 64. 핵심 Interface 7개

Skeleton Code 구현 전 다음 Interface를 Baseline으로 확정한다.

```text
1. RuntimeEngine.execute()
2. ExpressionEvaluator.evaluate()
3. StatementExecutor.execute()
4. ToolExecutionPort.execute()
5. CheckEvaluator.evaluate()
6. TraceSink.emit()
7. ProvenanceStore.record()
```

---

# 65. 핵심 Data Object

```text
SkillObject
ExecutionRequest
ExecutionContext
ValueEnvelope
ToolCallRequest
ToolExecutionOutcome
CheckResult
ExecutionResult
```

---

# 66. ToolResultEnvelope과 ValueEnvelope 분리

```text
Tool Layer 결과
ToolResultEnvelope
       ↓
Runtime Provenance 생성
       ↓
ValueEnvelope
```

이를 통해 Tool Layer가 ExecutionContext 및 Audit Implementation에 의존하지 않도록 한다.

---

# 67. Runtime 전체 Class 관계 요약

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

# 68. 최종 설계 결정

| 항목 | 결정 |
|---|---|
| IR Object | Passive Immutable Data |
| Runtime Behavior | Service Object |
| Execution State | ExecutionContext |
| RuntimeEngine | Stateless / Reusable |
| Value | ValueEnvelope |
| Tool Result | ToolResultEnvelope |
| Tool Failure | Value 없음 |
| Execution Variable | Immutable Binding |
| Skill 내부 실행 | Sequential |
| Tool 호출 | async |
| External I/O | RuntimePorts |
| Live/Test/Replay | 동일 Runtime Code Path |
| MCP SDK Dependency | Provider Adapter만 사용 |
| Runtime Public API | `nsl/api`로 제한 |
| Python Core | Framework 독립 |

---

# 69. 다음 구현 단계

본 Detailed Design을 기준으로 다음 단계는 실제 Python Skeleton Code를 생성하는 것이다.

권장 순서:

```text
Phase 1
core/
    ids
    states
    types
    money
    values
    errors

Phase 2
ir/
    expressions
    statements
    model

Phase 3
runtime/
    request
    result
    frame
    context
    ports

Phase 4
tools/
    contracts
    requests
    outcomes

Phase 5
validation/
    predicate
    check
    summary

Phase 6
runtime/
    expressions
    statements
    engine

Phase 7
mock execution

Phase 8
MCP adapter
```

---

# 70. 최종 정의

NSL Runtime v0.1의 Python Object Model은 다음 원칙을 따른다.

> **Language와 IR은 데이터를 정의하고, Runtime Service는 실행을 담당하며, ExecutionContext는 실행 상태를 격리하고, RuntimePorts는 외부 세계와의 연결을 추상화한다.**

Live, Mock, Replay가 동일한 RuntimeEngine과 Statement/Expression 실행 경로를 공유하도록 설계한다.

본 문서를 NSL Runtime Python 구현의 Detailed Design Baseline으로 사용한다.
