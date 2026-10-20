# NeX Skill Language Shared Core / IR Single Source of Truth Detailed Design v0.1

**문서 버전:** v0.1  
**상태:** Detailed Design Baseline  
**대상:** NSL v0.1 Compiler / Runtime 공통 기반  
**구현 언어:** Python 3.11+  
**핵심 목적:** Compiler와 Runtime이 동일한 Type / IR 정의를 공유하도록 단일 기준 모델(Single Source of Truth)을 확정

---

## 1. 문서 목적

본 문서는 NeX Skill Language(NSL) Compiler와 Runtime이 공통으로 사용하는 `core/` Type Model과 `ir/` Object Model의 역할, 책임, Python Package 구조, Dependency Rule 및 버전관리 원칙을 정의한다.

핵심 목표는 다음과 같다.

```text
Compiler
   │
   │ produces
   ▼
Shared IR / SkillObject
   ▲
   │ consumes
   │
Runtime
```

Compiler용 Type/IR과 Runtime용 Type/IR을 별도로 만들지 않는다.

---

## 2. Stable Kernel 정의

NSL 전체 Architecture에서 다음 두 계층을 Stable Kernel로 정의한다.

### 2.1 Stable Semantic Kernel

```text
nsl/core/
```

책임:

```text
ID
Type
Money
Presence
Completeness
Check Status
Capability
Common Error Value
Runtime Value Envelope
```

정의:

> NSL의 실행 의미를 표현하는 최소 공통 Value Model

### 2.2 Stable Execution Contract

```text
nsl/ir/
```

책임:

```text
SkillObject
Expression
Statement
Policy
Resource Limit
Required Tool
Static Analysis
.nso Encoding / Decoding
IR Validation
Canonical Serialization
Semantic Hash
```

정의:

> Compiler가 생성하고 Runtime이 실행하는 Canonical Execution Contract

---

## 3. 전체 관계

```text
.ns Source
   │
   ▼
Compiler
   │
   ▼
core/ Type Model
   │
   ▼
ir/ SkillObject
   │
   ▼
.nso
   │
   ▼
Runtime
```

Compiler와 Runtime은 서로 직접 의존하지 않는다.

---

## 4. Single Source of Truth 원칙

다음 객체는 한 곳에서만 정의한다.

| 개념 | 기준 Package |
|---|---|
| SkillId | `core.ids` |
| SymbolId | `core.ids` |
| NodeId | `core.ids` |
| ToolId | `core.ids` |
| TypeInfo | `core.types` |
| Money | `core.money` |
| Presence | `core.data_quality` |
| Completeness | `core.data_quality` |
| CheckStatus | `core.checks` |
| CheckSeverity | `core.checks` |
| ToolCapability | `core.capabilities` |
| ValueEnvelope | `core.values` |
| BinaryOperator | `ir.operators` |
| Expression | `ir.expressions` |
| Statement | `ir.statements` |
| CheckDataPolicy | `ir.policies` |
| SkillObject | `ir.skill` |
| RequiredTool | `ir.schema` |
| ResourceLimits | `ir.schema` |

금지:

```text
CompilerMoneyType / RuntimeMoneyType
CompilerSkillObject / RuntimeSkillObject
CompilerCheckStatus / RuntimeCheckStatus
```

---

## 5. `core/` 포함 기준

객체를 `core/`에 넣기 위한 기준:

> Compiler / Runtime / Tool / Validation 같은 상위 Layer를 전혀 몰라도 독립적인 의미를 가지는가?

YES인 경우에만 `core/` 후보로 둔다.

예:

```text
Money                 YES
MoneyType             YES
Presence              YES
Completeness          YES
CheckStatus           YES
ToolCapability        YES
ValueEnvelope         YES

ExecutionContext      NO
ToolBinding           NO
CompilationResult     NO
SourceSpan            NO
Parser Token          NO
```

---

## 6. `core/` 권장 구조

```text
nsl/
└─ core/
   ├─ ids.py
   ├─ types.py
   ├─ money.py
   ├─ values.py
   ├─ data_quality.py
   ├─ checks.py
   ├─ capabilities.py
   └─ errors.py
```

