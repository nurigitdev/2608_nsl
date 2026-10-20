# NeX Skill Language v0.1 Interpreter & Runtime Environment
## System Requirements Specification v1.1 Draft

- **문서 버전:** v1.1 Draft
- **작성일:** 2026-08-18
- **대상 Language:** NeX Skill Language(NSL) v0.1
- **구현 언어:** Python
- **대상 시스템:** NSL Interpreter, Compiler, Runtime Environment
- **연계 시스템:** NeX-AE, MCP Tool Registry, Canonical Business API
- **Source 확장자:** `.ns`
- **Intermediate/Object 확장자:** `.nso`
- **Package 확장자:** `.nsp`
- **예약 확장자:** `.nse` — 향후 Executable Image 용도
- **작성 목적:** NSL v0.1 Interpreter와 Runtime Environment의 구현 요구사항 정의
- **Requirement Baseline:** `NSL-V0.1-SRS-RB1` (Slice 0002)
- **Traceability:** `requirements/nsl_v0_1_traceability.json`

---

## 0. 개정 이력

| 버전 | 상태 | 주요 내용 |
|---|---|---|
| v0.1 | Concept | NSL DSL 기본 개념 및 회계·구매 Use Case 검토 |
| v1.0 Draft | 2026-08-14 | Interpreter, Compiler, IR, Runtime, Tool Execution, Validation, Audit/Replay 요구사항 정의 |
| v1.1 Draft | 2026-08-18 | `include` Structured Source Composition, `true/false` Bool Literal, AST Interpreter 미사용, Canonical Tool Contract, Strict Validation Policy, Source Bundle Hash, Shared `core/`/`ir/` 구조 반영 |
| v1.1 RB1 | 2026-08-18 | 325개 Requirement ID 동결, Requirement Fingerprint와 Slice/Test Traceability 도입, Coverage 및 Schedule 범위 정합화 |

### 0.1 Slice 0002 Rebaseline

- `NSL v0.1`은 Language Version이고 `Runtime v1.0`은 최초 구현 Release를 의미한다.
- Requirement Table의 ID, Priority, Text는 `NSL-V0.1-SRS-RB1`의 Fingerprint 대상이다.
- Workflow Language, Multi-Skill Orchestration, WRITE, APPROVAL은 NSL v0.1 범위 밖이다.
- Schedule은 NSL Syntax/IR이 아닌 NeX Platform의 단일 Skill 실행 확장이다.
- 전체 품질 기준은 Statement Coverage 95% 이상, Branch Coverage 90% 이상이다.
- 상세 상태와 Slice/Test 배정은 `docs/09_NSL_v0.1_SRS_Rebaseline_and_Traceability.md`를 따른다.

---

# 1. 문서 목적

본 문서는 NeX Skill Language(NSL) v0.1을 실행할 수 있는 Python 기반 Interpreter 및 Runtime Environment의 시스템 요구사항을 정의한다.

NSL은 범용 프로그래밍 언어가 아니라 Enterprise Agent가 검증된 MCP Tool과 업무 규칙을 사용하여 반복 업무를 안전하고 재현 가능하게 실행하기 위한 제한형 Domain-Specific Language이다.

NSL Runtime의 기본 철학은 다음과 같다.

```text
Natural Language
        │
        ▼
     NeX-AE
        │
 Intent / Context
        │
 Skill Selection
        │
────────┼────────────────────
 Non-deterministic Boundary
────────┼────────────────────
        ▼
       NSL
        │
        ▼
Deterministic Runtime
        │
 ┌──────┼────────┐
 ▼      ▼        ▼
MCP   CHECK    LIMIT
 │      │        │
 └──────┼────────┘
        ▼
 Structured Result
        │
────────┼────────────────────
        ▼
     NeX-AE
 Explain / Report
```

---

# 2. 시스템 목표

NSL Interpreter & Runtime v1.0의 목표는 다음과 같다.

1. `.ns` Source Code를 Parsing할 수 있어야 한다.
2. Syntax Tree(AST)를 생성할 수 있어야 한다.
3. Type, Tool, Safety 및 Resource Bound를 정적으로 검증할 수 있어야 한다.
4. 검증된 Source를 `.nso` Intermediate Representation으로 변환할 수 있어야 한다.
5. `.ns` 실행은 Compiler를 통해 in-memory `SkillObject`/`.nso` 의미로 변환한 뒤 `.nso`와 동일한 Runtime 경로를 사용해야 한다.
6. 외부 시스템 호출은 Registered MCP Tool을 통해서만 수행해야 한다.
7. 업무 Rule을 Deterministic하게 검증할 수 있어야 한다.
8. 모든 실행을 추적·감사·재현할 수 있어야 한다.
9. Tool 실패나 불완전 Data에 의해 False PASS가 발생하지 않아야 한다.
10. 향후 `.nsp` Certified Skill Package 상품화 구조로 확장할 수 있어야 한다.
11. `include`를 이용해 공통 선언 Source Fragment를 재사용하되 Compiler가 Structured Source Composition으로 안전하게 합성해야 한다.
12. Bool 상수 `true`, `false`를 정적 `Bool` Literal로 지원하고 Implicit Truthiness를 금지해야 한다.

---

# 3. Scope

## 3.1 v1.0 포함 범위

NSL v0.1의 실행 모델은 다음으로 제한한다.

```text
REQUIRES
    ↓
READ
    ↓
FOREACH
    ↓
LET
    ↓
CHECK
    ↓
EMIT

+ LIMITS
```

지원 기능:

- `.ns` Source Parsing
- `include` 기반 공통 Source Fragment Composition
- `true` / `false` Bool Literal
- Static Type Checking
- Immutable Variable
- MCP READ Tool
- Sequential Tool Execution
- Bounded `foreach`
- Deterministic Expression Evaluation
- `sum`, `count`, `min`, `max`
- Money Type
- CHECK
- PASS / FAIL / UNKNOWN
- Structured EMIT
- Execution Limit
- `.nso` IR
- `.nsp` 최소 Package
- Audit
- Deterministic Replay
- CLI
- Mock Tool Runtime
- NeX-AE Worker 연계

## 3.2 v1.0 제외 범위

다음 기능은 지원하지 않는다.

```text
WRITE
APPROVAL

if / else
retry
rollback
idempotency

schedule
watch
delegate

invoke_agent
invoke_skill

dynamic replan
recursion

arbitrary Python
arbitrary SQL
arbitrary HTTP
coalesce / Optional<T> / Null-Missing Semantics
```

해당 기능은 NSL v0.2 이후 또는 NeX-AE Stage 5~6에서 검토한다.

Slice 0002 Rebaseline에서는 `schedule`을 NSL v0.1 문법에서 계속 제외한다. 예약 실행은 하나의 등록된 Skill을 정해진 시각과 횟수로 호출하는 NeX Platform Schedule Extension으로만 정의하며, Workflow Language 또는 `invoke_skill`을 도입하지 않는다.

---

# 4. 전체 Architecture

```text
                       Root .ns
                          │
                          ▼
                    Lexer / Parser
                          │
                          ▼
                   Root Source AST
                          │
                          ▼
                 Include Resolution
                          │
                ┌─────────┼─────────┐
                ▼         ▼         ▼
             common A  common B  common C
                │         │         │
                └─────────┼─────────┘
                          ▼
                  Source Composition
                          │
                          ▼
                     Combined AST
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
 Declaration / Name   Tool Contract      Type Check
     Resolution        Resolution
        │                 │                  │
        └─────────────────┼──────────────────┘
                          ▼
                 Safety / Resource Bound
                          │
                          ▼
                 Lowering / Normalize
                          │
                          ▼
                  Shared SkillObject
                     / `.nso`
                          │
                          ▼
                    NSL Runtime
                          │
        ┌─────────────────┼──────────────────┐
        ▼                 ▼                  ▼
 Expression          Statement            Limit
 Evaluator           Executor             Guard
        │                 │                  │
        └─────────────────┼──────────────────┘
                          ▼
                    Tool Executor
                          │
                ┌─────────┴─────────┐
                ▼                   ▼
           Mock / Replay           MCP
                │                   │
                └─────────┬─────────┘
                          ▼
                Canonical Tool Contract
                          │
                          ▼
                Customer Tool Binding
                          │
                          ▼
                  Business System
```

