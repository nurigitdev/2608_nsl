# NSL v0.2 Approval Implementation Plan

**Version:** 1.0  
**Approval slices:** 0047-0049  
**Status:** IN PROGRESS  
**Normative requirements:** `03_NeX_Skill_Language_v0.2_Interpreter_Runtime_SRS_v2.0_Draft.md`  
**Machine traceability:** `requirements/nsl_v0_2_traceability.json`

## 1. Purpose

The Approval phase establishes protected Approval request/evidence semantics,
durable non-blocking suspension, and fail-closed single-consumer resume. It does
not implement Approval routing, user interfaces, notification, human decision
workflow, resume scheduling, retry policy, or reconciliation decisions. Those
remain Agent/Platform Control Plane responsibilities.

The implementation extends the Runtime through an isolated `nsl/approval.py`
boundary. Checkpoint serialization remains owned by `nsl/checkpoints.py`; the
Runtime engine consumes Approval contracts without depending on Platform
workflow infrastructure.

## 2. Dependency Order

```text
0047 Approval Request and Protected Evidence
  -> 0048 Durable Suspend and Approval Publication
  -> 0049 Resume Claim, Clock, and Evidence Validation
```

Each Slice is an independent implementation, regression, traceability, commit,
and synchronization unit.

## 3. Approval Slices

| Slice | Status | Title | Primary requirements | Governing decision | Completion boundary |
|---|---|---|---|---|---|
| 0047 | COMPLETE | Approval Request and Protected Evidence | `NSL-APR-002..003`, `NSL-APR-014` | `CHG-005`, `CHG-026` | Immutable request/evidence contracts distinguish static and execution identities, bind business and semantic identities, and expose only protected payload metadata. |
| 0048 | PLANNED | Durable Suspend and Approval Publication | `NSL-APR-004..008`, `NSL-ARC-206`, `NSL-ARC-208..209` | `CHG-006`, `CHG-018` | Runtime durably saves a checkpoint before idempotent request publication and returns a non-blocking suspended result without taking ownership of workflow orchestration. |
| 0049 | PLANNED | Resume Claim, Clock, and Evidence Validation | `NSL-APR-009..013` | `RD-004`, `RD-005`, `RD-006` | Resume atomically claims one checkpoint, validates trusted evidence and current bindings using injected ports and clock, and blocks rejected, expired, stale, duplicate, or mismatched attempts. |

`RD-004..006` are FROZEN implementation decisions, not additional SRS
requirements. They constrain Slice 0049 without changing the 123-requirement
baseline or its fingerprint.

## 4. Slice Boundaries

### 4.1 Slice 0047

- Distinguish static `ApprovalId` from execution-scoped `ApprovalRequestId`.
- Bind Approval request and evidence to tenant, execution,
  `BusinessOperationId`, semantic hash, write intent hash, and payload hash.
- Keep human decisions distinct from Approval lifecycle status.
- Carry `display_safe_summary`, `protected_payload_ref`, `payload_hash`, and
  `data_classification` instead of a raw WRITE payload.
- Make nested summary data immutable, JSON-safe, and credential-free.
- Preserve opaque integrity evidence without treating its presence as proof.

Slice 0047 does not publish requests, save checkpoints, verify authenticity,
evaluate expiry, claim resume work, or invoke WRITE.

Slice 0047 completion evidence is maintained in
`tests/test_v0_2_approval_request_evidence.py`, including immutable nested
summary data, protected metadata serialization, identifier separation,
required identity/hash bindings, UTC timestamp validation, malformed inputs,
and credential rejection.

### 4.2 Slice 0048

- Define injected Checkpoint and Approval publication Ports.
- Freeze the request identity before persistence.
- Require durable checkpoint save success before publication.
- Make publication idempotent by `ApprovalRequestId` and safe across retries.
- Return `WAITING_FOR_APPROVAL` after successful save and publication so the
  worker can terminate without blocking.
- Keep routing, UI, persistence of human workflow, notification, and resume job
  scheduling outside Runtime.

### 4.3 Slice 0049

- Atomically claim the exact `(execution_id, checkpoint_version)` through an
  injected durable Checkpoint Port before restoring mutable state.
- Reject stale, live-claimed, and consumed checkpoints before state transition.
- Use only an injected Clock and timezone-aware UTC instants; expiry occurs at
  `now >= expires_at`.
- Use an injected verifier to authenticate and bind evidence to every frozen
  identity, decision, issuer, and timestamp required by `RD-006`.
- Compare current write intent and canonical payload hashes after resume.
- Treat rejection as a no-WRITE business outcome and expiry as requiring a new
  Approval.

## 5. Status and Evidence Rules

- `IMPLEMENTED` requires repository behavior and named automated evidence.
- `PARTIAL` is used when local behavior exists but complete cross-layer
  enforcement remains in a later Slice.
- `PLANNED` is retained when the assigned behavior has no repository evidence.
- Requirement text, priority, membership, and fingerprint are not changed.
- Every Requirement ID receives its own verification ID and concrete test
  evidence in `requirements/nsl_v0_2_traceability.json`.

## 6. Quality Gate

Every Approval Slice adds boundary, robustness, malformed-input,
tenant-isolation, persistence-failure, duplicate-resume, expiry, and binding
tests appropriate to its behavior and then runs exactly:

```powershell
.\.venv\Scripts\python.exe tools\run_quality.py
```

The required result is zero regression failures, statement coverage of at
least 99.00 percent, and branch coverage of at least 98.00 percent. A Slice is
not committed or synchronized when this gate fails.
