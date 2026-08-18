# NeX Skill Language Compiler Python Class / Interface Detailed Design v0.1

**문서 버전:** v0.1  
**상태:** Detailed Design Baseline  
**대상:** NSL v0.1 Compiler  
**구현 언어:** Python 3.11+  
**Source:** `.ns`  
**Target:** `.nso` / `SkillObject`

---

## 1. 설계 목표

Compiler 구현은 다음 Pipeline을 Class Boundary에 반영한다.

```text
Source
  ↓
Lexer
  ↓
Parser
  ↓
Include Resolver / Source Composer
  ↓
Declaration Collector
  ↓
Name Resolver
  ↓
Tool Contract Resolver
  ↓
Type Checker
  ↓
Semantic Validator
  ↓
Safety Analyzer
  ↓
Resource Bound Analyzer
  ↓
Lowerer
  ↓
IR Validator
  ↓
Canonical Serializer
  ↓
.nso
```

핵심 원칙:

```text
Token / AST / SemanticModel / IR = Passive Data
Lexer / Parser / Analyzer / Lowerer = Behavior
Compiler = Pass Orchestrator
```

---

## 2. Compiler Public API

```python
class NslCompiler:

    def compile(
        self,
        source: SourceFile,
        environment: CompilationEnvironment,
        options: CompilerOptions,
    ) -> CompilationResult:
        ...

    def check(
        self,
        source: SourceFile,
        environment: CompilationEnvironment,
        options: CompilerOptions,
    ) -> CompilationCheckResult:
        ...
```

`check()`는 `.nso` 외부 산출물을 만들지 않고 Compile Pass를 수행한다.

---

## 3. Source / Position Model

```python
SourceId = NewType("SourceId", str)
AstNodeId = NewType("AstNodeId", str)

@dataclass(frozen=True, slots=True)
class SourceFile:
    source_id: SourceId
    logical_path: str
    text: str
    encoding: str = "utf-8"

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

---

## 4. IncludeResolver

```python
class IncludeResolver(Protocol):

    def resolve(
        self,
        including_source: SourceFile,
        include_path: str,
    ) -> IncludeResolution:
        ...
```

구현 후보:

```text
FileSystemIncludeResolver
PackageIncludeResolver
MemoryIncludeResolver
```

Include는 Text Concatenation이 아니라 Structured Source Composition으로 처리한다.

---

## 5. Token / Lexer

```python
@dataclass(frozen=True, slots=True)
class Token:
    kind: TokenKind
    lexeme: str
    value: object | None
    span: SourceSpan
```

`true / false`는 `BOOLEAN` Token으로 처리한다.

```python
class Lexer:

    def tokenize(
        self,
        source: SourceFile,
        diagnostics: DiagnosticSink,
    ) -> LexResult:
        ...
```

---

## 6. Parser

```python
class ParseMode(str, Enum):
    ROOT_SKILL = "ROOT_SKILL"
    INCLUDE_FRAGMENT = "INCLUDE_FRAGMENT"

class Parser:

    def parse(
        self,
        lexed: LexResult,
        mode: ParseMode,
        diagnostics: DiagnosticSink,
    ) -> ParseResult:
        ...
```

Parser는 Hand-written Recursive Descent를 기본으로 하고, Expression은 Pratt Parser 또는 Precedence Climbing을 사용한다.

---

## 7. AST 핵심 객체

```python
@dataclass(frozen=True, slots=True)
class AstNode:
    node_id: AstNodeId
    span: SourceSpan
```

주요 AST:

```text
LiteralExpressionAst
IdentifierExpressionAst
FieldAccessExpressionAst
BinaryExpressionAst
ReadExpressionAst

LetStatementAst
ForeachStatementAst
CheckStatementAst
EmitStatementAst

IncludeDeclarationAst
CompilationUnitAst
IncludeFragmentAst
```

AST는 Source-oriented이며 아직 Symbol/Type/Tool Resolution이 완료되지 않은 상태를 허용한다.

---

## 8. Include Dependency Graph

```python
@dataclass(frozen=True)
class IncludeEdge:
    from_source: SourceId
    to_source: SourceId
    include_span: SourceSpan

@dataclass
class IncludeDependencyGraph:
    nodes: dict[SourceId, ParsedSourceUnit]
    edges: list[IncludeEdge]
```

지원 기능:

```text
Cycle Detection
Diamond Include Deduplication
Include Chain Diagnostic
Source Manifest
```

---

## 9. SourceBundleBuilder / SourceComposer

```python
class SourceBundleBuilder:

    def build(
        self,
        root_source: SourceFile,
        resolver: IncludeResolver,
        options: CompilerOptions,
        diagnostics: DiagnosticSink,
    ) -> SourceBundleResult:
        ...