`.ns` 직접 실행도 별도의 AST Interpreter를 두지 않는다.

```text
.ns → Compiler → SkillObject → RuntimeEngine
.nso → NsoCodec / IR Validator → SkillObject → RuntimeEngine
```

Compiler와 Runtime의 공통 기준은 Shared `core/` Type Model과 Shared `ir/SkillObject`이다.

# 5. Architecture 원칙

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-ARC-001 | Parser와 Runtime은 독립 Module로 구현해야 한다. | MUST |
| NSL-ARC-002 | `.ns` Source 실행은 Compile 후 `SkillObject`를 동일 Runtime에 전달하는 방식으로 지원해야 한다. | MUST |
| NSL-ARC-003 | Runtime은 Parser/AST에 종속되지 않고 `SkillObject`/`.nso`만 실행해야 한다. | MUST |
| NSL-ARC-004 | NSL Runtime 내부에서 LLM을 호출하지 않아야 한다. | MUST |
| NSL-ARC-005 | 모든 외부 업무시스템 접근은 Tool Executor를 통해야 한다. | MUST |
| NSL-ARC-006 | Runtime에서 DB, HTTP, filesystem에 임의 접근할 수 없어야 한다. | MUST |
| NSL-ARC-007 | Runtime Engine은 NeX-AE와 독립적인 Python Package로 구현해야 한다. | MUST |
| NSL-ARC-008 | 초기 배포 시 별도 Microservice가 아닌 NeX-AE Worker Process에 탑재할 수 있어야 한다. | SHOULD |
| NSL-ARC-009 | 향후 별도 Skill Runtime Service로 분리 가능해야 한다. | SHOULD |
| NSL-ARC-010 | 별도의 Source AST Interpreter를 구현하지 않아야 한다. | MUST |
| NSL-ARC-011 | Compiler와 Runtime은 동일한 `core/` Type Model과 `ir/` Object Model을 공유해야 한다. | MUST |
| NSL-ARC-012 | Compiler는 Canonical Tool Contract까지만 Resolve하고 고객별 Tool Binding은 Runtime에서 Resolve해야 한다. | MUST |
| NSL-ARC-013 | `include`는 Compiler Front-end에서 해소되어 Runtime IR에 남지 않아야 한다. | MUST |

---

# 6. Source Code 요구사항

## 6.1 `.ns`

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-SRC-001 | NSL Source 파일 확장자는 `.ns`이어야 한다. | MUST |
| NSL-SRC-002 | UTF-8 Encoding을 지원해야 한다. | MUST |
| NSL-SRC-003 | 한글 String Literal을 지원해야 한다. | MUST |
| NSL-SRC-004 | Source 위치를 Line/Column 단위로 추적해야 한다. | MUST |
| NSL-SRC-005 | Source 내 Skill ID와 Version을 선언해야 한다. | MUST |
| NSL-SRC-006 | Language Version을 Source에 선언해야 한다. | MUST |
| NSL-SRC-007 | 지원되지 않는 Language Version은 Compile Error 처리해야 한다. | MUST |
| NSL-SRC-008 | NSL Source는 `include` Keyword를 지원해야 한다. | MUST |
| NSL-SRC-009 | NSL Source는 `true`, `false` Bool Literal을 지원해야 한다. | MUST |
| NSL-SRC-010 | Root Skill Source와 Include Fragment Source의 Logical Path를 구분하여 추적해야 한다. | MUST |

예:

```nsl
language NSL "0.1";

skill FINANCE.PROJECT_BUDGET_CHECK {
    version "1.0.0";
}
```

---

## 6.2 `include` Source Composition 요구사항

v0.1의 `include`는 단순 Text Concatenation이 아니라 Compiler 단계의 Structured Source Composition으로 정의한다.

```nsl
include "common/finance.ns";
```

v0.1 Include Fragment는 `include`, `requires`, `context`, `limits`만 포함할 수 있다. `skill`, `input`, `output`, `let`, `foreach`, `check`, `emit`은 Include Fragment에서 금지한다.

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-INC-001 | Compiler는 `include`에 지정된 `.ns` Source Fragment를 Resolve해야 한다. | MUST |
| NSL-INC-002 | Include Source는 Text Concatenation이 아니라 AST/Source Composition 방식으로 합성해야 한다. | MUST |
| NSL-INC-003 | Circular Include를 Compile Error로 처리해야 한다. | MUST |
| NSL-INC-004 | Include Root 밖으로 벗어나는 Absolute Path/Path Traversal을 금지해야 한다. | MUST |
| NSL-INC-005 | Diamond Include는 Canonical Source ID 기준으로 동일 Source를 한 번만 Compose해야 한다. | MUST |
| NSL-INC-006 | Include Depth 기본 최대값은 16으로 제한해야 한다. | MUST |
| NSL-INC-007 | Include File 수 기본 최대값은 100으로 제한해야 한다. | MUST |
| NSL-INC-008 | 전체 Source Bundle 기본 최대 크기는 10MB로 제한해야 한다. | MUST |
| NSL-INC-009 | `requires`는 호환되는 Tool Contract 기준으로 Set Merge할 수 있어야 한다. | MUST |
| NSL-INC-010 | 중복 `context` 및 중복 `limits` Field는 암묵 Override하지 않고 Compile Error로 처리해야 한다. | MUST |
| NSL-INC-011 | Include Dependency Graph와 Source Manifest를 생성할 수 있어야 한다. | MUST |
| NSL-INC-012 | Include된 각 Source의 원래 Line/Column/Logical Path를 Diagnostic에 보존해야 한다. | MUST |

---

# 7. Lexer 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-LEX-001 | Source를 Token Stream으로 변환해야 한다. | MUST |
| NSL-LEX-002 | Keyword, Identifier, Literal, Operator, Delimiter를 구분해야 한다. | MUST |
| NSL-LEX-003 | 각 Token은 Source Line/Column 정보를 보유해야 한다. | MUST |
| NSL-LEX-004 | 잘못된 문자를 명확한 Lexical Error로 보고해야 한다. | MUST |
| NSL-LEX-005 | Comment 문법을 지원해야 한다. | SHOULD |
| NSL-LEX-006 | Lexer Error 이후 가능한 범위에서 추가 오류를 탐색할 수 있어야 한다. | SHOULD |
| NSL-LEX-007 | `true`, `false`를 BOOLEAN Literal Token으로 인식해야 한다. | MUST |
| NSL-LEX-008 | `include`를 Reserved Keyword로 인식해야 한다. | MUST |
| NSL-LEX-009 | Duration Literal(`30s`, `500ms` 등)을 독립 Token으로 처리해야 한다. | MUST |

주요 Keyword:

```text
language
skill
version
description
risk
include
requires
limits
input
context
output
tool
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

# 8. Parser / AST 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-PAR-001 | Token Stream으로부터 AST를 생성해야 한다. | MUST |
| NSL-PAR-002 | Parser는 Recursive Descent 방식으로 구현 가능해야 한다. | SHOULD |
| NSL-PAR-003 | Expression Parsing은 Operator Precedence를 정확히 처리해야 한다. | MUST |
| NSL-PAR-004 | Binary Expression AST를 지원해야 한다. | MUST |
| NSL-PAR-005 | Function Call Expression AST를 지원해야 한다. | MUST |
| NSL-PAR-006 | Field Reference AST를 지원해야 한다. | MUST |
| NSL-PAR-007 | READ Expression AST를 지원해야 한다. | MUST |
| NSL-PAR-008 | LET Statement AST를 지원해야 한다. | MUST |
| NSL-PAR-009 | FOREACH Statement AST를 지원해야 한다. | MUST |
| NSL-PAR-010 | CHECK Statement AST를 지원해야 한다. | MUST |
| NSL-PAR-011 | EMIT Statement AST를 지원해야 한다. | MUST |
| NSL-PAR-012 | Syntax Error는 Line/Column 및 기대 Token을 표시해야 한다. | MUST |
| NSL-PAR-013 | Parser가 Python `eval()` 또는 `exec()`를 사용해서는 안 된다. | MUST |
| NSL-PAR-014 | Boolean Literal Expression AST를 지원해야 한다. | MUST |
| NSL-PAR-015 | IncludeDeclaration AST를 지원해야 한다. | MUST |
| NSL-PAR-016 | Root Skill과 Include Fragment Parse Mode를 구분할 수 있어야 한다. | MUST |
| NSL-PAR-017 | Source-level Collection Field Access는 AST에서 유지하고 Projection 변환은 Type Analysis/Lowering 단계에서 수행해야 한다. | SHOULD |

AST 예:

```text
BinaryExpression
 ├─ Left
 │   └─ Reference(spent)
 ├─ Operator(<=)
 └─ Right
     └─ FieldReference(parent.budget)
```

---

# 9. Symbol 및 Scope 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-SEM-001 | Input, Context, Variable, Check Identifier를 Symbol Table로 관리해야 한다. | MUST |
| NSL-SEM-002 | 선언되지 않은 Identifier 사용을 Compile Error로 처리해야 한다. | MUST |
| NSL-SEM-003 | 동일 Scope에서 중복 Variable 선언을 금지해야 한다. | MUST |
| NSL-SEM-004 | `let` Variable은 Immutable이어야 한다. | MUST |
| NSL-SEM-005 | foreach 내부 Scope를 별도로 관리해야 한다. | MUST |
| NSL-SEM-006 | foreach iterator는 foreach Block 외부에서 사용할 수 없어야 한다. | MUST |
| NSL-SEM-007 | CHECK Result Identifier는 CHECK 이후 참조할 수 있어야 한다. | MUST |
| NSL-SEM-008 | v0.1에서는 Nested Scope의 Symbol Shadowing을 금지해야 한다. | MUST |

---

# 10. Type System 요구사항

지원 Primitive Type:

```text
String
Int
Decimal
Bool
Date
DateTime
Year
Money
TeamId
EmployeeId
ProjectCode
OrganizationId
List<T>
CheckStatus
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-TYP-001 | Static Type Checking을 지원해야 한다. | MUST |
| NSL-TYP-002 | Assignment 및 Expression Type을 Compile 시 검증해야 한다. | MUST |
| NSL-TYP-003 | Bool이 아닌 값을 CHECK assert에 사용할 수 없어야 한다. | MUST |
| NSL-TYP-004 | Tool Input Type과 Tool Contract Type을 검증해야 한다. | MUST |
| NSL-TYP-005 | Tool Result Type을 AST/IR에 반영해야 한다. | MUST |
| NSL-TYP-006 | List Element Type을 추적해야 한다. | MUST |
| NSL-TYP-007 | Record Field 존재 여부를 가능한 경우 Compile 시 검증해야 한다. | MUST |
| NSL-TYP-008 | Float/Double Type을 제공하지 않아야 한다. | MUST |
| NSL-TYP-009 | Decimal은 Python `decimal.Decimal` 기반으로 구현해야 한다. | MUST |
| NSL-TYP-010 | `true`와 `false`의 Static Type은 `Bool`이어야 한다. | MUST |
| NSL-TYP-011 | CHECK `assert`에는 정확히 `Bool` Type만 허용하고 Int/String/List 등의 Implicit Truthiness를 금지해야 한다. | MUST |
| NSL-TYP-012 | `Bool == Bool`, `Bool != Bool`은 허용하되 Bool Ordering Comparison은 금지해야 한다. | MUST |
| NSL-TYP-013 | Bool을 Int/String 등에서 암묵 변환하는 Built-in/Conversion을 v0.1에서 제공하지 않아야 한다. | MUST |

---

# 11. Money 요구사항

Money는 다음 논리구조를 갖는다.

```text
Money
 ├─ amount: Decimal
 └─ currency: Currency
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-MNY-001 | 금액 계산에 Binary Floating Point를 사용해서는 안 된다. | MUST |
| NSL-MNY-002 | Money는 Amount와 Currency를 함께 보유해야 한다. | MUST |
| NSL-MNY-003 | 동일 Currency 간 덧셈/뺄셈을 지원해야 한다. | MUST |
| NSL-MNY-004 | 서로 다른 Currency의 직접 연산을 금지해야 한다. | MUST |
| NSL-MNY-005 | 자동 환율변환을 수행하지 않아야 한다. | MUST |
| NSL-MNY-006 | Money List의 SUM은 동일 Currency만 허용해야 한다. | MUST |
| NSL-MNY-007 | Mixed Currency 발생 시 명시적인 Error를 반환해야 한다. | MUST |
| NSL-MNY-008 | Currency는 ISO 4217 Code 형태를 지원해야 한다. | SHOULD |

---

# 12. Built-in Function 요구사항

v1.0 Built-in:

```text
sum()
count()
min()
max()
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-BLT-001 | Built-in Function은 Side Effect가 없어야 한다. | MUST |
| NSL-BLT-002 | Built-in은 Runtime Registry를 통해 관리해야 한다. | MUST |
| NSL-BLT-003 | 사용자가 임의 Built-in을 등록할 수 없어야 한다. | MUST |
| NSL-BLT-004 | `sum()`은 Int, Decimal, Money Collection을 지원해야 한다. | MUST |
| NSL-BLT-005 | Empty Int/Decimal List의 `sum()` 결과는 0이어야 한다. | MUST |
| NSL-BLT-006 | Empty Money List의 처리 정책을 명시적으로 정의해야 한다. | MUST |
| NSL-BLT-007 | `count()`는 Collection 크기를 반환해야 한다. | MUST |
| NSL-BLT-008 | Built-in Error가 Skill PASS로 변환되어서는 안 된다. | MUST |
| NSL-BLT-009 | `coalesce()`는 Optional/Null/Missing Semantics가 확정될 때까지 v0.1 Baseline에서 비활성화해야 한다. | MUST |

---

# 13. REQUIRES 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-REQ-001 | Skill이 사용할 Tool을 `requires`에 선언해야 한다. | MUST |
| NSL-REQ-002 | 선언하지 않은 Tool을 호출할 수 없어야 한다. | MUST |
| NSL-REQ-003 | Compile 시 Tool Registry에서 Tool 존재 여부를 확인할 수 있어야 한다. | MUST |
| NSL-REQ-004 | Tool Version Compatibility를 확인할 수 있어야 한다. | SHOULD |
| NSL-REQ-005 | v0.1 Skill은 READ Capability Tool만 사용할 수 있어야 한다. | MUST |
| NSL-REQ-006 | WRITE Tool이 발견되면 Compile Error를 발생시켜야 한다. | MUST |
| NSL-REQ-007 | Compiler는 Canonical Business Tool Contract를 Resolve해야 한다. | MUST |
| NSL-REQ-008 | 고객별 MCP Tool Binding/Endpoint는 Compile 단계에서 Resolve하지 않아야 한다. | MUST |

---

# 14. READ 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-READ-001 | `read`는 Registered Tool만 호출해야 한다. | MUST |
| NSL-READ-002 | Tool Parameter 이름과 Type을 검증해야 한다. | MUST |
| NSL-READ-003 | Tool Result는 Structured Data여야 한다. | MUST |
| NSL-READ-004 | Tool Result Schema를 Tool Contract를 통해 확인해야 한다. | MUST |
| NSL-READ-005 | Tool Error를 Empty Result로 변환해서는 안 된다. | MUST |
| NSL-READ-006 | Tool Timeout을 명시적 TOOL_ERROR로 처리해야 한다. | MUST |
| NSL-READ-007 | Tool Invocation마다 Input/Output을 Audit에 기록해야 한다. | MUST |
| NSL-READ-008 | Tool Result Hash를 생성할 수 있어야 한다. | MUST |

