# NeX Skill Language Compiler Detailed Design v0.1

**문서 버전:** v0.1  
**상태:** Detailed Design Baseline  
**대상 Language:** NSL v0.1  
**구현 언어:** Python 3.11+  
**Source:** `.ns`  
**Target IR:** `.nso` — NeX Skill Object  
**연계:** NSL Runtime v1.0 / Canonical Tool Contract  
**기본 Semantics Profile:** `NSL-0.1-STRICT`

---

## 1. 목적

본 문서는 NSL v0.1 Source Code를 분석·검증하고 Runtime 실행용 `.nso` Intermediate Representation으로 변환하기 위한 Compiler의 상세 설계를 정의한다.

Compiler는 단순 Syntax Translator가 아니라 다음 정적 검증 경계 역할을 수행한다.

```text
.ns Source
   ↓
Source Dependency Resolution
   ↓
Syntax Validation
   ↓
Semantic Validation
   ↓
Type Resolution
   ↓
Tool Contract Resolution
   ↓
Safety Analysis
   ↓
Resource Bound Analysis
   ↓
Normalization / Lowering
   ↓
Canonical Typed IR
   ↓
.nso
```

---

## 2. 핵심 Architecture 원칙

1. AST와 `.nso` IR을 분리한다.
2. Runtime은 Source AST를 실행하지 않는다.
3. `.ns` 직접 실행도 내부적으로 Compile → Runtime 경로를 사용한다.
4. Compiler는 Canonical Tool Contract까지만 Resolve한다.
5. 고객별 Tool Binding은 Compiler가 알지 않는다.
6. Static Type Checking을 최대한 수행한다.
7. Safety Rule을 Compile 단계에서 강제한다.
8. Bounded Execution을 Compile 단계에서 검증한다.
9. 동일 Source Bundle + 동일 Contract 환경은 동일 Semantic IR을 생성해야 한다.
10. Error Message는 Source 위치 중심으로 제공한다.
11. `include`는 Textual Concatenation이 아니라 Structured Source Composition으로 처리한다.
12. Bool Literal은 `true / false`를 사용하며 Implicit Truthiness는 금지한다.

---

## 3. Compiler Pass Pipeline

```text
Root .ns Source
   │
   ▼
P01 Root Source Loading
   │
   ▼
P02 Lexical Analysis
   │
   ▼
P03 Parsing
   │
   ▼
Root AST
   │
   ▼
P04 Include Resolution
   │
   ▼
Include Dependency Graph
   │
   ▼
P05 Included Source Loading / Parsing
   │
   ▼
P06 Source Composition
   │
   ▼
Combined AST
   │
   ▼
P07 Declaration Collection
   │
   ▼
P08 Name / Symbol Resolution
   │
   ▼
P09 Tool Contract Resolution
   │
   ▼
P10 Type Checking & Type Inference
   │
   ▼
P11 Semantic Validation
   │
   ▼
P12 Safety Analysis
   │
   ▼
P13 Resource Bound Analysis
   │
   ▼
Semantic Model
   │
   ▼
P14 Normalization / Lowering
   │
   ▼
Execution-oriented IR
   │
   ▼
P15 IR Validation
   │
   ▼
P16 Deterministic ID Assignment
   │
   ▼
P17 Canonical Serialization
   │
   ▼
.nso
```

---

## 4. Compiler와 Runtime의 경계

```text
nsl run skill.ns

skill.ns
   ↓
Compiler
   ↓
SkillObject
   ↓
RuntimeEngine
```

```text
nsl run skill.nso

skill.nso
   ↓
NsoLoader
   ↓
SkillObject
   ↓
RuntimeEngine
```

별도의 AST Interpreter는 구현하지 않는다.

---

## 5. Source Model

```python
@dataclass(frozen=True, slots=True)
class SourceFile:
    source_id: SourceId
    logical_path: str
    text: str
    encoding: str = "utf-8"
```

모든 Source는 논리 경로와 Source ID를 가진다.

---

## 6. SourceSpan

```python
@dataclass(frozen=True, slots=True)
class SourcePosition:
    offset: int
    line: int
    column: int


@dataclass(frozen=True, slots=True)
class SourceSpan:
    source_id: SourceId
    start: SourcePosition
    end: SourcePosition
```