`core/`는 Python Standard Library 외 Dependency를 사용하지 않는 것을 원칙으로 한다.

---

## 7. `core/ids.py`

```python
from typing import NewType

SkillId = NewType("SkillId", str)
SymbolId = NewType("SymbolId", str)
NodeId = NewType("NodeId", str)

ToolId = NewType("ToolId", str)
ToolRef = NewType("ToolRef", str)
ToolInvocationId = NewType("ToolInvocationId", str)

ExecutionId = NewType("ExecutionId", str)
ProvenanceRef = NewType("ProvenanceRef", str)
```

Compiler 전용 `SourceId`, `AstNodeId`는 `core/`에 넣지 않는다.

---

## 8. `core/types.py`

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
    PrimitiveType
    | DomainType
    | MoneyType
    | ListType
    | NamedType
    | EnumType
)
```

---

## 9. Compiler / Runtime Type 책임

Compiler:

```text
Type inference
Operator type validation
Tool result type resolution
Builtin return type 결정
Money currency compatibility
Field access / projection type resolution
```

Runtime:

```text
IR Type에 대한 방어적 conformance check
Tool 결과가 Contract Type과 일치하는지 검증
Runtime Value가 IR Type과 일치하는지 검증
```

Runtime은 Type Inference를 다시 수행하지 않는다.

---

## 10. `core/money.py`

```python
from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal
    currency: str
```

Compiler와 Runtime이 동일 `Money`를 사용한다.

---

## 11. `core/data_quality.py`

```python
from enum import Enum


class Presence(str, Enum):
    PRESENT = "PRESENT"
    EMPTY = "EMPTY"


class Completeness(str, Enum):
    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNKNOWN = "UNKNOWN"
```

사용 영역:

```text
Tool Result
ExecutionContext
Validation
Replay
Audit
```

---

## 12. `core/checks.py`

```python
class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