---

# 15. FOREACH 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-FOR-001 | `foreach`에는 반드시 `max`가 존재해야 한다. | MUST |
| NSL-FOR-002 | max 값은 양의 정수여야 한다. | MUST |
| NSL-FOR-003 | Runtime은 실제 반복횟수를 추적해야 한다. | MUST |
| NSL-FOR-004 | max 초과 시 `LIMIT_EXCEEDED`로 중단해야 한다. | MUST |
| NSL-FOR-005 | 무한 반복을 구성할 수 없어야 한다. | MUST |
| NSL-FOR-006 | 재귀적 foreach 실행구조를 제한할 수 있어야 한다. | SHOULD |
| NSL-FOR-007 | foreach 실행 순서는 Deterministic해야 한다. | MUST |
| NSL-FOR-008 | v1.0에서 foreach Parallel Execution을 지원하지 않아야 한다. | MUST |

---

# 16. LET 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-LET-001 | `let`은 Immutable Binding을 생성해야 한다. | MUST |
| NSL-LET-002 | 이미 Binding된 이름을 재할당할 수 없어야 한다. | MUST |
| NSL-LET-003 | Expression은 Side Effect가 없어야 한다. | MUST |
| NSL-LET-004 | Expression 평가 결과 Type을 보존해야 한다. | MUST |
| NSL-LET-005 | Expression Evaluation Error는 Skill 실행 실패 또는 UNKNOWN 처리정책에 따라 처리되어야 한다. | MUST |

---

# 17. CHECK / Validation 요구사항

CHECK Result:

```text
PASS
FAIL
UNKNOWN
```

Validator Mapping:

```text
PASS       → SAT
FAIL       → UNSAT
UNKNOWN    → UNKNOWN
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-VAL-001 | CHECK는 Bool Expression을 평가해야 한다. | MUST |
| NSL-VAL-002 | TRUE는 PASS로 변환해야 한다. | MUST |
| NSL-VAL-003 | FALSE는 FAIL로 변환해야 한다. | MUST |
| NSL-VAL-004 | 판단 불가능 상태를 UNKNOWN으로 표현할 수 있어야 한다. | MUST |
| NSL-VAL-005 | Tool Error를 PASS로 변환해서는 안 된다. | MUST |
| NSL-VAL-006 | 불완전한 Source Data를 PASS로 간주해서는 안 된다. | MUST |
| NSL-VAL-007 | CHECK 결과와 사용된 Fact를 Audit에 저장해야 한다. | MUST |
| NSL-VAL-008 | CHECK Message를 Runtime Result에 포함해야 한다. | MUST |
| NSL-VAL-009 | v1.0의 단순 CHECK는 Rule Evaluator가 처리해야 한다. | MUST |
| NSL-VAL-010 | 향후 NeX Reasoning Validator Adapter로 확장할 수 있어야 한다. | SHOULD |
| NSL-VAL-011 | `NSL-0.1-STRICT`에서 COMPLETE+TRUE만 PASS, COMPLETE+FALSE만 FAIL로 판단해야 한다. | MUST |
| NSL-VAL-012 | PARTIAL 또는 UNKNOWN Completeness의 Predicate는 CHECK UNKNOWN으로 처리해야 한다. | MUST |
| NSL-VAL-013 | UNKNOWN을 PASS로 변환하는 설정을 v0.1에서 제공하지 않아야 한다. | MUST |

---

# 18. False PASS 방지 요구사항

본 항목은 v1.0의 핵심 안전 요구사항이다.

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-FP-001 | Tool 실패를 정상 Empty Collection으로 처리해서는 안 된다. | MUST |
| NSL-FP-002 | Required Tool Result가 누락된 경우 CHECK PASS를 생성해서는 안 된다. | MUST |
| NSL-FP-003 | Partial Result 여부를 Runtime이 추적할 수 있어야 한다. | MUST |
| NSL-FP-004 | Required Data가 Partial인 경우 해당 CHECK는 UNKNOWN 또는 Execution Failure가 되어야 한다. | MUST |
| NSL-FP-005 | Data Completeness 상태가 Audit에 기록되어야 한다. | MUST |
| NSL-FP-006 | UNKNOWN을 PASS로 자동 변환해서는 안 된다. | MUST |

---

# 19. EMIT 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-EMT-001 | `emit`은 Typed Structured Record를 생성해야 한다. | MUST |
| NSL-EMT-002 | Output Schema와 Emit Record Schema가 일치해야 한다. | MUST |
| NSL-EMT-003 | 여러 emit 결과를 List 형태로 반환할 수 있어야 한다. | MUST |
| NSL-EMT-004 | emitted_rows Limit을 적용해야 한다. | MUST |
| NSL-EMT-005 | Money, Date 등 Type 정보를 결과에 유지해야 한다. | MUST |
| NSL-EMT-006 | Result Serialization을 지원해야 한다. | MUST |

---

# 20. LIMITS 요구사항

지원 Limit:

```text
tool_calls
loop_iterations
emitted_rows
duration
collection_size
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-LIM-001 | Skill은 실행 Resource Limit을 정의할 수 있어야 한다. | MUST |
| NSL-LIM-002 | Runtime은 Tool Call 횟수를 추적해야 한다. | MUST |
| NSL-LIM-003 | Runtime은 Loop Iteration을 추적해야 한다. | MUST |
| NSL-LIM-004 | Runtime은 Emitted Row 수를 추적해야 한다. | MUST |
| NSL-LIM-005 | Runtime은 전체 실행시간을 제한해야 한다. | MUST |
| NSL-LIM-006 | Runtime은 Collection Size Limit을 지원해야 한다. | SHOULD |
| NSL-LIM-007 | Limit 초과 시 `LIMIT_EXCEEDED` 상태를 반환해야 한다. | MUST |
| NSL-LIM-008 | Resource Limit 초과를 CHECK FAIL로 표현해서는 안 된다. | MUST |

---

# 21. Resource Bound Static Analysis

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-BND-001 | Compiler는 가능한 경우 최대 Tool Call 수를 계산해야 한다. | MUST |
| NSL-BND-002 | foreach max를 Resource 분석에 사용해야 한다. | MUST |
| NSL-BND-003 | 선언된 tool_calls보다 정적 최대값이 큰 경우 Warning/Error를 생성해야 한다. | MUST |
| NSL-BND-004 | 무한 실행 가능성이 있는 구조를 Compile 단계에서 거부해야 한다. | MUST |
| NSL-BND-005 | Bound 분석 결과를 `.nso`에 포함해야 한다. | SHOULD |

예:

```text
READ Parent                  1

FOREACH parent MAX 100
 └─ READ Children          100

Maximum Tool Calls          101
```

---

# 22. `.nso` Intermediate Representation

`.nso`는 v1.0에서 **Canonical JSON 기반 Typed IR**로 구현하는 것을 기본안으로 한다.

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-IR-001 | `.ns` Source를 `.nso`로 Compile할 수 있어야 한다. | MUST |
| NSL-IR-002 | `.nso`는 Source 없이 실행 가능해야 한다. | MUST |
| NSL-IR-003 | `.nso`는 Language Version을 포함해야 한다. | MUST |
| NSL-IR-004 | Skill ID와 Version을 포함해야 한다. | MUST |
| NSL-IR-005 | Typed Statement/Expression 구조를 포함해야 한다. | MUST |
| NSL-IR-006 | Required Tool 목록을 포함해야 한다. | MUST |
| NSL-IR-007 | Limits를 포함해야 한다. | MUST |
| NSL-IR-008 | Output Schema를 포함해야 한다. | MUST |
| NSL-IR-009 | `.nso` Canonical Serialization 결과가 동일 입력에서 동일해야 한다. | MUST |
| NSL-IR-010 | `.nso` Hash를 생성할 수 있어야 한다. | MUST |
| NSL-IR-011 | Runtime이 malformed `.nso`를 거부해야 한다. | MUST |
| NSL-IR-012 | `.nso`에는 `include` 또는 Include AST Node가 존재해서는 안 된다. | MUST |
| NSL-IR-013 | Bool Literal은 Typed IR Literal로 정규화되어야 한다. | MUST |
| NSL-IR-014 | `.nso` Build Metadata에는 Root/Included Source Manifest를 기록해야 한다. | MUST |
| NSL-IR-015 | `source_bundle_sha256`과 `semantic_sha256`을 분리해야 한다. | MUST |
| NSL-IR-016 | 동일 실행 의미이면 Source를 include로 분할하더라도 Semantic Hash는 동일하게 유지되어야 한다. | SHOULD |