Include 파일의 오류도 원본 SourceSpan을 유지해야 한다.

---

## 7. Token Model

```python
class TokenKind(Enum):
    IDENTIFIER = "IDENTIFIER"
    STRING = "STRING"
    INTEGER = "INTEGER"
    DECIMAL = "DECIMAL"
    BOOLEAN = "BOOLEAN"
    DURATION = "DURATION"

    LBRACE = "{"
    RBRACE = "}"
    LPAREN = "("
    RPAREN = ")"
    COLON = ":"
    SEMICOLON = ";"
    COMMA = ","
    DOT = "."
    ASSIGN = "="

    PLUS = "+"
    MINUS = "-"
    STAR = "*"
    SLASH = "/"

    EQ = "=="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="

    EOF = "EOF"
```

---

## 8. NSL v0.1 Keyword

```text
language
skill
version
description
risk
include
requires
tool
limits
input
context
output
let
read
foreach
in
max
check
assert
severity
on_fail
message
emit
true
false
```

---

## 9. Identifier / Qualified Name

기본 Identifier:

```text
[A-Za-z_][A-Za-z0-9_]*
```

Qualified Name은 Lexer가 하나의 Token으로 만들지 않고 Parser가 구성한다.

```text
PROJECT.LIST_PARENT_PROJECTS
```

→ `IDENTIFIER DOT IDENTIFIER DOT IDENTIFIER`

---

## 10. String / Numeric / Duration Literal

String Escape:

```text
\"
\\
\n
\r
\t
```

Numeric:

```text
123
123.45
```

Scientific Notation, NaN, Infinity는 v0.1에서 지원하지 않는다.

Duration:

```nsl
duration: 30s;
```

지원 단위:

```text
ms
s
m
```

IR에서는 millisecond로 Normalize한다.

---

## 11. Bool Literal

NSL v0.1은 다음 Constant를 제공한다.

```nsl
true
false
```

Python 스타일 `True / False`는 사용하지 않는다.

Lexer:

```python
KEYWORDS = {
    "true": TokenKind.BOOLEAN,
    "false": TokenKind.BOOLEAN,
}
```

AST:

```python
LiteralExpressionAst(
    value=True,
    literal_kind=LiteralKind.BOOL,
    span=span,
)
```

IR:

```json
{
  "kind": "literal",
  "type": {"kind": "primitive", "name": "Bool"},
  "value": true
}
```

Implicit Truthiness는 금지한다.

```nsl
assert count;
```

에서 `count`가 Int이면 Compile Error다.

허용:

```text
Bool == Bool → Bool
Bool != Bool → Bool
```

금지:

```text
Bool < Bool
Bool > Bool
Bool == Int
Bool == String
```

---

## 12. Comment

v0.1에서는 Single-line Comment를 지원한다.

```nsl
// comment
```

Block Comment는 제외한다.

---

## 13. Include 문법

```nsl
include "common/finance.ns";
```

Include는 C/C++식 Textual Preprocessor가 아니다.

```text
Root Source
   ↓
Lexer / Parser
   ↓
IncludeDeclarationAst
   ↓
IncludeResolver
   ↓
Included Source AST
   ↓
SourceComposer
   ↓
Combined AST
```

---

## 14. Include Scope

v0.1에서 Include Fragment에 허용:

```text
requires
context
limits
include
```

금지 권장:

```text
skill
input
output
let
foreach
check
emit
```

Input / Output Contract는 항상 Root Skill Source에 명시한다.

---

## 15. Include AST

```python
@dataclass(frozen=True, slots=True)
class IncludeDeclarationAst:
    path: str
    span: SourceSpan
```

```python
@dataclass(frozen=True, slots=True)
class IncludeFragmentAst:
    includes: tuple[IncludeDeclarationAst, ...]
    requires: tuple[ToolRequirementAst, ...]
    contexts: tuple[ContextDeclarationAst, ...]
    limits: tuple[LimitDeclarationAst, ...]
    span: SourceSpan
```

---

## 16. IncludeResolver

```python
class IncludeResolver(Protocol):
    def resolve(
        self,
        including_source: SourceFile,
        include_path: str,
    ) -> SourceFile:
        ...
```

Compiler Environment에 IncludeResolver를 주입한다.

