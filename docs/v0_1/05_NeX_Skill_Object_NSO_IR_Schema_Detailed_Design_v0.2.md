# NeX Skill Object (.nso)
## Intermediate Representation Schema Detailed Design v0.2 — Revised Baseline

**대상 Language:** NeX Skill Language(NSL) v0.1  
**문서 버전:** v0.2  
**개정일:** 2026-08-18  
**대상 Runtime:** NSL Interpreter & Runtime Environment v1.1  
**확장자:** `.nso`  
**명칭:** NeX Skill Object  
**초기 Encoding:** Canonical JSON  
**주요 목적:** 검증 완료된 NSL Skill의 Typed Execution IR

## 0. 개정 이력

| 문서 버전 | 일자 | 변경 내용 |
|---|---|---|
| v0.1 | 2026-08-14 | Canonical Typed IR, Symbol/Node ID, Tool Contract Fingerprint, Resource Bound, Hash 기본 구조 정의 |
| v0.2 | 2026-08-18 | `include` Source Composition의 IR 비노출 원칙, Bool Literal Lowering, Canonical Tool ID, Strict CHECK Data Policy, Source Manifest/Bundle Hash 반영 |

> 본 문서 버전은 v0.2이지만 `.nso` 실제 `ir_version`은 **1.0을 유지**한다. 이번 개정은 기존 Runtime Contract를 깨는 변경이 아니라 Source/Metadata/Semantics 정합성 보완이다.

---

## 1. 설계 목적

`.nso`는 NSL Source Code를 단순히 JSON으로 변환한 파일이 아니다.

> **`.nso`는 NSL Source를 Parsing, Symbol Resolution, Type Checking, Tool Contract Resolution, Safety Validation, Resource Bound Analysis한 후 생성되는 Runtime 실행용 Canonical Typed Intermediate Representation이다.**

전체 Pipeline은 다음과 같다.

```text
Root .ns Source
    │
    ▼
 Lexer / Parser
    │
    ▼
 Root AST
    │
    ▼
 Include Resolution / Included Source Parsing
    │
    ▼
 Source Composition
    │
    ▼
 Combined AST
    │
    ├─ Symbol Resolution
    ├─ Type Check
    ├─ Tool Contract Resolution
    ├─ Safety Check
    └─ Resource Bound Analysis
    │
    ▼
 Typed AST
    │
    ▼
 IR Generator
    │
    ▼
.nso
    │
    ▼
NSL Runtime
```

---

## 2. AST와 `.nso` IR의 경계

AST는 **Source Language 중심 구조**이고 `.nso`는 **Runtime 중심 구조**이다.

| 항목 | AST | `.nso` IR |
|---|---|---|
| Source 문법 구조 보존 | 중요 | 중요하지 않음 |
| Identifier 이름 | 그대로 유지 | Symbol ID로 Resolution |
| Type | 일부 미결정 가능 | 모두 결정 |
| Tool | 이름만 존재 가능 | Tool Contract Resolve 완료 |
| Default 값 | 생략 가능 | 명시적 값으로 Normalize |
| `children.amount` | Source 표현 유지 | Projection Node로 변환 |
| Resource Bound | 없음 | 계산 완료 |
| Safety 검증 | 진행 중 | 완료 |
| Runtime 실행 | 직접 실행 비권장 | 직접 실행 |
| Source 위치 | AST Node에 존재 | Debug Metadata로 분리 |

예를 들어 Source:

```nsl
let spent =
    sum(children.expense_amount);
```

AST:

```text
LetStatement
 ├─ name: spent
 └─ CallExpression
     ├─ function: sum
     └─ FieldReference
         ├─ children
         └─ expense_amount
```

IR:

```text
LET
 ├─ symbol_id: s0007
 └─ value:
     CALL
      ├─ builtin: SUM
      ├─ result_type: Money
      └─ argument:
          PROJECT
           ├─ source: s0006
           ├─ field: expense_amount
           └─ type: List<Money>
```

Runtime은 Source 변수명인 `spent`, `children`을 실행 lookup key로 사용하지 않는다.

### 2.1 `include`와 IR 경계