초기 `.nso` 예시:

```json
{
  "format": "NSO",
  "ir_version": "1.0",
  "language": {"name": "NSL", "version": "0.1"},
  "skill": {
    "id": "FINANCE.PROJECT_BUDGET_CHECK",
    "version": "1.0.0",
    "risk": "READ_VALIDATE"
  },
  "requires": [
    {
      "tool_ref": "tool0001",
      "tool_id": "PROJECT.LIST_PARENT_PROJECTS",
      "capability": "READ",
      "contract_hash": "sha256:..."
    }
  ],
  "body": [],
  "hashes": {
    "source_bundle_sha256": "sha256:...",
    "semantic_sha256": "sha256:..."
  }
}
```

---

# 23. `.nsp` Package 요구사항

기본 Package:

```text
finance-budget.nsp

├─ manifest.json
├─ skill.nso
├─ tool-contracts.json
├─ metadata.json
├─ tests/
└─ signature.json
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-PKG-001 | `.nsp` Package를 생성할 수 있어야 한다. | SHOULD |
| NSL-PKG-002 | Package에 최소 하나의 `.nso`가 포함되어야 한다. | MUST |
| NSL-PKG-003 | Package Manifest를 지원해야 한다. | MUST |
| NSL-PKG-004 | Skill ID/Version을 Manifest에 저장해야 한다. | MUST |
| NSL-PKG-005 | Package Hash를 생성할 수 있어야 한다. | SHOULD |
| NSL-PKG-006 | Signature Metadata 구조를 지원해야 한다. | SHOULD |
| NSL-PKG-007 | v1.0 Development 환경에서 Unsigned Package를 허용할 수 있다. | MAY |
| NSL-PKG-008 | Production에서는 Signature Required 정책으로 확장할 수 있어야 한다. | MUST |

`.nse`는 v1.0에서 구현하지 않는다.

---

# 24. Runtime Engine 요구사항

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

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-RT-001 | `.ns` Source 실행은 Compiler가 생성한 in-memory `SkillObject`를 Runtime에 전달하는 방식으로 지원해야 한다. | MUST |
| NSL-RT-002 | `.nso`를 직접 실행할 수 있어야 한다. | MUST |
| NSL-RT-003 | Runtime은 Execution Context를 생성해야 한다. | MUST |
| NSL-RT-004 | Input/Context/Variable/Check/Emit 공간을 분리해야 한다. | MUST |
| NSL-RT-005 | Statement를 Source Order대로 실행해야 한다. | MUST |
| NSL-RT-006 | Runtime 실행은 동일 Input/Tool Result에서 Deterministic해야 한다. | MUST |
| NSL-RT-007 | Python `eval()`을 사용하지 않아야 한다. | MUST |
| NSL-RT-008 | Python `exec()`를 사용하지 않아야 한다. | MUST |
| NSL-RT-009 | 임의 Python Object Reflection을 허용하지 않아야 한다. | MUST |
| NSL-RT-010 | 모든 Runtime Expression은 IR 기반 Evaluator가 직접 처리해야 한다. | MUST |
| NSL-RT-012 | Runtime은 Source AST를 직접 실행하는 별도 Interpreter 경로를 제공하지 않아야 한다. | MUST |
| NSL-RT-011 | 실행 결과에 Execution ID를 부여해야 한다. | MUST |

---

# 25. Tool Registry 요구사항

Tool Contract 예:

```text
Tool ID
Tool Version
Capability
  READ
Input Schema
Output Schema
Timeout
Risk Level
Provider
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-TOL-001 | Tool Registry를 제공해야 한다. | MUST |
| NSL-TOL-002 | Tool ID는 전역적으로 유일해야 한다. | MUST |
| NSL-TOL-003 | Tool Capability를 관리해야 한다. | MUST |
| NSL-TOL-004 | Tool Input/Output Schema를 관리해야 한다. | MUST |
| NSL-TOL-005 | Tool Version을 관리해야 한다. | MUST |
| NSL-TOL-006 | Runtime 실행 전 required Tool을 Resolve해야 한다. | MUST |
| NSL-TOL-007 | Tool Contract 불일치 시 실행을 거부해야 한다. | MUST |
| NSL-TOL-008 | Tool Registry는 MCP 구현 세부정보를 NSL Source에 노출하지 않아야 한다. | MUST |

---

# 26. Tool Executor 요구사항

기본 Interface:

```text
ToolExecutor
 ├─ MockToolExecutor
 └─ MCPToolExecutor
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-EXE-001 | ToolExecutor Interface를 정의해야 한다. | MUST |
| NSL-EXE-002 | MockToolExecutor를 제공해야 한다. | MUST |
| NSL-EXE-003 | MCPToolExecutor Adapter 구조를 제공해야 한다. | MUST |
| NSL-EXE-004 | Runtime이 MCP 구현체에 직접 종속되지 않아야 한다. | MUST |
| NSL-EXE-005 | Tool 호출 전에 Contract Validation을 수행해야 한다. | MUST |
| NSL-EXE-006 | Tool 실행시간을 측정해야 한다. | SHOULD |
| NSL-EXE-007 | Tool Invocation ID를 생성해야 한다. | MUST |

---

# 27. Audit / Trace 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-AUD-001 | 모든 Skill Execution을 Audit해야 한다. | MUST |
| NSL-AUD-002 | Skill ID/Version을 기록해야 한다. | MUST |
| NSL-AUD-003 | `.nso` Hash를 기록해야 한다. | MUST |
| NSL-AUD-004 | Runtime Version을 기록해야 한다. | MUST |
| NSL-AUD-005 | Input과 Context를 기록해야 한다. | MUST |
| NSL-AUD-006 | Tool Invocation Input을 기록해야 한다. | MUST |
| NSL-AUD-007 | Tool Result Snapshot 또는 Reference를 기록해야 한다. | MUST |
| NSL-AUD-008 | Tool Result Hash를 기록해야 한다. | MUST |
| NSL-AUD-009 | CHECK 결과를 기록해야 한다. | MUST |
| NSL-AUD-010 | EMIT 결과를 기록해야 한다. | MUST |
| NSL-AUD-011 | Error 발생 위치와 원인을 기록해야 한다. | MUST |
| NSL-AUD-012 | CONFIDENTIAL/RESTRICTED 원문은 일반 Trace/Audit Event에 기록하지 않아야 한다. | MUST |
| NSL-AUD-013 | 보호 대상 원문은 Secure Snapshot Reference, Hash, Classification으로 Audit해야 한다. | MUST |
| NSL-AUD-014 | Principal과 Authorization Decision Reference를 기록해야 한다. | MUST |

---

# 28. Deterministic Replay 요구사항

```text
Captured Input
      +
Captured Context
      +
Captured Tool Results
      +
Same .nso
      ↓
    Runtime
      ↓
 Same Result
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-RPL-001 | 이전 실행을 Replay할 수 있어야 한다. | MUST |
| NSL-RPL-002 | Replay에서는 실제 MCP를 호출하지 않아야 한다. | MUST |
| NSL-RPL-003 | 이전 Tool Snapshot을 사용해야 한다. | MUST |
| NSL-RPL-004 | 동일 `.nso`, Input, Tool Snapshot에서 동일 결과를 생성해야 한다. | MUST |
| NSL-RPL-005 | Replay Result와 Original Result를 자동 비교할 수 있어야 한다. | MUST |
| NSL-RPL-006 | 불일치 시 Difference를 보고해야 한다. | MUST |
| NSL-RPL-007 | Runtime Version 차이를 기록해야 한다. | SHOULD |

