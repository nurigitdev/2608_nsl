# NSL v0.2 Runtime Foundation Implementation Plan

**Version:** 1.0  
**Runtime Foundation slices:** 0043-0046  
**Status:** COMPLETE<br>
**Normative requirements:** `03_NeX_Skill_Language_v0.2_Interpreter_Runtime_SRS_v2.0_Draft.md`  
**Machine traceability:** `requirements/nsl_v0_2_traceability.json`

## 1. Purpose

The Runtime Foundation phase establishes execution-scoped identity, security
context, mutable state, checkpoint, status/result, and pre-write validation
contracts required by Controlled Write. This plan is not a new normative
authority. The FROZEN architecture decisions, SRS, and detailed Runtime designs
remain authoritative.

The implementation extends the current `nsl/runtime_models.py`,
`nsl/runtime.py`, and `nsl/validation.py` ownership boundaries. External
persistence, Approval workflow, Tool execution, policy engines, trust
verification, resume scheduling, and reconciliation decisions remain injected
Port or Agent/Platform responsibilities.

## 2. Dependency Order

```text
0043 Execution Identity and Context v0.2
  -> 0044 Checkpoint State and Restore
  -> 0045 Execution Status and Structured Result
  -> 0046 Validation Summary and PreWriteEligibility
```

Slice 0045 also depends directly on the execution identity and state boundary
from Slice 0043. Each Slice is an independent implementation, regression,
traceability, commit, and synchronization unit.

## 3. Runtime Foundation Slices

| Slice | Status | Title | Primary requirements | Governing decision | Completion boundary |
|---|---|---|---|---|---|
| 0043 | COMPLETE | Execution Identity and Context v0.2 | `NSL-CTX-201..206`, `NSL-CTX-210`, `NSL-ARC-211` | `CHG-003`, `CHG-009`, `CHG-027` | Execution identity and preserved Principal/DataPolicy are explicit; Approval, Write Journal, Idempotency, and Checkpoint state are isolated per execution; side-effecting external services are absent from `ExecutionContext`. |
| 0044 | COMPLETE | Checkpoint State and Restore | `NSL-CTX-207..209` | `CHG-018` | A versioned immutable checkpoint is deterministically serialized, integrity-bound, and validated against semantic, security, and business identities before state restoration. |
| 0045 | COMPLETE | Execution Status and Structured Result | `NSL-STS-201..205`, `NSL-RSL-201..203` | `CHG-008`, `CHG-022`, `CHG-030` | Durable suspend and reconciliation statuses plus additive v0.2 result summaries preserve every v0.1 result field and meaning. |
| 0046 | COMPLETE | Validation Summary and PreWriteEligibility | `NSL-WEL-001..005`, `NSL-WRT-005` | `CHG-007` | Deterministic validation aggregation produces typed pre-write eligibility, and WRITE entry rejects every result other than `ALLOWED`. |

`RD-004` and `RD-008` are FROZEN implementation decisions, not additional SRS
requirements. They constrain implementation and evidence without changing the
123-requirement baseline or its fingerprint. The atomic single-consumer claim
required by `RD-004` is owned by Slice 0049, and the commit uncertainty behavior
required by `RD-008` is owned by Slice 0054.

## 4. Slice Boundaries

### 4.1 Slice 0043

- Preserve the v0.1 `execution_id`, `ExecutionPrincipal`, and
  `DataHandlingPolicy` request contract additively.
- Add an optional Runtime-supplied `BusinessOperationId` without deriving it
  from Source or static IR and without inventing it for legacy requests.
- Expose immutable execution identity and security context views, including
  the tenant derived from the verified principal.
- Give every `ExecutionContext` independent Approval, Write Journal,
  Idempotency, and Checkpoint mutable state containers.
- Keep external persistence, audit/trace sinks, Snapshot stores, Tool
  executors, Approval services, DB/MCP clients, and trust verifiers outside the
  context and inject them into Runtime execution paths.