`include`는 Compiler Front-end 기능이며 Runtime Execution Semantics가 아니다.

```text
Root .ns + Included .ns Fragments
        ↓
Compiler Source Composition
        ↓
Combined AST
        ↓
Lowering
        ↓
.nso
```

따라서 `.nso`에는 `include`, `IncludeDeclaration`, `IncludeDependencyGraph` 같은 실행 Node가 존재해서는 안 된다. Include 정보는 Build Provenance의 Source Manifest로만 남길 수 있다. 동일 실행 의미를 한 파일로 작성했는지 여러 Include Fragment로 분리했는지는 `semantic_sha256`에 영향을 주지 않는 것을 원칙으로 한다.

---

## 3. `.nso` Top-Level Schema

권장 Top-Level 구조:

```json
{
  "format": "NSO",
  "ir_version": "1.0",
  "language": {},
  "skill": {},
  "semantics_profile": "NSL-0.1-STRICT",
  "features": [],
  "types": [],
  "symbols": [],
  "requires": [],
  "limits": {},
  "inputs": [],
  "contexts": [],
  "output": {},
  "body": [],
  "analysis": {},
  "hashes": {},
  "build": {},
  "debug": {}
}
```

Runtime 실행에 필요한 영역과 Provenance 영역을 구분한다.

```text
Execution Critical
────────────────────────
format
ir_version
language
skill
semantics_profile
features
types
symbols
requires
limits
inputs
contexts
output
body
analysis

Non-Execution / Provenance
────────────────────────
hashes
build
debug
```

---

## 4. Version Model

다음 세 Version을 분리한다.

```text
NSL Language Version
        0.1

NSO IR Version
        1.0

Skill Version
        1.0.0
```

Header 예:

```json
{
  "format": "NSO",
  "ir_version": "1.0",
  "language": {
    "name": "NSL",
    "version": "0.1"
  }
}
```

---

## 5. Skill Metadata

```json
{
  "skill": {
    "id": "FINANCE.PROJECT_BUDGET_CHECK",
    "version": "1.0.0",
    "risk": "READ_VALIDATE",
    "description": "모 프로젝트 예산과 자 프로젝트 지출 합계를 검증한다."
  }
}
```

필수 필드:

- `id`
- `version`
- `risk`

v0.1에서 지원하는 Risk:

```text
READ_ONLY
READ_VALIDATE
```

v0.1에서 다음 Risk/Capability는 허용하지 않는다.

```text
WRITE
TRANSACTION
APPROVAL
```

---

## 6. Semantics Profile

IR Schema Version과 Runtime 의미론을 분리한다.

```json
{
  "semantics_profile": "NSL-0.1-STRICT"
}
```

`NSL-0.1-STRICT` 기본 의미:

```text
Tool Error
    → ABORT

Required Partial Data
    → UNKNOWN 또는 Execution Error
    → PASS 금지

Unknown Check
    → UNKNOWN 유지
    → PASS 변환 금지

Money Currency Mismatch
    → ERROR

Variable
    → Immutable

FOREACH
    → Sequential
    → Bounded

WRITE
    → Forbidden
```

---

## 7. Feature Declaration

`.nso`에서 실제 사용하는 Language Feature를 선언한다.

```json
{
  "features": [
    "READ",
    "FOREACH",
    "LET",
    "CHECK",
    "EMIT",
    "LIMITS"
  ]
}
```

Runtime은 모든 Required Feature를 지원하는지 Load 시 검사한다.

`include`는 Compile-time Source Composition 기능이므로 `.nso.features`에 포함하지 않는다. `true/false`도 별도 Runtime Feature가 아니라 `Bool` Typed Literal로 Lowering된다.

향후 예:

```text
BOOLEAN_LOGIC
FILTER
ANY_ALL
TOLERANCE
```

---

## 8. Type Representation

IR Type은 단순 문자열보다 구조화된 Object로 표현하는 것을 권장한다.

### 8.1 Primitive

```json
{
  "kind": "primitive",
  "name": "String"
}
```

### 8.1.1 Bool Primitive

```json
{
  "kind": "primitive",
  "name": "Bool"
}
```

