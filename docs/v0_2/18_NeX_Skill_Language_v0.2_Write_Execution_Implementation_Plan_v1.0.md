# NSL v0.2 Write Execution Implementation Plan

**Version:** 1.0<br>
**Write Execution slices:** 0050-0054<br>
**Status:** IN PROGRESS<br>
**Normative requirements:** `03_NeX_Skill_Language_v0.2_Interpreter_Runtime_SRS_v2.0_Draft.md`<br>
**Machine traceability:** `requirements/nsl_v0_2_traceability.json`

## 1. Purpose

The Write Execution phase establishes deterministic WRITE semantic identity,
durable idempotency evidence, fail-closed preflight, canonical business-system
invocation, and explicit commit uncertainty handling. It connects the
Compiler, Runtime Foundation, and Approval phases to a side-effecting provider
without weakening the existing trust, authorization, data-protection, or
checkpoint boundaries.

This phase does not implement Approval workflow orchestration, provider-
specific database or network clients, Platform retry decisions, reconciliation
decisions, production package trust verification, post-write verification, or
Replay. Those responsibilities remain in their assigned later Slices or in the
Agent/Platform Control Plane.

## 2. Ownership Boundaries

The implementation is expected to preserve the following ownership model,
subject to structural review before each Slice:

- Runtime Write Intent and canonical payload identity belong in an isolated
  Runtime-owned module such as `nsl/write_intent.py`.
- Idempotency key, evidence, retention, and collision semantics belong in an
  isolated Runtime-owned module such as `nsl/idempotency.py`.
- Current policy and precondition aggregation extends `nsl/validation.py`
  without placing provider-side transactional logic in Runtime Core.
- WRITE invocation and commit outcome transitions belong behind an injected
  Runtime port/coordinator boundary such as `nsl/write_execution.py`.
- `nsl/runtime_models.py` retains execution-local state and structured result
  surfaces; it must not acquire persistence, network, SQL, or MCP clients.
- `nsl/tools.py` remains the owner of static Tool Contract semantics. Runtime
  WRITE code consumes the verified contract and cannot select an undeclared or
  non-canonical business tool dynamically.

No module in this phase may introduce direct filesystem, network, SQL, LLM,
NeX-AE, or web-framework dependencies into NSL Core or Runtime.

## 3. Dependency Order

```text
0049 Resume Claim, Clock, and Evidence Validation
  -> 0050 Write Intent and Canonical Payload Hash
  -> 0051 Idempotency Contract and Evidence
  -> 0052 WriteGuard and Runtime Preflight
  -> 0053 WriteExecutionPort and Durable Request Evidence
  -> 0054 Commit Outcomes and Reconciliation
```

Slice 0052 also consumes Approval validation from Slice 0049 and current Tool
Contract risk/capability evidence from Slices 0034-0035. Each Slice is an
independent implementation, regression, traceability, commit, and
synchronization unit.

## 4. Write Execution Slices

| Slice | Status | Title | Primary requirements | Governing decision | Completion boundary |
|---|---|---|---|---|---|
| 0050 | COMPLETE | Write Intent and Canonical Payload Hash | `NSL-WRT-006`, `NSL-WRT-010` | `RD-001` | Runtime produces immutable Write Intent and Payload identities from strict typed canonical encoding, and any semantic payload change rejects prior Approval evidence with a new-Approval requirement. |
| 0051 | PLANNED | Idempotency Contract and Evidence | `NSL-IDM-001..008` | `RD-009` | Every WRITE has a stable tenant/business-operation/write-node key, separate payload hash, durable collision evidence, resume reuse, and verified provider retention guarantee. |
| 0052 | PLANNED | WriteGuard and Runtime Preflight | `NSL-WRT-009`, `NSL-WRT-011`, `NSL-RSK-003`, `NSL-RSK-005` | `RD-007` | Runtime revalidates current policy and precondition evidence immediately before invocation while requiring the provider to enforce the actual business condition atomically with WRITE. |
| 0053 | PLANNED | WriteExecutionPort and Durable Request Evidence | `NSL-WRT-001`, `NSL-ARC-204..205` | `CHG-018`, `CHG-021` | Runtime durably records `WRITE_REQUESTED` before invoking one declared Canonical Business Tool exclusively through an injected side-effect Port. |
| 0054 | PLANNED | Commit Outcomes and Reconciliation | `NSL-WRT-007..008`, `NSL-CMT-001..004` | `RD-008` | Provider outcomes map to explicit commit states; uncertain commit becomes `RECONCILIATION_REQUIRED`, is never treated as ordinary failure, and is never automatically retried or re-keyed. |