---

## 17. Include Path Safety

Include Root 밖으로 나가는 Path는 금지한다.

허용:

```nsl
include "common/finance.ns";
```

금지:

```nsl
include "/etc/nex/secret.ns";
include "../../../../secret.ns";
```

---

## 18. Include Dependency Graph

Compiler는 Include 관계를 Graph로 관리한다.

```text
budget_check.ns
 ├─ common/finance.ns
 │   └─ common/base_read.ns
 └─ common/project.ns
```

사용 목적:

```text
Cycle Detection
Duplicate Include Detection
Diamond Include 처리
Source Manifest
Diagnostic
Bundle Hash
```

---

## 19. Circular Include

```text
A.ns → B.ns → C.ns → A.ns
```

은 Compile Error다.

```text
NSL-E2301
Circular include dependency detected.
```

---

## 20. Diamond Include

```text
A
├─ B
│  └─ D
└─ C
   └─ D
```

Canonical Source ID 기준으로 D는 한 번만 Compose한다.

---

## 21. Include Resource Limit

권장 기본값:

```text
max_include_depth = 16
max_include_files = 100
max_total_source_bytes = 10 MB
```

---

## 22. Source Composition Rule

`requires`는 Set Merge한다.

동일 Tool + 동일 Contract는 하나로 Normalize한다.

서로 다른 Version 요구는 Compile Error다.

Context 중복은 Error다.

Limits의 동일 Field 중복도 Error다.

암묵적 Override는 사용하지 않는다.

---

## 23. Parser Architecture

Hand-written Recursive Descent Parser를 기본으로 한다.

Expression은 Pratt Parser 또는 Precedence Climbing 방식을 사용한다.

```text
Parser
 ├─ Declaration Parser
 ├─ Include Parser
 ├─ Statement Parser
 └─ Expression Parser
```

---

## 24. AST 기본 원칙

AST는 Source-oriented 구조다.

AST에서는 아직 다음이 미확정일 수 있다.

```text
Name
Type
Tool Contract
Default Value
```

모든 AST Node는 SourceSpan을 가진다.

---

## 25. Skill AST

```python
@dataclass(frozen=True, slots=True)
class SkillDeclaration:
    name: QualifiedName
    version: str
    description: str | None
    risk: str

    includes: tuple[IncludeDeclarationAst, ...]
    requires: tuple[ToolRequirementAst, ...]
    limits: tuple[LimitDeclarationAst, ...]
    inputs: tuple[InputDeclarationAst, ...]
    contexts: tuple[ContextDeclarationAst, ...]
    outputs: tuple[OutputFieldAst, ...]
    body: tuple[StatementAst, ...]

    span: SourceSpan
```

---

## 26. Expression AST

```text
LiteralExpressionAst
IdentifierExpressionAst
FieldAccessExpressionAst
CallExpressionAst
ReadExpressionAst
BinaryExpressionAst
ParenthesizedExpressionAst
```

Collection Projection은 Type Analysis 후 Lowering 단계에서 명시적 IR Node로 바꾼다.

---

## 27. Literal Grammar

```ebnf
literal
    = string_literal
    | integer_literal
    | decimal_literal
    | boolean_literal
    ;

boolean_literal
    = "true"
    | "false"
    ;
```

---

## 28. Include Grammar

```ebnf
include_decl
    = "include"
      string_literal
      ";"
    ;
```

Semantic Validator가 Include Fragment 내 허용 요소를 검증한다.

---

## 29. Operator Precedence

```text
Highest

Field Access / Call
Unary              (v0.2 후보)
* /
+ -
< <= > >=
== !=
not                (v0.2)
and                (v0.2)
or                 (v0.2)

Lowest
```

---

## 30. Diagnostic Model

```python
class DiagnosticSeverity(Enum):
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"
```

```python
@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: DiagnosticSeverity
    message: str
    span: SourceSpan | None
    notes: tuple[str, ...] = ()
```

Include 오류는 실제 SourceSpan과 Include Chain을 함께 제공한다.

---

## 31. Semantic Analysis Pipeline

```text
Combined AST
 │
 ├─ DeclarationCollector
 ├─ NameResolver
 ├─ ToolContractResolver
 ├─ TypeChecker
 ├─ SemanticValidator
 ├─ SafetyAnalyzer
 └─ ResourceBoundAnalyzer
 │
 ▼
SemanticModel
```