Source의 `true`, `false`는 Compiler에서 Typed Bool Literal로 Lowering되며 Runtime은 Source Keyword를 알지 않는다.

### 8.2 Domain Type

```json
{
  "kind": "domain",
  "name": "ProjectCode",
  "base": "String"
}
```

### 8.3 Money

```json
{
  "kind": "money",
  "currency": "KRW"
}
```

### 8.4 List

```json
{
  "kind": "list",
  "item": {
    "kind": "named",
    "name": "ChildProject"
  }
}
```

### 8.5 Record

```json
{
  "kind": "record",
  "name": "ParentProject",
  "fields": [
    {
      "name": "code",
      "type": {
        "kind": "domain",
        "name": "ProjectCode",
        "base": "String"
      }
    },
    {
      "name": "budget",
      "type": {
        "kind": "money",
        "currency": "KRW"
      }
    }
  ]
}
```

---

## 9. Type Table

Named Record Type은 `types`에 저장한다.

```json
{
  "types": [
    {
      "type_id": "type:ParentProject",
      "kind": "record",
      "name": "ParentProject",
      "fields": [
        {
          "name": "code",
          "type": {
            "kind": "domain",
            "name": "ProjectCode",
            "base": "String"
          }
        },
        {
          "name": "budget",
          "type": {
            "kind": "money",
            "currency": "KRW"
          }
        }
      ]
    }
  ]
}
```

---

## 10. Decimal / Money Serialization

JSON Number를 Decimal 표현에 직접 사용하지 않는다.

비권장:

```json
{ "amount": 0.1 }
```

권장:

```json
{
  "kind": "decimal",
  "value": "0.1"
}
```

Money Literal:

```json
{
  "kind": "money",
  "amount": "100000000",
  "currency": "KRW"
}
```

Python Runtime은 `Decimal("100000000")`으로 변환하고 Binary Floating Point를 사용하지 않는다.

---

## 11. Symbol Table

Runtime에서는 Source Identifier 대신 Deterministic Symbol ID를 사용한다.

```json
{
  "symbols": [
    {
      "symbol_id": "s0001",
      "name": "year",
      "category": "INPUT",
      "type": {
        "kind": "primitive",
        "name": "Year"
      }
    },
    {
      "symbol_id": "s0002",
      "name": "team_id",
      "category": "CONTEXT",
      "type": {
        "kind": "domain",
        "name": "TeamId",
        "base": "String"
      }
    }
  ]
}
```

Runtime Reference:

```json
{
  "kind": "symbol_ref",
  "symbol_id": "s0002"
}
```

---

## 12. Symbol ID / Node ID 생성 규칙

Random UUID를 사용하지 않고 Declaration/Traversal Order 기반 ID를 권장한다.

```text
s0001
s0002
...

stmt0001
stmt0002

expr0001
expr0002

read0001
check0001
emit0001
```

목적:

```text
동일 Source
   ↓
동일 Compile
   ↓
동일 ID
   ↓
동일 Canonical IR
   ↓
동일 Semantic Hash
```

---

## 13. Requires / Tool Resolution

Source:

```nsl
requires {
    tool PROJECT.LIST_PARENT_PROJECTS;
    tool PROJECT.LIST_CHILD_PROJECTS;
}
```

IR:

```json
{
  "requires": [
    {
      "tool_ref": "tool0001",
      "tool_id": "PROJECT.LIST_PARENT_PROJECTS",
      "capability": "READ",
      "version": "1.0.0",
      "contract_hash": "sha256:..."
    }
  ]
}
```

Runtime은 `tool_ref`를 기준으로 실행한다.

---

## 14. Tool Contract Fingerprint

`.nso`에는 최소 다음 정보를 포함한다.

```text
tool_id
tool_version
capability
contract_hash
```

Runtime은 현재 Tool Registry Contract와 비교한다.

```text
.nso contract_hash
        == ?
Current Tool Registry contract_hash
```

불일치 시:

```text
TOOL_CONTRACT_MISMATCH
```

---

## 15. Limits

Source에서 생략된 Default도 IR에서는 명시적으로 Normalize하는 것을 권장한다.