---

# 29. CLI 요구사항

v1.0 CLI Command:

```bash
nsl parse
nsl check
nsl compile
nsl inspect
nsl run
nsl replay
nsl test
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-CLI-001 | `nsl parse <file.ns>`를 제공해야 한다. | MUST |
| NSL-CLI-002 | `nsl check <file.ns>`를 제공해야 한다. | MUST |
| NSL-CLI-003 | `nsl compile <file.ns> -o <file.nso>`를 제공해야 한다. | MUST |
| NSL-CLI-004 | `nsl inspect <file.nso>`를 제공해야 한다. | SHOULD |
| NSL-CLI-005 | `nsl run <file.ns>`를 지원해야 한다. | MUST |
| NSL-CLI-006 | `nsl run <file.nso>`를 지원해야 한다. | MUST |
| NSL-CLI-007 | Input JSON File을 지정할 수 있어야 한다. | MUST |
| NSL-CLI-008 | Context JSON File을 지정할 수 있어야 한다. | MUST |
| NSL-CLI-009 | Mock Tool Fixture를 지정할 수 있어야 한다. | MUST |
| NSL-CLI-010 | `nsl replay`를 지원해야 한다. | MUST |
| NSL-CLI-011 | CLI Return Code를 일관되게 제공해야 한다. | MUST |

---

# 30. Diagnostic / Error 요구사항

Error Category:

```text
NSL-E1xxx Syntax
NSL-E2xxx Semantic
NSL-E3xxx Type
NSL-E4xxx Tool
NSL-E5xxx Safety
NSL-E6xxx Resource
NSL-E7xxx Output
NSL-E8xxx Runtime
NSL-E9xxx Package
```

대표 Error:

```text
NSL-E1001 Syntax Error
NSL-E2001 Unknown Identifier
NSL-E2201 Undeclared Tool
NSL-E3001 Type Mismatch
NSL-E3101 Currency Mismatch
NSL-E4001 Tool Contract Mismatch
NSL-E4101 Tool Execution Failure
NSL-E5001 WRITE Tool Forbidden
NSL-E5101 Unbounded Foreach
NSL-E6001 Tool Call Limit Exceeded
NSL-E7001 Output Schema Mismatch
NSL-E8001 Runtime Evaluation Error
NSL-E9001 Invalid Package
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-ERR-001 | 모든 Error는 고유 Error Code를 가져야 한다. | MUST |
| NSL-ERR-002 | Source Error는 Line/Column을 포함해야 한다. | MUST |
| NSL-ERR-003 | 가능하면 Source Snippet을 표시해야 한다. | SHOULD |
| NSL-ERR-004 | Error Message와 Internal Exception을 분리해야 한다. | MUST |
| NSL-ERR-005 | Python Stack Trace를 일반 사용자에게 직접 노출하지 않아야 한다. | MUST |
| NSL-ERR-006 | Debug Mode에서 상세 Stack Trace를 제공할 수 있어야 한다. | SHOULD |

---

# 31. Security 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-SEC-001 | `eval()` 사용을 금지해야 한다. | MUST |
| NSL-SEC-002 | `exec()` 사용을 금지해야 한다. | MUST |
| NSL-SEC-003 | 임의 Module Import를 금지해야 한다. | MUST |
| NSL-SEC-004 | 임의 filesystem 접근을 금지해야 한다. | MUST |
| NSL-SEC-005 | 임의 network 접근을 금지해야 한다. | MUST |
| NSL-SEC-006 | 임의 SQL 실행을 금지해야 한다. | MUST |
| NSL-SEC-007 | Registered Tool만 외부 접근에 사용할 수 있어야 한다. | MUST |
| NSL-SEC-008 | Resource Limit을 우회할 수 없어야 한다. | MUST |
| NSL-SEC-009 | `.nso` 입력을 신뢰하지 않고 Schema Validation해야 한다. | MUST |
| NSL-SEC-010 | `.nsp` Path Traversal 공격을 방지해야 한다. | MUST |
| NSL-SEC-011 | Production Package Signature 검증을 지원할 수 있어야 한다. | MUST |
| NSL-SEC-012 | Production 실행은 Tenant와 Subject를 포함한 검증된 Execution Principal을 요구해야 한다. | MUST |
| NSL-SEC-013 | 예약/이벤트 실행은 명시적인 Service Principal을 사용해야 한다. | MUST |
| NSL-SEC-014 | Skill 실행과 Tool 호출은 Default Deny Authorization 정책을 적용해야 한다. | MUST |
| NSL-SEC-015 | Tool 호출은 Required Scope와 Authorization Decision을 검증해야 한다. | MUST |
| NSL-SEC-016 | Credential 원문을 `.nso`, ExecutionContext, Trace에 저장하지 않아야 한다. | MUST |
| NSL-SEC-017 | Data Classification을 PUBLIC/INTERNAL/CONFIDENTIAL/RESTRICTED로 표현할 수 있어야 한다. | MUST |
| NSL-SEC-018 | CONFIDENTIAL/RESTRICTED Replay Snapshot은 암호화 저장해야 한다. | MUST |
| NSL-SEC-019 | Snapshot Store는 Tenant 격리와 접근 통제를 제공해야 한다. | MUST |
| NSL-SEC-020 | Audit/Replay Data의 보존기간과 삭제 정책을 적용할 수 있어야 한다. | MUST |
| NSL-SEC-021 | Error와 Diagnostic에서 Credential 및 민감값을 Redact해야 한다. | MUST |
| NSL-SEC-022 | Replay Data 열람 권한을 실행 권한과 별도로 검증해야 한다. | MUST |

---

# 32. Performance 요구사항

v1.0에서는 Language Engine 자체 성능보다 외부 Tool 호출시간이 전체 실행시간의 대부분을 차지할 것으로 가정한다.

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-PERF-001 | 소규모 `.ns` 파일 Parsing은 일반 개발환경에서 즉시 응답 수준이어야 한다. | SHOULD |
| NSL-PERF-002 | `.nso` Loading은 Source Compile보다 빨라야 한다. | SHOULD |
| NSL-PERF-003 | Runtime 자체 Overhead를 측정할 수 있어야 한다. | SHOULD |
| NSL-PERF-004 | Tool 실행시간과 Runtime 계산시간을 분리 측정해야 한다. | SHOULD |
| NSL-PERF-005 | 1,000개 수준의 Bounded foreach 테스트를 지원해야 한다. | SHOULD |

---

# 33. Reliability 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-REL-001 | 동일 `.nso`와 동일 Input에서 동일 Expression 결과를 보장해야 한다. | MUST |
| NSL-REL-002 | Runtime Crash가 전체 NeX-AE API Process를 종료시키지 않아야 한다. | MUST |
| NSL-REL-003 | Skill Runtime Error를 구조화된 Result로 반환해야 한다. | MUST |
| NSL-REL-004 | Tool Failure 시 명시적 실패 상태를 반환해야 한다. | MUST |
| NSL-REL-005 | Partial Execution 결과를 정상 COMPLETE로 처리하지 않아야 한다. | MUST |

---

# 34. NeX-AE Integration 요구사항