`RD-001`, `RD-007`, `RD-008`, and `RD-009` are FROZEN implementation
decisions, not additional SRS requirements. They constrain implementation and
evidence without changing the 123-requirement baseline or its fingerprint.

## 5. Slice Boundaries

### 5.1 Slice 0050

#### Part A: Canonical Typed Payload Encoding

- Reuse the existing NSL typed value encoding and canonical JSON rules.
- Sort object keys lexicographically, preserve array order, and encode Decimal,
  Money, Date, and DateTime explicitly and deterministically.
- Reject non-string object keys, unsupported values, non-finite Decimal values,
  and naive DateTime values before hashing.
- Keep credentials and customer-specific endpoints outside canonical payload
  evidence.

#### Part B: Payload and Intent Identity

- Build an immutable Runtime `WriteIntent` bound to tenant,
  `BusinessOperationId`, write node, tool, Approval, canonical arguments,
  `PayloadHash`, and `WriteIntentHash`.
- Keep `PayloadHash`, `WriteIntentHash`, and `IdempotencyKey` as distinct
  semantic values.
- Recalculate payload and intent identity after resume and compare it with the
  approved evidence from Slice 0049.
- Require a new Write Intent and Approval request whenever payload semantics
  change after Approval.

Slice 0050 does not generate an idempotency key, persist evidence, invoke a
provider, or interpret a commit outcome.

Slice 0050 completion evidence is maintained in
`tests/test_v0_2_write_intent.py`. It covers deterministic typed encoding,
empty and nested payloads, object ordering, Decimal, Money, Date, DateTime,
non-finite and unsupported values, malformed keys, cyclic and excessive
nesting, domain-separated hashes, deep immutability, exact IR/tool binding,
forged identities, changed and unchanged payload semantics, and fail-closed
reuse of prior Approval evidence. The stable framework-neutral API is exposed
from `nsl.api`; the implementation does not persist evidence, generate an
idempotency key, invoke a provider, or interpret a commit outcome.

### 5.2 Slice 0051

#### Part A: Stable Key Generation and Retention

- Derive one stable `IdempotencyKey` from at least tenant,
  `BusinessOperationId`, and write-node identity.
- Preserve the same key across suspend, resume, re-execution, and
  reconciliation for the same business WRITE.
- Never silently mint a new key after an uncertain or incomplete attempt.
- Verify that the Tool Contract retention guarantee covers the maximum
  suspend, resume, retry, and reconciliation horizon required by `RD-009`.

#### Part B: Durable Evidence and Collision Semantics

- Persist key, payload hash, business identity, write node, and retention
  evidence through an injected idempotency evidence Port.
- Keep the idempotency key and payload hash in separate fields.
- Treat the same key and same payload hash as the same logical business result.
- Treat the same key and a different payload hash as
  `IDEMPOTENCY_CONFLICT`, before provider invocation.
- Require the Canonical Business API/Tool Contract to provide the durable
  duplicate guarantee; an in-memory Runtime cache is not sufficient.

Slice 0051 completion requires boundary tests for first use, same-key reuse,
payload conflict, concurrent duplicate attempts, expired/insufficient
retention, restored checkpoint reuse, and cross-tenant isolation.

### 5.3 Slice 0052

#### Part A: Current Policy and Risk Preflight

- Require current external `PolicyDecision` or verifiable Policy Evidence
  immediately before WRITE.
- Bind policy evidence to tenant, principal, action, tool, risk, business
  operation, and current execution evidence.
- Fail closed for missing, stale, denied, malformed, or mismatched policy
  evidence.
- Support the initial acceptance boundary that limits WRITE to R3 while
  preserving explicit behavior for every risk value.

#### Part B: Two-stage Business Preconditions

- Validate the presence, freshness, and binding of version, revision, ETag,
  precondition token, transactional-revalidation request, or equivalent
  evidence in Runtime preflight.
- Pass the precondition evidence through the WRITE request contract.
- Require the Canonical Business API to enforce the actual current condition
  atomically with the side effect.
- Never report provider-side precondition success based only on Runtime
  pre-call validation.

Slice 0052 produces a typed WriteGuard decision. It does not invoke a provider
and does not convert policy or precondition failure into commit uncertainty.