```json
{
  "limits": {
    "tool_calls": 200,
    "loop_iterations": 10000,
    "emitted_rows": 100,
    "duration_ms": 30000,
    "collection_size": 10000
  }
}
```

---

## 16. Input / Context / Output Schema

### Input

```json
{
  "inputs": [
    {
      "symbol_id": "s0001",
      "name": "year",
      "type": {
        "kind": "primitive",
        "name": "Year"
      },
      "required": true
    }
  ]
}
```

### Context

```json
{
  "contexts": [
    {
      "symbol_id": "s0002",
      "name": "team_id",
      "type": {
        "kind": "domain",
        "name": "TeamId",
        "base": "String"
      },
      "source": {
        "kind": "context_path",
        "path": ["user", "team_id"]
      }
    }
  ]
}
```

### Output

```json
{
  "output": {
    "kind": "record",
    "fields": [
      {
        "name": "parent_project",
        "type": {
          "kind": "domain",
          "name": "ProjectCode",
          "base": "String"
        }
      },
      {
        "name": "budget",
        "type": {
          "kind": "money",
          "currency": "KRW"
        }
      },
      {
        "name": "status",
        "type": {
          "kind": "enum",
          "name": "CheckStatus",
          "values": ["PASS", "FAIL", "UNKNOWN"]
        }
      }
    ]
  }
}
```

---

## 17. Expression Node 공통 구조

```json
{
  "node_id": "expr0001",
  "kind": "...",
  "type": {},
  "...": "..."
}
```

Type Checking 결과를 IR에 포함하고 Runtime은 Type Inference를 다시 수행하지 않는다.

---

## 18. Symbol / Field / Projection Expression

### Symbol Reference

```json
{
  "node_id": "expr0010",
  "kind": "symbol_ref",
  "symbol_id": "s0003",
  "type": {
    "kind": "list",
    "item": {
      "kind": "named",
      "name": "ParentProject"
    }
  }
}
```

### Field Reference

```json
{
  "node_id": "expr0020",
  "kind": "field",
  "source": {
    "kind": "symbol_ref",
    "symbol_id": "s0005"
  },
  "field": "budget",
  "type": {
    "kind": "money",
    "currency": "KRW"
  }
}
```

### Collection Projection

Source:

```nsl
children.expense_amount
```

IR:

```json
{
  "node_id": "expr0021",
  "kind": "project",
  "source": {
    "kind": "symbol_ref",
    "symbol_id": "s0006"
  },
  "field": "expense_amount",
  "type": {
    "kind": "list",
    "item": {
      "kind": "money",
      "currency": "KRW"
    }
  }
}
```

---

## 19. Binary / Built-in Expression

### Bool Literal

Source:

```nsl
let approved = true;
```

IR:

```json
{
  "node_id": "expr0029",
  "kind": "literal",
  "type": {
    "kind": "primitive",
    "name": "Bool"
  },
  "value": true
}
```

`false`도 JSON native Boolean `false`로 Serialization한다. Bool Literal은 정적 `Bool` Type으로만 사용하며 Runtime은 Int/String/List를 Bool로 암묵 변환하지 않는다.

### Binary

```json
{
  "node_id": "expr0030",
  "kind": "binary",
  "operator": "LE",
  "left": {
    "kind": "symbol_ref",
    "symbol_id": "s0007"
  },
  "right": {
    "kind": "field",
    "source": {
      "kind": "symbol_ref",
      "symbol_id": "s0005"
    },
    "field": "budget"
  },
  "type": {
    "kind": "primitive",
    "name": "Bool"
  }
}
```

Source Operator는 Normalized Enum으로 변환한다.

```text
<=  → LE
+   → ADD
-   → SUB
==  → EQ
```

### Built-in Call

```json
{
  "node_id": "expr0040",
  "kind": "call",
  "function": {
    "namespace": "builtin",
    "name": "sum",
    "version": "1"
  },
  "arguments": [
    {
      "kind": "project",
      "source": {
        "kind": "symbol_ref",
        "symbol_id": "s0006"
      },
      "field": "expense_amount"
    }
  ],
  "type": {
    "kind": "money",
    "currency": "KRW"
  }
}
```