---

## 32. Symbol Namespace

```text
Value Symbols
Check Symbols
Tool Symbols
Builtin Symbols
Type Symbols
```

Shadowing은 v0.1에서 금지한다.

---

## 33. Tool Contract Resolution

Compiler는 Canonical Tool Contract까지만 Resolve한다.

Resolve 대상:

```text
tool_id
version
capability
risk
input_schema
output_schema
contract_hash
```

Resolve 금지:

```text
MCP URL
Customer Binding
Credential
Provider-specific Mapping
```

---

## 34. Static Type System

```text
String
Int
Decimal
Bool
Date
DateTime
Year
Money(Currency)
TeamId
EmployeeId
ProjectCode
OrganizationId
List<T>
CheckStatus
Named Record
```

---

## 35. Type Checking

Compile-time 검사:

```text
Identifier Type
Field 존재 여부
Function Argument Type
Tool Argument Type
Binary Operator Type
CHECK Assert Bool 여부
Output Type
Money Currency Compatibility
```

---

## 36. Arithmetic Type Rule

| Operator | Left | Right | Result |
|---|---|---|---|
| `+` | Int | Int | Int |
| `-` | Int | Int | Int |
| `*` | Int | Int | Int |
| `/` | Int | Int | Decimal |
| `+` | Decimal | Decimal | Decimal |
| `-` | Decimal | Decimal | Decimal |
| `*` | Decimal | Decimal | Decimal |
| `/` | Decimal | Decimal | Decimal |
| `+` | Money(C) | Money(C) | Money(C) |
| `-` | Money(C) | Money(C) | Money(C) |

Money × Money, Money / Money는 v0.1에서 금지한다.

---

## 37. Comparison Type Rule

| Operator | Left | Right | Result |
|---|---|---|---|
| `< <= > >=` | Int | Int | Bool |
| `< <= > >=` | Decimal | Decimal | Bool |
| `< <= > >=` | Money(C) | Money(C) | Bool |
| `== !=` | 동일 Type | 동일 Type | Bool |
| `== !=` | Bool | Bool | Bool |

---

## 38. Built-in Signature Registry

```python
class BuiltinSignatureRegistry(Protocol):
    def resolve(
        self,
        name: str,
        argument_types: tuple[TypeInfo, ...],
    ) -> BuiltinSignature | None:
        ...
```

v0.1:

```text
sum(List<Int>)      → Int
sum(List<Decimal>)  → Decimal
sum(List<Money(C)>) → Money(C)
count(List<T>)      → Int
min(List<T>)        → T
max(List<T>)        → T
```

`coalesce()`는 Optional/Null/Missing Semantics 확정 전까지 비활성화한다.

---

## 39. Safety Analyzer

```text
SAF-001 WRITE Capability Tool 금지
SAF-002 Dynamic Tool Reference 금지
SAF-003 Unbounded foreach 금지
SAF-004 foreach max <= 0 금지
SAF-005 지원하지 않는 Context Path 금지
SAF-006 Unknown Builtin 금지
SAF-007 Recursion 구조 금지
SAF-008 Dynamic Code 실행 구조 금지
SAF-009 Unsupported Feature 금지
SAF-010 Include Root Escape 금지
SAF-011 Circular Include 금지
SAF-012 Include Resource Limit 초과 금지
```

---

## 40. Resource Bound Analysis

정적 계산 대상:

```text
Tool Calls
Loop Iterations
Emit Records
```

기본 규칙:

```text
Bound(Block) = Σ Bound(statement)
ToolCalls(READ) = 1
Bound(FOREACH) = max_iterations × Bound(body) + Bound(collection)
EmitBound(EMIT) = 1
```

---

## 41. SemanticModel

```python
@dataclass(frozen=True)
class SemanticModel:
    symbols: SymbolModel
    expression_types: Mapping[AstNodeId, TypeInfo]
    resolved_tools: Mapping[AstNodeId, ResolvedToolRequirement]
    builtins: Mapping[AstNodeId, BuiltinSignature]
    projection_nodes: frozenset[AstNodeId]
    resource_bounds: ResourceBounds
    source_bundle: SourceBundleModel
```