- Preserve v0.1 Runtime behavior and constructor compatibility at public
  request boundaries.

Slice 0043 does not implement checkpoint serialization/restore, approval
records or publication, idempotency key generation, WRITE invocation, new
durable statuses, or pre-write eligibility. Those behaviors remain assigned to
later Slices.

Slice 0043 completion evidence is maintained in
`tests/test_v0_2_runtime_context.py` and the existing v0.1 Runtime regression
suite.

### 4.2 Slice 0044

- Freeze a versioned immutable checkpoint DTO from execution-local state.
- Bind checkpoint identity to tenant, execution, business operation, Skill
  semantic hash, approval request, write intent, and payload hash where
  applicable.
- Restore Symbol, Check, Resource, Approval, Write, and Idempotency state with
  schema, integrity, and ownership validation.
- Establish the monotonic checkpoint version token consumed by the atomic
  single-consumer claim/restore transition in Slice 0049. Slice 0044 does not
  own a Checkpoint Port, claim persistence, resume queue, or worker lifecycle.

Slice 0044 completion evidence is maintained in
`tests/test_v0_2_checkpoint_restore.py`, including deterministic round-trip,
full execution-local state restoration, semantic/security/business binding,
identity preservation, boundary values, and malformed or tampered checkpoint
rejection.

### 4.3 Slice 0045

- Add only the durable Runtime statuses defined by the SRS; platform queue and
  transient write-attempt states remain outside `ExecutionStatus`.
- Extend structured results additively with pre-write eligibility, approval,
  write, verification, and suspension summaries.
- Keep v0.1 result decoding, required fields, status meaning, and canonical
  output behavior compatible.
- Keep the complete commit uncertainty transition policy governed by `RD-008`
  in Slice 0054; Slice 0045 provides its typed public status/result surface.

Slice 0045 completion evidence is maintained in
`tests/test_v0_2_status_and_result.py`, including exact v0.1 JSON
compatibility, independent optional-field serialization, READ-only absence
semantics, forbidden public statuses, and structured suspension and
reconciliation outcomes.

### 4.4 Slice 0046

- Aggregate static, policy, approval, binding, precondition, idempotency,
  durability, and trust validation evidence deterministically.
- Keep `PreWriteEligibility`, `CommitState`, and `ExecutionStatus` as distinct
  semantic domains.
- Exclude post-write reconciliation from pre-write eligibility.
- Guard the Runtime WRITE entry so only `ALLOWED` can proceed; actual provider
  invocation remains assigned to the Write execution Slices.

Slice 0046 completion evidence is maintained in
`tests/test_v0_2_prewrite_eligibility.py`, including deterministic CHECK
aggregation, fail-closed approval and control evidence, each independently
failing policy/precondition/binding/idempotency/durability/trust axis,
forbidden reconciliation values, and the ALLOWED-only WRITE entry gate.

## 5. Status and Evidence Rules

- `IMPLEMENTED` requires repository behavior and named automated evidence.
- `PARTIAL` is used when local behavior exists but complete Runtime or
  cross-layer enforcement remains in a later Slice.
- `PLANNED` is retained when the assigned behavior has no repository evidence.
- Requirement text, priority, membership, and fingerprint are not changed by
  this implementation plan.
- Each Requirement ID receives its own verification ID and concrete test
  evidence in `requirements/nsl_v0_2_traceability.json`.

## 6. Quality Gate

Every Runtime Foundation Slice must add boundary, robustness, malformed-input,
tenant-isolation, restore/replay, and v0.1 compatibility tests appropriate to
its behavior and then run exactly:

```powershell
.\.venv\Scripts\python.exe tools\run_quality.py
```

The required result is zero regression failures, statement coverage of at
least 99.00 percent, and branch coverage of at least 98.00 percent. A Slice is
not committed or synchronized when this gate fails.