---

## 20. READ Expression과 Result Policy

```json
{
  "node_id": "read0001",
  "kind": "read",
  "tool_ref": "tool0001",
  "arguments": [
    {
      "name": "year",
      "value": {
        "kind": "symbol_ref",
        "symbol_id": "s0001"
      }
    }
  ],
  "result_type": {
    "kind": "list",
    "item": {
      "kind": "named",
      "name": "ParentProject"
    }
  },
  "result_policy": {
    "required": true,
    "accept_partial": false,
    "empty_is_valid": true
  }
}
```

`result_policy`는 False PASS 방지의 핵심이다.

- Tool 호출 실패와 Empty Result를 구분한다.
- Partial Result를 별도 상태로 취급한다.
- Empty Collection이 업무적으로 정상인지 Tool/Skill Contract에서 명시한다.

---

## 21. Statement IR

### LET

```json
{
  "node_id": "stmt0001",
  "kind": "let",
  "target_symbol_id": "s0003",
  "value": {
    "kind": "read",
    "...": "..."
  }
}
```

### FOREACH

```json
{
  "node_id": "stmt0002",
  "kind": "foreach",
  "iterator_symbol_id": "s0005",
  "collection": {
    "kind": "symbol_ref",
    "symbol_id": "s0003"
  },
  "max_iterations": 100,
  "body": []
}
```

### CHECK

```json
{
  "node_id": "check0001",
  "kind": "check",
  "check_id": "BUDGET_LIMIT",
  "condition": {
    "kind": "binary",
    "operator": "LE"
  },
  "severity": "ERROR",
  "on_fail": "REPORT",
  "message": "자 프로젝트 지출 합계가 모 프로젝트 예산을 초과했습니다.",
  "result_symbol_id": "s0008",
  "data_policy": {
    "require_complete": true,
    "on_partial": "UNKNOWN",
    "on_unknown": "UNKNOWN"
  }
}
```

`NSL-0.1-STRICT`에서는 `UNKNOWN → PASS`를 허용하는 옵션을 제공하지 않는다. `PARTIAL` 또는 `UNKNOWN` Completeness는 CHECK `UNKNOWN`으로 처리한다.

### EMIT

```json
{
  "node_id": "emit0001",
  "kind": "emit",
  "fields": [
    {
      "name": "spent",
      "value": {
        "kind": "symbol_ref",
        "symbol_id": "s0007"
      }
    },
    {
      "name": "status",
      "value": {
        "kind": "field",
        "source": {
          "kind": "symbol_ref",
          "symbol_id": "s0008"
        },
        "field": "status"
      }
    }
  ]
}
```

---

## 22. Resource / Safety Analysis

Compiler 분석 결과를 `.nso`에 기록한다.

```json
{
  "analysis": {
    "resource_bounds": {
      "max_tool_calls": 101,
      "max_loop_iterations": 100,
      "max_emit_records": 100,
      "bounded": true
    },
    "capabilities": [
      "MCP_READ",
      "VALIDATE",
      "STRUCTURED_OUTPUT"
    ],
    "safety": {
      "contains_write": false,
      "contains_unbounded_loop": false,
      "contains_dynamic_code": false
    }
  }
}
```

Runtime은 Compiler 결과를 무조건 신뢰하지 않고 Load 시 최소 Safety Validation을 다시 수행한다.

---

## 23. Build / Debug Metadata

### Build

```json
{
  "build": {
    "compiler": {
      "name": "nslc",
      "version": "0.1.0"
    },
    "compiled_at": "2026-08-18T14:55:00+09:00",
    "root_source": "skills/project_budget_check.ns",
    "sources": [
      {"logical_path": "skills/project_budget_check.ns", "sha256": "sha256:..."},
      {"logical_path": "common/finance.ns", "sha256": "sha256:..."}
    ]
  }
}
```

### Debug

```json
{
  "debug": {
    "nodes": {
      "read0001": {
        "logical_path": "skills/project_budget_check.ns",
        "line": 30,
        "column": 9,
        "end_line": 33,
        "end_column": 10
      }
    }
  }
}
```