```text
User
 │
 ▼
NeX-AE-WEB
 │
 ▼
NeX-AE-API
 │
 ├─ Intent Analyzer
 ├─ Skill Router
 └─ Skill Execution Request
             │
             ▼
        Job Queue
             │
             ▼
      NeX-AE Worker
             │
             ▼
       NSL Runtime
             │
             ▼
        MCP Gateway
```

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-AE-001 | NeX-AE가 Skill ID를 이용해 Runtime 실행을 요청할 수 있어야 한다. | MUST |
| NSL-AE-002 | Input Parameter를 Structured Data로 전달해야 한다. | MUST |
| NSL-AE-003 | Runtime Context를 별도 객체로 전달해야 한다. | MUST |
| NSL-AE-004 | 장기 실행은 NeX-AE API Request Thread에서 직접 수행하지 않아야 한다. | MUST |
| NSL-AE-005 | Skill 실행을 NeX-AE Worker가 수행할 수 있어야 한다. | MUST |
| NSL-AE-006 | Execution Progress를 NeX-AE에 제공할 수 있어야 한다. | SHOULD |
| NSL-AE-007 | Structured Result를 NeX-AE가 자연어 설명에 사용할 수 있어야 한다. | MUST |
| NSL-AE-008 | NSL Runtime 결과와 LLM 생성 설명을 분리 저장할 수 있어야 한다. | MUST |
| NSL-AE-009 | NeX-AE는 검증된 Execution Principal과 Authorization Context를 실행 요청에 포함해야 한다. | MUST |
| NSL-AE-010 | NeX-AE는 실행 요청에 Data Handling Policy를 포함해야 한다. | MUST |
| NSL-AE-011 | 입력값의 출처를 user/context/default 중 하나로 추적할 수 있어야 한다. | SHOULD |

---

# 35. Python 구현 요구사항

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-PY-001 | Runtime은 Python으로 구현해야 한다. | MUST |
| NSL-PY-002 | Language Core는 Web Framework에 종속되지 않아야 한다. | MUST |
| NSL-PY-003 | AST Node는 명시적인 Python Class/Data Class로 구현해야 한다. | MUST |
| NSL-PY-004 | IR Model은 Schema Validation 가능한 구조여야 한다. | MUST |
| NSL-PY-005 | Decimal 계산은 Python `decimal.Decimal`을 사용해야 한다. | MUST |
| NSL-PY-006 | Runtime Core에서 비동기 Framework 의존성을 최소화해야 한다. | SHOULD |
| NSL-PY-007 | MCP Adapter는 Runtime Core와 분리해야 한다. | MUST |

---

# 36. 권장 Python Package 구조

Compiler와 Runtime이 동일 정의를 공유하도록 `core/`와 `ir/`를 Single Source of Truth로 사용한다.

```text
nsl/
├─ core/
│  ├─ ids.py
│  ├─ types.py
│  ├─ money.py
│  ├─ values.py
│  ├─ data_quality.py
│  ├─ checks.py
│  ├─ capabilities.py
│  └─ errors.py
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
├─ compiler/
│  ├─ api.py
│  ├─ source/
│  ├─ lexical/
│  ├─ syntax/
│  ├─ semantic/
│  ├─ analysis/
│  ├─ lowering/
│  └─ diagnostics/
├─ tools/
├─ validation/
├─ runtime/
├─ audit/
├─ replay/
├─ package/
├─ api/
└─ cli/
```

Dependency 원칙:

```text
core → Python Standard Library only
ir → core
compiler → core + ir + tools.contract/catalog
runtime → core + ir + tools protocol + validation + audit protocol
compiler ↔ runtime 직접 의존 금지
runtime → compiler AST 의존 금지
```

Architecture Dependency Rule은 자동 Test로 검증한다.

# 37. Testing 전략

## 37.1 Coverage 목표

| 영역 | Statement | Branch |
|---|---:|---:|
| 전체 | ≥ 95% | ≥ 90% |
| Parser | ≥ 95% | ≥ 90% 권장 |
| Type Checker | ≥ 95% | ≥ 90% 권장 |
| Safety Checker | ≥ 95% | ≥ 90% 권장 |
| Runtime Evaluator | ≥ 95% | ≥ 90% 권장 |
| CHECK Evaluator | ≥ 95% | ≥ 90% 권장 |

## 37.2 테스트 종류

| ID | 요구사항 | 우선순위 |
|---|---|---|
| NSL-TST-001 | Lexer Unit Test를 제공해야 한다. | MUST |
| NSL-TST-002 | Parser Golden Test를 제공해야 한다. | MUST |
| NSL-TST-003 | Parser Negative Test를 제공해야 한다. | MUST |
| NSL-TST-004 | Type Checker Test를 제공해야 한다. | MUST |
| NSL-TST-005 | Money Boundary Test를 제공해야 한다. | MUST |
| NSL-TST-006 | Bounded Loop Test를 제공해야 한다. | MUST |
| NSL-TST-007 | Tool Failure Test를 제공해야 한다. | MUST |
| NSL-TST-008 | False PASS 방지 Test를 제공해야 한다. | MUST |
| NSL-TST-009 | Resource Limit Test를 제공해야 한다. | MUST |
| NSL-TST-010 | Replay Test를 제공해야 한다. | MUST |
| NSL-TST-011 | Malformed `.nso` Test를 제공해야 한다. | MUST |
| NSL-TST-012 | Package Tamper Test를 제공해야 한다. | SHOULD |
| NSL-TST-013 | Regression Test를 자동화해야 한다. | MUST |
| NSL-TST-014 | Include Resolution/Cycle/Diamond/Path Traversal/Limit Test를 제공해야 한다. | MUST |
| NSL-TST-015 | `true/false` Bool Literal 및 Implicit Truthiness Negative Test를 제공해야 한다. | MUST |
| NSL-TST-016 | Compiler/Runtime Package Dependency Rule을 검증하는 Architecture Test를 제공해야 한다. | MUST |
| NSL-TST-017 | 동일 Source Bundle 반복 Compile에서 동일 Symbol/Node ID와 Semantic Hash를 검증해야 한다. | MUST |
| NSL-TST-018 | Principal 누락과 Required Scope 부족 시 실행 거부 Test를 제공해야 한다. | MUST |
| NSL-TST-019 | 민감 데이터가 Trace/Audit Event에 노출되지 않는 Test를 제공해야 한다. | MUST |
| NSL-TST-020 | Replay Snapshot Tenant 격리와 권한 Test를 제공해야 한다. | MUST |

---

# 38. Test Case 설계 원칙

NSL Built-in 및 Runtime Test는 다음 기법을 적용한다.

```text
Boundary Value Analysis
Robustness Testing
Worst-case Testing
Equivalence Partitioning
Golden Test
Regression Test
Fault Injection
```

특히 Money, Loop, Resource Limit에는 Boundary Value Analysis를 우선 적용한다.

예:

```text
foreach max

0
1
MAX-1
MAX
MAX+1
```

Tool Failure:

```text
Success
Empty Result
Timeout
Malformed Result
Partial Result
Connection Error
```

---

# 39. v1.0 Acceptance Vertical Slice

대표 Skill:

```text
FINANCE.PROJECT_BUDGET_CHECK
```

Source:

```text
project_budget_check.ns
```

실행 Scenario:

```text
1. .ns Parse
2. Static Validation
3. .nso Generate
4. Runtime Load
5. ERP.GET_PARENT_PROJECTS
6. ERP.GET_CHILD_PROJECTS
7. Child Expense SUM
8. CHECK
   spent <= budget
9. EMIT
10. Audit
11. Replay
```

Test Data:

```text
Parent Budget
100,000,000 KRW

Child Expense
30,000,000
25,000,000
32,500,000

Total
87,500,000
```

Expected:

```text
spent
87,500,000 KRW

remaining
12,500,000 KRW

check
PASS

execution
COMPLETED
```

---

# 40. Mandatory Negative Acceptance Cases

## AC-N01 Syntax Error

잘못된 Source는 실행되지 않아야 한다.

## AC-N02 Undeclared Tool

`requires`에 없는 Tool 호출은 Compile Error가 되어야 한다.

## AC-N03 WRITE Tool

READ_VALIDATE Skill에서 WRITE Tool은 Compile Error가 되어야 한다.

## AC-N04 Unbounded foreach

```nsl
foreach item in items {
}
```

은 Compile Error가 되어야 한다.