### 5.4 Slice 0053

#### Part A: Canonical Business WRITE Port

- Define an injected `WriteExecutionPort` for side-effecting business calls.
- Permit only the statically declared and contract-verified Canonical Business
  Tool.
- Carry business operation identity, idempotency key, payload hash, write
  intent hash, current precondition evidence, and protected trace references.
- Keep direct filesystem, network, SQL, DB, and MCP access outside Runtime.

#### Part B: Durable Request-before-call Evidence

- Persist `WRITE_REQUESTED` evidence before calling the provider.
- Bind the durable record to tenant, execution, business operation, write node,
  tool, approval request, idempotency key, payload hash, and intent hash.
- Prevent provider invocation if durable request persistence fails.
- Make retry after acknowledgement loss reuse the same request identity and
  idempotency evidence.

Slice 0053 stops at provider invocation and raw provider response acquisition.
Commit-state normalization and recovery transitions belong to Slice 0054.

### 5.5 Slice 0054

#### Part A: Commit Outcome Normalization

- Map provider success to `COMMITTED` with durable result evidence.
- Map a confirmed provider rejection or confirmed no-side-effect outcome to
  `NOT_COMMITTED` with a structured reason code; do not create a separate
  public `REJECTED` CommitState.
- Map any outcome that cannot prove committed or not committed to
  `UNKNOWN_COMMIT_STATE`.
- Preserve provider transaction ID, business reference, timestamps, and
  protected evidence references when available.

#### Part B: Commit Uncertainty and Reconciliation

- Treat timeout, cancellation, connection loss, malformed acknowledgement, or
  worker interruption after provider invocation as commit-uncertain unless
  durable evidence proves otherwise.
- Map `UNKNOWN_COMMIT_STATE` to durable execution status
  `RECONCILIATION_REQUIRED`.
- Do not automatically retry, downgrade to ordinary failure, or rerun the same
  business operation with a new idempotency key.
- Expose reconciliation evidence and disposition to the Agent/Platform without
  making the Runtime choose a business recovery action.

Slice 0054 completion requires crash-window, timeout/cancellation,
acknowledgement-loss, malformed response, duplicate result, and
reconciliation-lockout tests.

## 6. Cross-slice Safety Invariants

- No WRITE occurs without `PreWriteEligibility.ALLOWED`, valid current
  Approval evidence, current policy evidence, valid precondition evidence,
  durable idempotency evidence, and successful `WRITE_REQUESTED` persistence.
- Runtime never performs automatic currency conversion or weakens typed value
  semantics while constructing canonical payloads.
- Runtime never uses Python truthiness, ad hoc string serialization, random
  payload hashes, or process-local locks as durable business guarantees.
- Raw WRITE payloads are not copied into generic events, traces, queues, or
  audit records; protected references and hashes are used instead.
- Provider invocation happens at most through the injected canonical WRITE
  Port and never during Replay.
- A provider call is never inferred to be `NOT_COMMITTED` merely because its
  acknowledgement was not received.
- Reconciliation ownership remains with the Agent/Platform Control Plane.

## 7. Status and Evidence Rules

- `IMPLEMENTED` requires repository behavior and named automated evidence.
- `PARTIAL` is used when local behavior exists but complete Runtime,
  provider-contract, or persistence enforcement remains in a later Slice.
- `PLANNED` is retained when the assigned behavior has no repository evidence.
- Requirement text, priority, membership, and fingerprint are not changed.
- Every Requirement ID receives its own verification ID and concrete test
  evidence in `requirements/nsl_v0_2_traceability.json`.
- FROZEN decisions receive explicit automated evidence but do not increase the
  SRS requirement count.

## 8. Quality Gate

Every Write Execution Slice adds Boundary Value Analysis, robustness, and
worst-case tests appropriate to its behavior. Required cases include empty and
maximum payload structures, canonical ordering, malformed typed values,
tenant isolation, duplicate/concurrent attempts, persistence failure,
provider failure, timeout/cancellation, commit uncertainty, checkpoint resume,
and replay-safe boundaries.

After each Slice, run exactly:

```powershell
.\.venv\Scripts\python.exe tools\run_quality.py
```

The required result is zero regression failures, statement coverage of at
least 99.00 percent, and branch coverage of at least 98.00 percent. A Slice is
not committed or synchronized when this gate fails.