```

```python
class SourceComposer:

    def compose(
        self,
        bundle: SourceBundle,
        diagnostics: DiagnosticSink,
    ) -> ComposedSkillAst | None:
        ...
```

v0.1 Include Fragment 허용 범위:

```text
include
requires
context
limits
```

Root Skill의 `input`, `output`, 실행문은 include에서 가져오지 않는다.

---

## 10. Diagnostic

```python
class DiagnosticSink(Protocol):

    def report(self, diagnostic: Diagnostic) -> None:
        ...

    def has_errors(self) -> bool:
        ...

    def diagnostics(self) -> tuple[Diagnostic, ...]:
        ...
```

Source 오류는 Diagnostic으로 처리하고 Compiler 내부 버그만 Exception으로 처리한다.

---

## 11. Declaration / Symbol

```python
class DeclarationCollector:

    def collect(
        self,
        ast: ComposedSkillAst,
        diagnostics: DiagnosticSink,
    ) -> DeclarationIndex:
        ...
```

Namespace:

```text
Value Symbols
Check Symbols
Tool Symbols
Builtin Symbols
Type Symbols
```

Shadowing은 v0.1에서 금지한다.

Deterministic Symbol ID:

```text
s0001
s0002
s0003
...
```

---

## 12. NameResolver

```python
class NameResolver:

    def resolve(
        self,
        ast: ComposedSkillAst,
        declarations: DeclarationIndex,
        diagnostics: DiagnosticSink,
    ) -> NameResolution:
        ...
```

---

## 13. Tool Contract Resolution

```python
class ToolContractCatalog(Protocol):

    def resolve(
        self,
        tool_id: ToolId,
    ) -> ToolContract | None:
        ...

class ToolContractResolver:

    def resolve(
        self,
        ast: ComposedSkillAst,
        catalog: ToolContractCatalog,
        diagnostics: DiagnosticSink,
    ) -> ToolResolution:
        ...
```

Compiler는 Canonical Tool Contract까지만 Resolve하며 고객별 Tool Binding은 Resolve하지 않는다.

---

## 14. Context / Builtin Signature

```python
class ContextSchema(Protocol):

    def resolve_path(
        self,
        path: tuple[str, ...],
    ) -> TypeInfo | None:
        ...

class BuiltinSignatureRegistry(Protocol):

    def resolve(
        self,
        name: str,
        argument_types: tuple[TypeInfo, ...],
    ) -> BuiltinSignature | None:
        ...
```

---

## 15. Type Checker

```python
class OperatorTypeRules:

    def resolve_binary(
        self,
        operator: SourceBinaryOperator,
        left: TypeInfo,
        right: TypeInfo,
    ) -> TypeInfo | None:
        ...

class TypeChecker:

    def check(
        self,
        ast: ComposedSkillAst,
        names: NameResolution,
        tools: ToolResolution,
        contexts: ContextSchema,
        builtins: BuiltinSignatureRegistry,
        diagnostics: DiagnosticSink,
    ) -> TypeAnalysis:
        ...
```

핵심 Rule:

```text
assert는 정확히 Bool Type만 허용
Implicit Truthiness 금지
Money Currency Type Check
Tool Argument Type Check
Field Access Type Check
Collection Projection Type Inference
```

---

## 16. Semantic / Safety / Resource Analysis

```python
class SemanticValidator:

    def validate(
        self,
        ast: ComposedSkillAst,
        names: NameResolution,
        tools: ToolResolution,
        types: TypeAnalysis,
        diagnostics: DiagnosticSink,
    ) -> SemanticValidationReport:
        ...
```

```python
class SafetyAnalyzer:

    def analyze(
        self,
        ast: ComposedSkillAst,
        tools: ToolResolution,
        types: TypeAnalysis,
        bundle: SourceBundle,
        options: CompilerOptions,
        diagnostics: DiagnosticSink,
    ) -> SafetyReport:
        ...
```

```python
class ResourceBoundAnalyzer:

    def analyze(
        self,
        ast: ComposedSkillAst,
        types: TypeAnalysis,
        tools: ToolResolution,
        diagnostics: DiagnosticSink,
    ) -> ResourceBounds:
        ...
```

---

## 17. SemanticModel

```python
@dataclass(frozen=True)
class SemanticModel:
    names: NameResolution
    tools: ToolResolution
    types: TypeAnalysis
    safety: SafetyReport
    resource_bounds: ResourceBounds
    source_bundle: SourceBundleModel