## AC-N05 Currency Mismatch

```text
KRW + USD
```

는 실패해야 한다.

## AC-N06 Tool Failure

Tool Failure를 Empty Result로 처리해서는 안 된다.

## AC-N07 Partial Result

Partial Result로 정상 PASS를 생성해서는 안 된다.

## AC-N08 Budget Exceeded

```text
spent > budget
```

이면 CHECK FAIL이어야 한다.

## AC-N09 Limit Exceeded

Resource Limit을 초과하면 `LIMIT_EXCEEDED`이어야 한다.

## AC-N10 Replay

Captured Tool Results를 사용한 Replay는 원본과 동일 결과를 생성해야 한다.

---

# 41. 개발 단계

## Phase 0 — Shared Core / IR 기반

```text
Package Directory
Architecture Dependency Test
core/ Type Model
ir/ Object Model
```

완료 기준: Compiler와 Runtime이 동일 `core/`/`ir/` 정의를 사용한다.

## Phase 1 — Compiler Front-end

```text
Source / SourceSpan / Diagnostic
Token / Lexer / Bool Literal
AST / Parser
IncludeResolver / DependencyGraph / SourceComposer
```

완료 기준: Root `.ns` + Include Fragment → Combined AST

## Phase 2 — Compiler Semantic / Static Analysis

```text
Declaration / Symbol / Name Resolution
Canonical Tool Contract Resolution
Type System / Money / Bool
Built-in Signature
Semantic / Safety / Resource Bound
```

완료 기준: Combined AST → Validated SemanticModel

## Phase 3 — Lowering / IR

```text
Lowering / Normalization
Deterministic Symbol/Node ID
SkillObject
IR Validator / NsoCodec
Source Bundle Hash / Semantic Hash
```

완료 기준: `.ns` Source Bundle → `.nso`

## Phase 4 — Runtime State / Execution

```text
ExecutionContext / FrameStack
Expression / Statement Executor
LET / FOREACH / CHECK / EMIT
Resource Guard
```

완료 기준: SkillObject/`.nso` → Structured Result

## Phase 5 — Tool Runtime

```text
Canonical Tool Contract
Tool Resolver
Mock Tool
MCP Adapter
```

## Phase 6 — Audit / Replay

```text
Trace / Provenance / Snapshot / Replay
```

## Phase 7 — Package / CLI

```text
.nsp
CLI check / compile / run / replay
Package Verification
```

## Phase 8 — NeX-AE Integration

```text
Skill Router / Job Queue / Worker / SSE / ExecutionResult
```

# 42. v0.2 Extension Point

v1.0 Runtime은 다음 확장이 Architecture 변경 없이 추가 가능해야 한다.

```text
and / or / not
exists / any / all
filter / where
tolerance comparison
UNKNOWN propagation 강화
abs()
round()
group / aggregate
Optional<T> / Null / Missing
coalesce()
explicit include override semantics
```

Stage 5 대응 확장:

```text
approve
write
idempotency
rollback
```

Stage 6 대응:

```text
schedule
watch
delegate
invoke_skill
invoke_agent
```

---

# 43. Traceability 기본 구조

Requirement와 Test는 다음 형태로 연결한다.

```text
Requirement
     │
     ▼
Design
     │
     ▼
Implementation
     │
     ▼
Test Case
```

예:

```text
NSL-FOR-001
    ↓
ForeachStatement
    ↓
runtime/statements.py
    ↓
TEST-FOR-001
TEST-FOR-002
TEST-FOR-BVA-001
```

SRS v1.1부터 Requirement-to-Test Traceability를 유지하고 기존 v1.0 Requirement ID를 가능한 한 보존한다.

---

# 44. 핵심 설계 결정 요약

| 항목 | v1.0 결정 |
|---|---|
| 구현 언어 | Python |
| Source | `.ns` |
| IR/Object | `.nso` |
| Distribution Package | `.nsp` |
| Executable | `.nse` 예약 |
| Parser | Hand-written Recursive Descent + Pratt/Precedence Climbing |
| Runtime Expression | IR 직접 평가; AST Interpreter 없음 |
| `eval/exec` | 금지 |
| Variable | Immutable |
| Loop | Bounded foreach |
| Tool | Canonical Business Tool Contract + Runtime Binding |
| IR | Shared `ir/SkillObject` + Canonical JSON `.nso` |
| Money | Decimal + Currency |
| Validation | PASS / FAIL / UNKNOWN |
| Tool Error | 정상 데이터로 변환 금지 |
| False PASS | 핵심 Safety Requirement |
| Replay | v1.0 필수 |
| Include | Structured Source Composition; Runtime IR에는 미포함 |
| Bool Literal | `true` / `false`; Implicit Truthiness 금지 |
| Shared Model | `core/` Type + `ir/` Object를 Compiler/Runtime이 공동 사용 |
| Mock Runtime | 필수 |
| Runtime 배포 | Python Package + NeX-AE Worker |
| Microservice | 향후 선택 |
| Coverage | Statement ≥95%, Branch ≥90% |

---

# 45. 최종 Acceptance Definition

NSL Interpreter & Runtime Environment v1.0은 다음이 모두 만족될 때 완료로 판단한다.

```text
Root .ns + Included Source 작성
       ↓
Lexer / Parser
       ↓
Include Resolution / Source Composition
       ↓
Combined AST
       ↓
Static Validation / Type / Tool / Safety / Bound
       ↓
Lowering / Shared SkillObject
       ↓
.nso
       ↓
NSL Runtime
       ↓
Registered Business Tool
       ↓
FOREACH
       ↓
LET
       ↓
CHECK
       ↓
EMIT
       ↓
Structured Result
       ↓
Audit
       ↓
Replay
```

대표 회계·정산 Use Case인:

> **“모 프로젝트 예산 대비 자 프로젝트 지출 합계 검증”**

이 전체 Pipeline에서 정상 실행되어야 한다.

동시에 다음 상황에서 False PASS가 발생하지 않아야 한다.

```text
Tool Failure
Partial Result
Missing Required Data
Currency Mismatch
Resource Limit
Malformed IR
```

---

# 46. 최종 정의

NSL v0.1 Interpreter & Runtime Environment의 목적은 단순히 `.ns` Source를 실행하는 Interpreter를 만드는 것이 아니다.

본 시스템의 목적은 다음 전체 실행체계를 구축하는 것이다.

```text
Natural Language
       ↓
NeX-AE
       ↓
Skill Selection
       ↓
.ns
       ↓
Compile / Validate
       ↓
.nso
       ↓
NSL Runtime
       ↓
Registered Business Tool
       ↓
Deterministic Validation
       ↓
Structured Result
       ↓
Audit / Replay
```

즉,

> **Non-deterministic한 자연어 Agent와 Deterministic한 기업 업무시스템 사이에 검증 가능하고 재현 가능한 실행 계층을 구축하는 것**

이 NSL Interpreter & Runtime Environment v1.0의 핵심 목적이다.

---

# Appendix A. 구현 전 우선 검토 항목 — 현재 상태

초기 SRS v1.0에서 우선 검토 대상으로 정의했던 네 항목은 후속 Detailed Design에서 구체화되었다.

1. **`.nso` IR Schema** — 상세설계 완료 및 v0.2 개정
2. **ExecutionContext** — 상세설계 완료
3. **Tool Contract** — 상세설계 완료
4. **Validation Semantics** — 상세설계 완료

추가로 v1.1에서는 Compiler Detailed Design과 Shared Core/IR Single Source of Truth 설계를 Baseline으로 반영한다.

```text
Source / Include / Bool Literal
        ↓
Compiler Front-end
        ↓
Semantic / Tool / Type / Safety / Bound
        ↓
Shared core/ + ir/
        ↓
.nso
        ↓
Runtime
```

향후 구현은 `core/`와 `ir/`를 먼저 Skeleton Code로 고정한 후 Compiler와 Runtime을 동일 Vertical Slice에서 연결하는 방식을 권장한다.