Production Package에서는 Debug Metadata를 제거할 수 있다.

---

## 24. Hash Model

Include 도입 이후 Source 구성과 실행 의미를 분리하여 Hash를 관리한다.

```json
{
  "hashes": {
    "source_bundle_sha256": "sha256:...",
    "semantic_sha256": "sha256:..."
  }
}
```

### Source Bundle Hash

`source_bundle_sha256`는 어떤 Root/Included Source 조합을 Compile했는지 식별한다. 각 Source는 `build.sources[]`에 개별 SHA-256을 기록하며 Source Manifest를 Canonicalize하여 Bundle Hash를 생성한다.

```text
source_bundle_sha256
    → Source 구성 / Provenance 식별
```

### Semantic Hash

`semantic_sha256`는 Runtime 실행 의미를 식별한다.

포함:

```text
language
skill.id
skill.version
skill.risk
semantics_profile
features
types
symbols
requires
limits
inputs
contexts
output
body
analysis 중 execution-semantic 영역
```

제외:

```text
description
build
compiled_at
root_source
build.sources
debug
hashes
signature
```

동일 실행 의미를 한 `.ns` 파일에 작성하거나 여러 `include` Fragment로 분리하더라도 최종 Canonical IR이 같다면 `semantic_sha256`도 같아야 한다.

## 25. Canonical JSON Serialization

권장 규칙:

```text
UTF-8

Object Key
    Lexicographic Order

Insignificant Whitespace
    제거

Array
    의미상 순서 유지

Decimal
    Canonical String

Date / DateTime
    ISO 8601

Boolean
    true / false

Source Manifest
    logical_path 기준 Stable Order
```

동일 IR은 항상 동일 Byte Sequence를 생성해야 한다.

---

## 26. Version Compatibility

Runtime은 최소 다음을 검사한다.

```text
IR Version
Language Version
Semantics Profile
Required Features
```

v1.0에서는 보수적으로 처리한다.

```text
IR Major Version 다름 → Reject
지원하지 않는 Minor → Reject
지원하지 않는 NSL Version → Reject
Unknown Feature → Reject
Unknown Statement Kind → Reject
Unknown Expression Kind → Reject
```

---

## 27. `.nso` Load Validation

```text
.nso
 │
 ▼
JSON Parse
 │
 ▼
Schema Validation
 │
 ▼
IR Version Check
 │
 ▼
Language Version Check
 │
 ▼
Feature Check
 │
 ▼
Symbol Integrity
 │
 ▼
Type Integrity
 │
 ▼
Tool Contract Check
 │
 ▼
Safety Check
 │
 ▼
Resource Bound Check
 │
 ▼
Runtime Execute
```

다음은 실행 거부 대상이다.

```text
Unknown node kind
Missing symbol
Duplicate node ID
Duplicate symbol ID
Unknown tool_ref
WRITE Tool
Unbounded foreach
Invalid Money type
Unknown feature
Unsupported IR version
Malformed output schema
Hash mismatch
```

---

## 28. Python IR Object Model

`.nso` JSON Dictionary를 Runtime이 직접 탐색하며 실행하지 않는 것을 권장한다.

```text
.nso JSON
    ↓
Schema Validation
    ↓
Immutable Python IR Model
    ↓
Runtime
```

예:

```python
@dataclass(frozen=True)
class LetStatement:
    node_id: str
    target_symbol_id: str
    value: Expression
```

```python
@dataclass(frozen=True)
class ForeachStatement:
    node_id: str
    iterator_symbol_id: str
    collection: Expression
    max_iterations: int
    body: tuple[Statement, ...]
```

권장 구현 기반:

```text
dataclass(frozen=True)
+
Enum
+
Decimal
```

---

## 29. 권장 Module 구조

```text
nsl/
└─ ir/
   ├─ schema.py
   ├─ types.py
   ├─ expressions.py
   ├─ statements.py
   ├─ models.py
   ├─ loader.py
   ├─ validator.py
   ├─ serializer.py
   ├─ canonical.py
   └─ hashing.py
```

---

## 30. IR Schema Test

최소 Test Set:

```text
Normal IR Load
Canonical Serialization
Serialize → Deserialize
Semantic Hash Reproducibility
Unknown Node
Missing Symbol
Duplicate Symbol
Duplicate Node ID
Type Mismatch
Unsupported Feature
Unsupported Version
Malformed Money
Malformed Tool Ref
Unbounded Loop
Hash Tamper
```

Golden Test:

```text
project_budget_check.ns
       ↓ compile
project_budget_check.nso
```

동일 Source를 반복 Compile해도 `semantic_sha256`은 항상 동일해야 한다.

---

## 31. 핵심 Architecture Decision

`.nso`를 다음처럼 정의하지 않는다.

```text
AST를 JSON으로 저장한 파일
```

권장 정의:

```text
Validated
Typed
Normalized
Bounded
Tool-resolved
Execution-oriented
Canonical IR
```

즉 `.nso`는 **Source Language와 Runtime 사이의 안정적인 Contract**이다.

---

## 32. 설계 효과

### Parser와 Runtime 분리

```text
NSL Syntax 변경
       ↓
Parser / Compiler 변경
       ↓
동일 IR 생성
       ↓
Runtime 변경 없음
```

### NSL v0.2 확장

`and/or`, `filter`, `tolerance`가 추가되어도 기존 IR로 Normalize하거나 최소한의 신규 Node만 추가한다.

### 상품화

```text
.ns
 ↓
.nso
 ↓
.nsp
```

Source 공개 없이 Runtime Artifact를 배포할 수 있다.

### Audit / Replay

Node ID와 Semantic Hash를 기준으로 정확한 실행 Logic을 추적하고 재현할 수 있다.

---

## 33. 현 시점 권장 결정사항

| 항목 | 권장안 |
|---|---|
| `.nso` 의미 | NeX Skill Object |
| Encoding | Canonical JSON |
| 성격 | Typed Execution IR |
| AST 저장 여부 | 직접 AST 저장 아님 |
| Symbol | Deterministic Symbol ID |
| Node | Deterministic Node ID |
| Type | IR에 명시 |
| Tool | Resolve + Contract Hash |
| Loop | Bound 명시 |
| CHECK | Strict Data Policy: `on_partial=UNKNOWN`, `on_unknown=UNKNOWN` |
| Decimal | String Serialization |
| Money | Decimal + Currency |
| Source 위치 | Debug Metadata (`logical_path` 포함) |
| Include | Compiler Source Composition에서 제거; IR Node 없음 |
| Bool Literal | `true/false` → Typed Bool Literal IR |
| Source Bundle Hash | Root/Included Source 구성 식별 |
| Semantic Hash | Source 파일 분할과 독립적인 Execution 의미 기준 |
| Build Metadata | Hash에서 제외 |
| Runtime | `.nso` 직접 실행 |
| JSON Dict 직접 실행 | 금지 |
| Python IR Model | Immutable 권장 |

---

## 34. 연계 상세 설계 상태

본 IR Schema와 연계되는 Runtime Core 상세설계는 후속 문서에서 구체화되었다.

```text
.nso IR
   ↓
ExecutionContext
   ↓
Canonical Tool Contract
   ↓
PASS / FAIL / UNKNOWN Validation Semantics
   ↓
Runtime Class/Object Model
```

Compiler Detailed Design에서 `include → Structured Source Composition → IR 비노출`, `true/false → Bool Literal AST → Typed Bool Literal IR`이 확정되었다. Compiler와 Runtime은 Shared `core/` Type Model과 Shared `ir/SkillObject`를 Single Source of Truth로 사용한다.

## 35. 최종 정의

> **`.nso`는 NSL Source의 문법적 표현을 제거하고 모든 Symbol, Type, Tool, Safety, Resource 정보를 확정한 뒤 NSL Runtime과 계약하는 Canonical Typed Execution Object이다.**

```text
.ns
       ↓
Compiler
       ↓
.nso
       ↓
Runtime
       ↓
MCP / CHECK / EMIT
```

이 경계를 안정적으로 유지하는 것이 NSL Runtime의 확장성과 재현성의 핵심이다.