class CheckSeverity(str, Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
```

---

## 13. `core/capabilities.py`

```python
class ToolCapability(str, Enum):
    READ = "READ"
    WRITE = "WRITE"
    TRANSACTION = "TRANSACTION"
```

Compiler Safety Analyzer와 Runtime Preflight가 같은 Enum을 사용한다.

---

## 14. `core/values.py`

```python
@dataclass(frozen=True, slots=True)
class ValueEnvelope:
    value: object
    type_info: TypeInfo
    presence: Presence
    completeness: Completeness
    provenance_ref: ProvenanceRef
```

ValueEnvelope은 Runtime / Validation / Audit / Replay가 공유한다.

---

## 15. ToolResultEnvelope은 `core/`에 넣지 않음

Tool Subsystem 개념은 `tools/`에 둔다.

```text
core.ValueEnvelope
    Runtime 일반 Value

tools.ToolResultEnvelope
    외부 Tool 실행 결과
```

---

## 16. `ir/` 포함 기준

IR 포함 기준:

> Runtime 실행 의미의 일부인가?

YES:

```text
BinaryExpression
ForeachStatement
CheckDataPolicy
ResourceLimits
RequiredTool
SkillObject
```

NO:

```text
Compiler Diagnostic
IncludeDeclaration
Source AST
Tool Binding
ExecutionContext
```

---

## 17. `ir/` 권장 구조

```text
nsl/
└─ ir/
   ├─ operators.py
   ├─ expressions.py
   ├─ statements.py
   ├─ policies.py
   ├─ schema.py
   ├─ skill.py
   ├─ analysis.py
   ├─ debug.py
   ├─ encoder.py
   ├─ decoder.py
   ├─ canonical.py
   ├─ hashing.py
   ├─ validator.py
   └─ codec.py
```

---

## 18. `ir/operators.py`

```python
class BinaryOperator(str, Enum):
    ADD = "ADD"
    SUB = "SUB"
    MUL = "MUL"
    DIV = "DIV"

    EQ = "EQ"
    NE = "NE"

    LT = "LT"
    LE = "LE"
    GT = "GT"
    GE = "GE"
```

Compiler는 Source Operator를 이 Enum으로 Lowering하고 Runtime은 Source Symbol을 알지 않는다.

---

## 19. `ir/expressions.py`

대표 Expression:

```text
LiteralExpression
SymbolRefExpression
FieldExpression
ProjectionExpression
BinaryExpression
BuiltinCallExpression
ReadExpression
```

예:

```python
@dataclass(frozen=True, slots=True)
class BinaryExpression:
    node_id: NodeId
    operator: BinaryOperator
    left: Expression
    right: Expression
    type_info: TypeInfo
```

---

## 20. `ir/statements.py`

대표 Statement:

```text
LetStatement
ForeachStatement
CheckStatement
EmitStatement
```

IR Object는 실행 Method를 갖지 않는다.

```text
Passive Data only
```

---

## 21. `ir/policies.py`

```python
class IncompleteDataAction(str, Enum):
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"
```

```python
@dataclass(frozen=True, slots=True)
class CheckDataPolicy:
    require_complete: bool
    on_partial: IncompleteDataAction
    on_unknown: IncompleteDataAction
```

`NSL-0.1-STRICT`:

```text
require_complete = true
on_partial = UNKNOWN
on_unknown = UNKNOWN
```

---

## 22. `ir/schema.py`

```python
@dataclass(frozen=True, slots=True)
class RequiredTool:
    tool_ref: ToolRef
    tool_id: ToolId
    version: str
    capability: ToolCapability
    contract_hash: str
```

ToolContract 전체가 아니라 Skill이 요구하는 Fingerprint를 IR에 저장한다.

---

## 23. ToolContract와 RequiredTool 구분

ToolContract:

```text
Canonical Business Interface 전체 정의
```

위치:

```text
tools/contracts.py
```

RequiredTool:

```text
특정 Skill이 요구하는 Tool Fingerprint
```

위치:

```text
ir/schema.py
```

---

## 24. `ir/skill.py`

```python
@dataclass(frozen=True, slots=True)
class SkillObject:
    ir_version: str
    language_version: str

    skill_id: SkillId
    skill_version: str

    semantic_hash: str
    semantics_profile: str

    features: frozenset[str]

    required_tools: tuple[RequiredTool, ...]

    limits: ResourceLimits

    inputs: tuple[InputSpec, ...]
    contexts: tuple[ContextSpec, ...]
    output_schema: OutputSchema

    body: tuple[Statement, ...]

    analysis: StaticAnalysis
```

Compiler와 Runtime이 동일 Class를 Import한다.

---

## 25. `.nso`와 SkillObject

```text
.nso Canonical JSON
       ⇅
    NsoCodec
       ⇅
    SkillObject
```

`.nso`는 Persistent / Distribution Representation이고 `SkillObject`는 In-memory Execution Representation이다.

---

## 26. IR Debug Model

IR이 Compiler의 `SourceSpan`에 의존하지 않도록 별도 DTO를 둔다.

```python
@dataclass(frozen=True, slots=True)
class DebugSourceSpan:
    logical_path: str
    start_line: int
    start_column: int
    end_line: int
    end_column: int
```

Compiler Lowerer가 Compiler SourceSpan을 IR DebugSourceSpan으로 변환한다.

---

## 27. Nso Encoder / Decoder

Compiler Serializer와 Runtime Loader가 별도 JSON 구현을 갖지 않도록 한다.

```python
class NsoEncoder:
    def encode(
        self,
        skill: SkillObject,
        build: BuildMetadata,
    ) -> bytes:
        ...


class NsoDecoder:
    def decode(
        self,
        data: bytes,
    ) -> SkillObject:
        ...
```

`NsoCodec`은 façade로 사용할 수 있다.

---

## 28. Canonical Serialization

`ir/canonical.py` 책임:

```text
UTF-8
Stable Object Key Order
Stable Array Order
Decimal String Representation
ISO Date / DateTime
Deterministic Symbol / Node ID
```

동일 IR은 동일 Byte Sequence를 생성해야 한다.

---

## 29. IR Structural Validator

```python
class IrValidator:
    def validate(
        self,
        skill: SkillObject,
    ) -> IrValidationResult:
        ...
```

검사:

```text
Symbol Reference Integrity
Node ID uniqueness
ToolRef integrity
Supported Node Shape
Bounded foreach
Type validity
Check policy validity
Output schema validity
```

Compiler 생성 IR과 외부 `.nso` 모두 같은 Validator를 사용한다.

---

## 30. IrValidator와 RuntimePreflight 차이

IrValidator:

```text
SkillObject 자체가 구조적으로 정상인가?
```

RuntimePreflight:

```text
현재 Runtime Environment에서 실행 가능한가?
```

두 책임을 분리한다.

---

## 31. Tool Package 구조

```text
nsl/
└─ tools/
   ├─ contracts.py
   ├─ catalog.py
   ├─ bindings.py
   ├─ requests.py
   ├─ outcomes.py
   ├─ resolver.py
   ├─ executor.py
   └─ providers/
```

Compiler는:

```text
contracts
catalog
```

까지만 사용한다.

Runtime은 실행 단계에서 Binding/Resolver/Executor/Provider까지 사용한다.

---

## 32. 전체 Python Package 통합 구조

```text
nsl/
│
├─ core/
│  ├─ ids.py
│  ├─ types.py
│  ├─ money.py
│  ├─ values.py
│  ├─ data_quality.py
│  ├─ checks.py
│  ├─ capabilities.py
│  └─ errors.py
│
├─ ir/
│  ├─ operators.py
│  ├─ expressions.py
│  ├─ statements.py
│  ├─ policies.py
│  ├─ schema.py
│  ├─ skill.py
│  ├─ analysis.py
│  ├─ debug.py
│  ├─ encoder.py
│  ├─ decoder.py
│  ├─ canonical.py
│  ├─ hashing.py
│  ├─ validator.py
│  └─ codec.py
│
├─ compiler/
│  ├─ api.py
│  ├─ compiler.py
│  ├─ environment.py
│  ├─ options.py
│  ├─ result.py
│  ├─ source/
│  ├─ lexical/
│  ├─ syntax/
│  ├─ semantic/
│  ├─ analysis/
│  ├─ lowering/
│  └─ diagnostics/
│
├─ tools/
│  ├─ contracts.py
│  ├─ catalog.py
│  ├─ bindings.py
│  ├─ requests.py
│  ├─ outcomes.py
│  ├─ resolver.py
│  ├─ executor.py
│  └─ providers/
│
├─ validation/
│  ├─ predicate.py
│  ├─ semantics.py
│  ├─ check.py
│  ├─ summary.py
│  └─ ports.py
│
├─ runtime/
│  ├─ api.py
│  ├─ engine.py
│  ├─ request.py
│  ├─ result.py
│  ├─ preflight.py
│  ├─ context.py
│  ├─ frame.py
│  ├─ expressions.py
│  ├─ statements.py
│  ├─ builtins.py
│  ├─ resources.py
│  ├─ cancellation.py
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

## 33. Dependency Graph

```text
                     core
                ┌─────┼─────┐
                ▼     ▼     ▼
               ir   tools  audit
                ▲     ▲
                │     │
         ┌──────┘     └──────┐
         │                   │
     compiler            validation
         │                   │
         └────────┐  ┌───────┘
                  ▼  ▼
                runtime
                   │
              ┌────┼────┐
              ▼         ▼
            replay    package
              │
              └────┬────┘
                   ▼
                  api
                   │
                   ▼
                  cli
```

Compiler와 Runtime은 서로 직접 의존하지 않는다.

---

## 34. 허용 Dependency 규칙

```text
core
    → Python Standard Library only

ir
    → core

tools.contracts
    → core

compiler
    → core
    → ir
    → tools.contracts
    → tools.catalog

validation
    → core
    → ir

runtime
    → core
    → ir
    → tools public protocol
    → validation
    → audit protocol
```

---

## 35. 금지 Dependency 규칙

Architecture Error:

```text
core → compiler
core → runtime
core → tools

ir → compiler
ir → runtime

compiler → runtime

runtime → compiler

tools → runtime

validation → runtime

audit → runtime
```

---

## 36. Architecture Test

Dependency Rule은 자동 Test로 강제한다.

권장 파일:

```text
tests/architecture/
└─ test_dependency_rules.py
```

개념:

```python
assert_no_import(
    package="nsl.core",
    forbidden=[
        "nsl.compiler",
        "nsl.runtime",
        "nsl.tools",
    ],
)
```

CI에서 Architecture Rule 위반 시 실패시킨다.

---

## 37. IR Round-trip Test

```text
SkillObject
    ↓
Encode
    ↓
.nso bytes
    ↓
Decode
    ↓
SkillObject
```

Expected:

```text
Original == Decoded
```

---

## 38. Compiler / Runtime Contract Test

대표 Skill:

```text
.ns
 ↓
Compiler
 ↓
SkillObject
```

이 SkillObject를 직접 Runtime에 전달한 결과와:

```text
SkillObject
 ↓
Encode
 ↓
Decode
 ↓
Runtime
```

결과가 동일해야 한다.

---

## 39. Tool Contract Compatibility Test

Compiler가 `RequiredTool`을 생성하고 Runtime Preflight가 현재 ToolContract와 비교한다.

```text
contract_hash match
→ executable

contract_hash mismatch
→ Runtime Preflight Reject
```

---

## 40. Stable Layer Versioning

안정성 우선순위:

```text
가장 안정적

core/
  ↓
ir/
  ↓
tools contract
  ↓
compiler / runtime
  ↓
adapter

가장 빠르게 변경
```

---

## 41. Version 분리

```text
NSL Language Version
    0.1

NSO IR Version
    1.0

Compiler Version
    0.x

Runtime Version
    0.x

Skill Version
    1.0.0
```

Source Syntax가 바뀌더라도 기존 IR로 Lowering 가능하면 IR Version을 변경하지 않는다.

---

## 42. IR Version 변경 기준

```text
새 Source Keyword
→ 기존 IR Node로 Lowering 가능
→ IR Version 유지

새 Runtime Semantics 필요
→ 새 IR Node / Policy 필요
→ IR Version 변경 검토
```

---

## 43. core Type Versioning

다음 의미 변경은 Breaking Change로 취급한다.

```text
Money amount representation
Presence / Completeness 의미
CheckStatus 의미
ToolCapability 의미
```

`core/` Type은 가장 보수적으로 변경한다.

---

## 44. Public API

```text
nsl/api/
```

대표 Export:

Compiler:

```python
NslCompiler
CompilerOptions
CompilationEnvironment
```

Runtime:

```python
RuntimeEngine
ExecutionRequest
ExecutionResult
RuntimePorts
```

IR:

```python
SkillObject
NsoCodec
```

---

## 45. Test 구조

```text
tests/
├─ core/
├─ ir/
├─ compiler/
├─ tools/
├─ validation/
├─ runtime/
├─ replay/
└─ architecture/
```

`core` Test는 Compiler/Runtime 없이 실행 가능해야 한다.

`ir` Test도 Compiler 없이 실행 가능해야 한다.

---

## 46. Skeleton Code 구현 순서

```text
Phase 0
Package Directory
Architecture Dependency Test

Phase 1
core/

Phase 2
ir/ Object Model

Phase 3
ir/ Codec + Validator

Phase 4
tools/ Contract Model

Phase 5
compiler Front-end

Phase 6
compiler Semantic

Phase 7
compiler Lowering

Phase 8
runtime State Model

Phase 9
runtime Evaluator

Phase 10
Mock Tool

Phase 11
Compiler + Runtime Vertical Slice

Phase 12
Replay

Phase 13
MCP Adapter
```

---

## 47. 최종 정의

`core/`는 NSL의 **의미론적 Single Source of Truth**이다.

`ir/`는 Compiler와 Runtime 사이의 **실행계약 Single Source of Truth**이다.

Compiler:

```text
Source
→ core Type
→ ir SkillObject
```

Runtime:

```text
ir SkillObject
→ core Value
→ Execution
```

다음 구조는 만들지 않는다.

```text
Compiler-specific Type System
Runtime-specific Type System

Compiler-specific IR
Runtime-specific IR
```

본 문서를 NSL Compiler / Runtime Shared Core & IR Package Architecture Baseline으로 사용한다.
