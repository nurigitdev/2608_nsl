# NSL v0.2 Compiler Implementation Plan

**Version:** 1.0  
**Compiler slices:** 0038-0042  
**Status:** IN PROGRESS  
**Normative requirements:** `03_NeX_Skill_Language_v0.2_Interpreter_Runtime_SRS_v2.0_Draft.md`  
**Machine traceability:** `requirements/nsl_v0_2_traceability.json`

## 1. Purpose

The Compiler phase adds Source Language 0.2 syntax and static safety while
preserving Source Language 0.1 keyword, identifier, deterministic lowering,
canonical serialization, and semantic hash behavior. This plan does not create
a new normative authority. The FROZEN architecture decisions, SRS, and detailed
Compiler designs remain authoritative.

The implementation extends the existing `nsl/syntax.py`, `nsl/symbols.py`,
`nsl/type_system.py`, and `nsl/compiler.py` ownership boundaries. Runtime
approval state, authorization decisions, write execution, and environment
policy remain outside the Compiler.

## 2. Dependency Order

```text
0038 Version-aware Lexer Bootstrap
  -> 0039 Approval and Write Parser AST
  -> 0040 Approval Symbol and Binding Semantics
  -> 0041 Static Type and Write Safety Analysis
  -> 0042 Compiler Lowering to IR 1.1
```

Each Slice is an independent implementation, regression, traceability, commit,
and synchronization unit.

## 3. Compiler Slices

| Slice | Status | Title | Primary requirements | Governing decision | Completion boundary |
|---|---|---|---|---|---|
| 0038 | COMPLETE | Version-aware Lexer Bootstrap | `NSL-LEX-201..203` | `RD-003` | A bounded leading declaration scan selects the 0.1 or 0.2 keyword table before full tokenization. Invalid headers fail before tokenization, while 0.1 lexical compatibility remains unchanged. |
| 0039 | COMPLETE | Approval and Write Parser AST | `NSL-PAR-201..204` | - | Approval and WRITE Source forms produce immutable AST nodes whose complete token ranges are retained as `SourceSpan` values. |
| 0040 | PLANNED | Approval Symbol and Binding Semantics | `NSL-SEM-201..206`, `NSL-APR-001` | `RD-002` | Approval IDs have a dedicated namespace; references obey declaration order, dominance, exactly 1:1 binding, and no-unused-approval rules. |
| 0041 | PLANNED | Static Type and Write Safety Analysis | `NSL-TYP-201..204`, `NSL-WRT-002..004`, `NSL-RSK-002` | `RD-002` | WRITE payloads and tool contracts are statically typed; strict profile flow, count, loop, atomicity, and risk metadata rules are enforced without deciding user or tenant entitlement. |
| 0042 | PLANNED | Compiler Lowering to IR 1.1 | `NSL-IR-206..207` | `RD-003` | Source Language 0.1 deterministically lowers to IR 1.0 and Source Language 0.2 to IR 1.1 without an override option or changes to 0.1 canonical output. |

`RD-002` and `RD-003` are FROZEN implementation decisions, not additional SRS
requirements. They constrain implementation and verification evidence without
changing the 123-requirement baseline or its fingerprint.

## 4. Slice Boundaries

### 4.1 Slice 0038

- Scan only the leading language declaration, comments, whitespace, and source
  positions within a fixed byte bound.
- Reject missing, malformed, unsupported, duplicate, and non-leading language
  declarations before Root Source tokenization.
- Keep `write`, `approve`, and `approved_by` as identifiers in Language 0.1 and
  reserve them only in Language 0.2.
- Preserve v0.1 `include`, Bool literals, identifiers, Decimal literals, Money
  type lexemes, token raw lexemes, normalized values, and `SourceSpan` values.
- Propagate the Root Source language version explicitly to Include Fragment
  tokenization.

Slices 0039-0042 own AST additions, approval binding, static WRITE analysis,
and IR lowering respectively. Slice 0038 must not implement those behaviors.

Slice 0038 completion evidence is maintained in
`tests/test_v0_2_version_aware_lexer.py`. The tests cover `RD-003` header
bootstrap behavior in addition to the three assigned SRS requirements.

### 4.2 Slice 0039

- Parse Language 0.2 `approve` statements and mandatory `approved_by` WRITE
  references without changing Language 0.1 identifier behavior.
- Represent approval statements and WRITE expressions with immutable AST
  nodes, ordered argument tuples, and exact approval identifiers.
- Retain complete node ranges and identifier-level source spans for subsequent
  binding diagnostics.
- Reject incomplete approval and WRITE forms during parsing.

Slice 0039 completion evidence is maintained in
`tests/test_v0_2_parser_and_ast.py`.

## 5. Status and Evidence Rules

- `IMPLEMENTED` requires repository behavior and named automated evidence.
- `PARTIAL` is used when local behavior exists but a requirement's complete
  Compiler or cross-layer contract remains in a later Slice.
- `PLANNED` is retained when the assigned behavior has no repository evidence.
- Requirement text, priority, membership, and fingerprint are not changed by
  this implementation plan.
- Each Requirement ID receives its own verification ID and concrete test
  evidence in `requirements/nsl_v0_2_traceability.json`.

## 6. Quality Gate

Every Compiler Slice must add boundary, robustness, malformed-input, and v0.1
compatibility tests appropriate to its behavior and then run exactly:

```powershell
.\.venv\Scripts\python.exe tools\run_quality.py
```

The required result is zero regression failures, statement coverage of at least
99.00 percent, and branch coverage of at least 98.00 percent. A Slice is not
committed or synchronized when this gate fails.