---

## 42. Lowering / Normalization

대표 변환:

```text
Source Name → Symbol ID
Tool Name → Tool Ref
Field on List → Projection
<= → LE
+ → ADD
sum → builtin/sum/version 1
Duration → milliseconds
Missing Limit → explicit default
CHECK Policy → NSL-0.1-STRICT
Bool Literal → typed bool literal
```

---

## 43. CHECK Policy Normalize

```json
{
  "data_policy": {
    "require_complete": true,
    "on_partial": "UNKNOWN",
    "on_unknown": "UNKNOWN"
  }
}
```

UNKNOWN → PASS 설정은 지원하지 않는다.

---

## 44. Source Manifest / Hash

Include Source가 존재하므로 Build Metadata에 Source Manifest를 포함한다.

```json
{
  "build": {
    "sources": [
      {"logical_path": "skills/budget_check.ns", "sha256": "..."},
      {"logical_path": "common/finance.ns", "sha256": "..."}
    ]
  }
}
```

별도 Hash:

```text
source_bundle_sha256
semantic_sha256
```

Source Bundle Hash는 실제 Source 묶음을 식별한다.

Semantic Hash는 최종 실행 의미를 식별한다.

동일 실행 의미라면 파일 분할 방식이 달라도 Semantic Hash는 동일할 수 있다.

---

## 45. CompilationEnvironment

```python
@dataclass(frozen=True)
class CompilationEnvironment:
    tool_catalog: ToolContractCatalog
    context_schema: ContextSchema
    builtin_signatures: BuiltinSignatureRegistry
    include_resolver: IncludeResolver
```

---

## 46. CompilerOptions

```python
@dataclass(frozen=True)
class CompilerOptions:
    language_version: str = "0.1"
    semantics_profile: str = "NSL-0.1-STRICT"
    warnings_as_errors: bool = False
    debug_metadata: bool = True
    max_include_depth: int = 16
    max_include_files: int = 100
    max_total_source_bytes: int = 10 * 1024 * 1024
```

---

## 47. Compiler Public Interface

```python
class NslCompiler:
    def compile(
        self,
        source: SourceFile,
        environment: CompilationEnvironment,
        options: CompilerOptions,
    ) -> CompilationResult:
        ...
```

---

## 48. CompilationResult

```python
@dataclass(frozen=True)
class CompilationSuccess:
    skill: SkillObject
    nso_bytes: bytes
    semantic_hash: str
    source_bundle_hash: str
    diagnostics: tuple[Diagnostic, ...]
```

```python
@dataclass(frozen=True)
class CompilationFailure:
    diagnostics: tuple[Diagnostic, ...]
```

Compile Error가 존재하면 `.nso`를 생성하지 않는다.

---

## 49. Compiler Module 구조

```text
nsl/
├─ compiler/
│  ├─ compiler.py
│  ├─ context.py
│  ├─ environment.py
│  ├─ result.py
│  ├─ source/
│  │  ├─ model.py
│  │  ├─ resolver.py
│  │  ├─ includes.py
│  │  ├─ graph.py
│  │  └─ composer.py
│  ├─ lexical/
│  │  ├─ token.py
│  │  └─ lexer.py
│  ├─ syntax/
│  │  ├─ ast.py
│  │  ├─ parser.py
│  │  └─ precedence.py
│  ├─ semantic/
│  │  ├─ symbols.py
│  │  ├─ declarations.py
│  │  ├─ resolver.py
│  │  ├─ tool_resolver.py
│  │  ├─ type_checker.py
│  │  ├─ builtin_signatures.py
│  │  ├─ validator.py
│  │  └─ model.py
│  ├─ analysis/
│  │  ├─ safety.py
│  │  └─ resources.py
│  ├─ lowering/
│  │  ├─ lowerer.py
│  │  ├─ normalization.py
│  │  └─ ids.py
│  └─ diagnostics/
│     ├─ model.py
│     ├─ sink.py
│     └─ formatter.py
├─ core/
├─ ir/
├─ tools/
└─ runtime/
```

Compiler는 Runtime에 의존하지 않는다.

---

## 50. Test Strategy