```

AST는 Source 구조를 유지하고 분석 결과는 SemanticModel에 저장한다.

---

## 18. Lowering

```python
class Lowerer:

    def lower(
        self,
        ast: ComposedSkillAst,
        semantic: SemanticModel,
        options: CompilerOptions,
        diagnostics: DiagnosticSink,
    ) -> SkillObject | None:
        ...
```

주요 변환:

```text
Source Name → SymbolRef
Tool Name → ToolRef
Field on List → Projection
Source Operator → IR Operator
true/false → Typed Bool Literal
Duration → milliseconds
CHECK → Strict Data Policy
Limits → Explicit Defaults
```

---

## 19. IR Validation / Serialization

```python
class IrValidator:

    def validate(
        self,
        skill: SkillObject,
        diagnostics: DiagnosticSink,
    ) -> bool:
        ...
```

```python
class CanonicalNsoSerializer:

    def serialize(
        self,
        skill: SkillObject,
        build: BuildMetadata,
    ) -> NsoArtifact:
        ...
```

---

## 20. CompilationEnvironment / Options

```python
@dataclass(frozen=True)
class CompilationEnvironment:
    tool_catalog: ToolContractCatalog
    context_schema: ContextSchema
    builtin_signatures: BuiltinSignatureRegistry
    include_resolver: IncludeResolver
```

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

## 21. Compilation Result

```python
@dataclass(frozen=True)
class CompilationSuccess:
    skill: SkillObject
    nso_bytes: bytes
    semantic_hash: str
    source_bundle_hash: str
    diagnostics: tuple[Diagnostic, ...]

@dataclass(frozen=True)
class CompilationFailure:
    diagnostics: tuple[Diagnostic, ...]
```

---

## 22. CompilerServices

```python
@dataclass(frozen=True)
class CompilerServices:
    lexer: Lexer
    parser: Parser
    bundle_builder: SourceBundleBuilder
    composer: SourceComposer
    declarations: DeclarationCollector
    names: NameResolver
    tool_resolver: ToolContractResolver
    type_checker: TypeChecker
    semantic_validator: SemanticValidator
    safety: SafetyAnalyzer
    resources: ResourceBoundAnalyzer
    lowerer: Lowerer
    ir_validator: IrValidator
    serializer: CanonicalNsoSerializer
```

---

## 23. NslCompiler

```python
class NslCompiler:

    def __init__(
        self,
        services: CompilerServices,
    ) -> None:
        self._services = services

    def compile(
        self,
        source: SourceFile,
        environment: CompilationEnvironment,
        options: CompilerOptions,
    ) -> CompilationResult:
        ...

    def check(
        self,
        source: SourceFile,
        environment: CompilationEnvironment,
        options: CompilerOptions,
    ) -> CompilationCheckResult:
        ...
```

Compiler 자체는 Compile별 Mutable State를 보유하지 않는다.

---

## 24. Public API

```python
from nsl.compiler.api import (
    NslCompiler,
    SourceFile,
    CompilationEnvironment,
    CompilerOptions,
    CompilationSuccess,
    CompilationFailure,
)
```

외부 Client가 내부 Compiler Pass를 직접 Import하지 않도록 한다.

---

## 25. 핵심 Baseline Interface

```text
Lexer.tokenize()

Parser.parse()

IncludeResolver.resolve()

SourceBundleBuilder.build()

SourceComposer.compose()

DeclarationCollector.collect()

NameResolver.resolve()

ToolContractResolver.resolve()

TypeChecker.check()

SemanticValidator.validate()

SafetyAnalyzer.analyze()

ResourceBoundAnalyzer.analyze()

Lowerer.lower()

IrValidator.validate()

CanonicalNsoSerializer.serialize()

NslCompiler.compile()
```

---

## 26. Compiler / Runtime 경계

```text
Compiler
    ↓
SkillObject
    ↓
.nso
    ↓
Runtime
```

Compiler는 Runtime Behavior를 알지 않고 Runtime은 Source Syntax를 알지 않는다.

`include`는 Compiler 단계에서 제거되며 Runtime에는 노출되지 않는다.

`true / false`는 Typed Bool Literal IR로 변환된다.

---

## 27. 최종 설계 원칙

> Compiler는 Source를 이해하고 검증한다.

> `.nso`는 검증된 실행 의미를 표현한다.

> Runtime은 `.nso`의 의미만 실행한다.

본 문서를 NSL Compiler Python Class / Interface Detailed Design v0.1 Baseline으로 사용한다.
