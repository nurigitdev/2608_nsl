# NSL v0.2 Foundation Implementation Plan

**Version:** 1.0  
**Foundation slices:** 0033-0037  
**Status:** ACTIVE  
**Normative requirements:** `03_NeX_Skill_Language_v0.2_Interpreter_Runtime_SRS_v2.0_Draft.md`  
**Machine traceability:** `requirements/nsl_v0_2_traceability.json`

## 1. Purpose

The Foundation phase establishes the smallest shared semantic kernel, canonical
Tool Contract identity, and additive IR 1.1 object and codec layers required by
Controlled Write. This document is an implementation plan, not a new normative
authority. The FROZEN architecture decisions, SRS, and detailed designs remain
authoritative.

The implementation follows the repository-first rule. The current
`nsl/core.py`, `nsl/tools.py`, `nsl/ir.py`, and `nsl/ir_schema.py` modules are
extended or minimally refactored only when the affected structure requires it.
A documentation-shaped, big-bang package rearrangement is outside this phase.

## 2. Dependency Order

```text
0033 Shared Core Semantic Kernel
  -> 0034 Tool Contract Static Model
  -> 0035 Tool Contract Canonical Identity
  -> 0036 IR 1.1 Object Model
  -> 0037 IR 1.1 Codec and IR 1.0 Compatibility
```

Each Slice is an independent implementation, regression, traceability, commit,
and synchronization unit.

## 3. Foundation Slices

| Slice | Title | Primary requirements | Completion boundary |
|---|---|---|---|
| 0033 | Shared Core v0.2 Semantic Kernel | `NSL-ARC-201` | Shared identity, capability, risk, approval, write, commit, and verification semantic types have one dependency-safe definition. Compiler and Runtime adoption remains `PARTIAL` until later Slices use the definitions. |
| 0034 | Tool Contract v0.2 Static Model | `NSL-TOL-201..206`, `NSL-RSK-001` | WRITE capability, operation kind, idempotency requirement, atomicity, commit semantics, risk, and optional verification metadata are immutable and validated without breaking READ contracts. |
| 0035 | Tool Contract Canonical Identity | `NSL-TOL-207`, `NSL-RSK-004` | Canonical contract identity includes WRITE semantics and excludes endpoint, credential, tenant entitlement, MFA, and other environment policy values. |
| 0036 | IR 1.1 Object Model | `NSL-IR-202..205` | Immutable Approval, Write, and RequiredTool semantic nodes exist without Runtime invocation state or Tool Contract object ownership. |
| 0037 | IR 1.1 Codec and IR 1.0 Compatibility | `NSL-IR-201`, `NSL-IR-208` | IR 1.1 validates and round-trips deterministically while IR 1.0 READ-only objects and their canonical hashes remain compatible. |

`NSL-IR-206..207` belongs to version-aware Compiler lowering. `NSL-IR-209`
is completed at the Signed NSP trust boundary. `NSL-RSK-002` belongs to Compiler
contract validation, while `NSL-RSK-003` and `NSL-RSK-005` belong to Runtime
policy and WriteGuard enforcement.

## 4. Slice 0033 Scope

Slice 0033 defines only semantics shared across multiple layers:

- `ToolCapability` and `RiskLevel` static classifications.
- `ApprovalStatus` lifecycle state.
- `PreWriteEligibility`, `CommitState`, and `VerificationStatus` as distinct
  state domains.
- `BusinessOperationId`, `IdempotencyKey`, and `PayloadHash` as distinct type
  declarations.
- Dependency and duplicate-definition guards for the shared semantic kernel.

Slice 0033 does not implement approval workflow, runtime policy, idempotency key
generation, payload hashing, write execution, commit recovery, verification,
or package signature validation. Runtime values are not added to static IR.

## 5. Status and Evidence Rules

- `IMPLEMENTED` requires repository behavior and named automated evidence.
- `PARTIAL` is used when a shared definition exists but cross-layer adoption or
  end-to-end enforcement remains in a later Slice.
- `PLANNED` is retained when the assigned behavior has no repository evidence.
- Requirement text, priority, membership, and fingerprint are not changed by
  this implementation-plan rebaseline.

## 6. Quality Gate

Every Foundation Slice must add boundary, robustness, and malformed-input tests
appropriate to its behavior and then run exactly:

```powershell
.\.venv\Scripts\python.exe tools\run_quality.py
```

The required result is zero regression failures, statement coverage of at least
99.00 percent, and branch coverage of at least 98.00 percent. A Slice is not
committed or synchronized when this gate fails.