```text
Lexer Unit Test
Parser Golden Test
Parser Negative Test
Include Resolution Test
Include Cycle Test
Diamond Include Test
Include Path Safety Test
Bool Literal Test
Truthiness Negative Test
Symbol Resolution Test
Scope / Shadowing Test
Type Rule Test
Tool Contract Test
Safety Test
Resource Bound Test
Lowering Golden Test
Canonical .nso Test
Semantic Hash Test
Source Bundle Hash Test
Diagnostic Golden Test
```

---

## 51. Include Test 필수 시나리오

```text
Single Include
Nested Include
Diamond Include
Duplicate Include
Circular Include
Missing File
Root Escape
Depth Limit
File Count Limit
Total Byte Limit
Duplicate Context
Duplicate Limit
Tool Set Merge
```

---

## 52. Bool Test 필수 시나리오

```nsl
let a = true;
let b = false;
```

→ `a: Bool`, `b: Bool`

```nsl
assert true;
```

→ Valid

```nsl
assert 1;
```

→ Type Error

```nsl
true == false
```

→ Bool

```nsl
true < false
```

→ Type Error

---

## 53. Compiler Invariant

`.nso` 생성 성공 시 다음을 보장한다.

```text
모든 Include 정상 Resolve
Circular Include 없음
Include Root Escape 없음
모든 Symbol Resolve
모든 Expression Type 확정
Bool Truthiness 위반 없음
모든 Tool Contract Resolve
Tool Binding 없음
WRITE Tool 없음
모든 foreach Bounded
Resource Bound 계산 완료
Output Schema 검증 완료
CHECK Bool 조건 검증 완료
Unsupported Feature 없음
Runtime 지원 IR Node만 존재
```

---

## 54. 구현 순서

```text
Phase 1  Source / SourceSpan / Diagnostic / Token
Phase 2  Lexer + Bool Literal
Phase 3  AST + Parser
Phase 4  IncludeResolver / DependencyGraph / SourceComposer
Phase 5  Symbol Table / Name Resolver
Phase 6  Tool Contract Resolver
Phase 7  Type Checker / Built-in Signature
Phase 8  Semantic / Safety Analyzer
Phase 9  Resource Bound Analyzer
Phase 10 Lowerer / Normalizer
Phase 11 IR Generator
Phase 12 Canonical Serializer / Hash
Phase 13 Compiler Public API
Phase 14 CLI check / compile
Phase 15 Runtime Integration
```

---

## 55. 최종 설계 결정

| 항목 | v0.1 결정 |
|---|---|
| Parser | Hand-written Recursive Descent |
| Expression Parser | Pratt / Precedence Climbing |
| AST | Source-oriented |
| AST Interpreter | 만들지 않음 |
| Runtime Target | `.nso / SkillObject` |
| Include | Structured Source Composition |
| Include Scope | requires/context/limits/include |
| Include Text Concatenation | 사용하지 않음 |
| Include Cycle | Compile Error |
| Include Diamond | include-once |
| Include Path | Include Root 내부만 |
| Bool Literal | `true / false` |
| Implicit Truthiness | 금지 |
| Symbol | Namespace 분리 |
| Shadowing | 금지 |
| Type | Static |
| Tool | Canonical Contract Resolve |
| Binding | Compile 시 Resolve하지 않음 |
| Built-in | Compile Signature Registry |
| Safety | 독립 Compiler Pass |
| Loop | Static Bound 필수 |
| Lowering | AST → normalized IR |
| Node/Symbol ID | Deterministic |
| IR | Canonical Typed Execution IR |
| Diagnostic | Multi-error + SourceSpan |
| Compiler Dependency | Runtime 독립 |

---

## 56. 최종 정의

NeX Skill Language Compiler는 `.ns`를 단순 변환하는 Translator가 아니다.

> **NSL Compiler는 하나 이상의 `.ns` Source를 안전하게 구성하고, 업무자동화 Logic을 구문·의미·타입·Tool Contract·Safety·Resource 관점에서 검증한 뒤 Runtime이 신뢰할 수 있는 Canonical Typed Execution Object인 `.nso`로 변환하는 정적 검증 경계이다.**

v0.1부터 `include`를 통한 공통 선언 재사용과 `true / false` Bool Literal을 Language Baseline에 포함한다.

이 구조를 NSL Compiler Detailed Design v0.1 Baseline으로 사용한다.
