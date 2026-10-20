# NSL v0.2 Requirement Baseline and Implementation Readiness

**Version:** 1.0  
**Slice:** 0032  
**Status:** FROZEN FOR IMPLEMENTATION  
**Normative source:** `03_NeX_Skill_Language_v0.2_Interpreter_Runtime_SRS_v2.0_Draft.md`  
**Machine baseline:** `requirements/nsl_v0_2_traceability.json`  
**Readiness ledger:** `requirements/nsl_v0_2_readiness.json`

## 1. Result

Slice 0032 freezes an independently verifiable v0.2 requirement baseline without claiming that v0.2 product behavior already exists. The baseline contains 123 requirements: 121 `MUST` and 2 `SHOULD`. Every requirement starts as `PLANNED`; implementation status may advance only with repository evidence and the mandatory regression and coverage gate.

The five FROZEN architecture authority documents are pinned by SHA-256. CHG-001 through CHG-032 are checked against every affected Markdown document listed in the Unified Change Ledger. The v0.1 325-requirement baseline remains active and is not replaced by this baseline.

## 2. Requirement Baseline

| Item | Frozen value |
|---|---|
| Baseline ID | `NSL-V0.2-SRS-RB1` |
| Requirement count | 123 |
| Priority count | `MUST=121`, `SHOULD=2` |
| Initial status | `PLANNED=123` |
| Fingerprint | `47ef551d51cddddf23a0b9b5745a5630403d333043db0350268245b9a361256b` |
| Fingerprint input | SRS order, `RequirementId|Priority|Text` |

`NSL-TOL-206` and `NSL-VFY-001` are `SHOULD`. Every other row is `MUST`. A change to requirement membership, text, or priority requires an explicit fingerprint rebaseline.

## 3. Implementation Slices

The original eight ownership groups were rebaselined into independently
implementable and verifiable Slices 0033 through 0062. The first phase is the
five-Slice Foundation defined in
`14_NeX_Skill_Language_v0.2_Foundation_Implementation_Plan_v1.0.md`.

| Range | Phase | Numbered requirements |
|---|---|---:|
| 0033-0037 | Foundation | 16 |
| 0038-0042 | Compiler | 24 |
| 0043-0046 | Runtime Foundation | 25 |
| 0047-0049 | Approval | 16 |
| 0050-0054 | Write Execution | 23 |
| 0055-0058 | Trust and Evidence | 17 |
| 0059-0062 | Vertical Integration and Certification | 2 |

The machine baseline owns the exact per-requirement allocation. Slices without
a numbered requirement deliver SRS acceptance behavior such as v0.2 audit,
vertical examples, and CLI developer experience. A requirement has one
traceability owner at a time.

## 4. Authority and Change Ledger

The architecture authority order remains:

1. FROZEN Architecture Delta and Batch A, B, C decisions.
2. FROZEN Architecture Consistency Closure and Unified Change Ledger.
3. Normative v0.2 SRS requirements.
4. Component detailed designs.
5. This implementation-readiness baseline for ambiguities not fixed by the documents above.

The machine readiness ledger pins all five authority documents and verifies 32 contiguous Change IDs. There are 22 `P0` changes and 10 `P1` changes. A Change ID passes only when every document named by its `Affected Docs` set exists and contains that exact `CHG-*` marker.

## 5. Frozen Implementation Decisions

### RD-001 Canonical Payload and Intent Hashes

Runtime WRITE values use the existing NSL typed value encoding and canonical JSON rules: UTF-8, lexicographically sorted object keys, compact separators, ordered arrays, explicit Decimal and Money encodings, and ISO Date and DateTime encodings. Non-finite Decimal values, non-string object keys, unsupported values, and naive DateTime values are rejected before hashing.

`PayloadHash` is SHA-256 over `b"NSL-WRITE-PAYLOAD-V1\\0" + canonical_json(encode_value(arguments))`. `write_intent_hash` is SHA-256 over `b"NSL-WRITE-INTENT-V1\\0"` plus canonical JSON containing `skill_semantic_hash`, `write_node_id`, `tool_id`, and `payload_hash`. Hash values are lowercase hexadecimal with an algorithm-qualified external representation when crossing a Port.

### RD-002 CHECK Approval and WRITE Dominance

Every transitive WRITE argument dependency and every required CHECK must be evaluated before the related Approval Request is created. The Approval Statement must dominate its WRITE and the relation is exactly 1:1. Unrelated pure, READ, or CHECK statements may remain between APPROVE and WRITE only when they cannot change the frozen WriteIntent or the required validation summary.

The strict profile does not invent a CHECK when Source declares none. An empty required-check set is vacuously all-pass, but it does not bypass Approval, current PolicyDecision, trust, idempotency, or write-time Business Preconditions.

### RD-003 Version-aware Lexer Bootstrap

Compilation first performs a bounded header scan that recognizes only `language NSL "<version>";`, comments, whitespace, and source locations. The declared version then selects the full keyword table and Parser mode. The compiler option cannot override it. Missing, duplicate, malformed, unsupported, or non-leading language declarations fail before full tokenization. Language 0.1 keeps its original identifier behavior; language 0.2 adds `write`, `approve`, and `approved_by`.

### RD-004 Single-consumer Resume

`checkpoint_version` is an optimistic-concurrency token, not informational metadata. Resume must atomically claim the exact `(execution_id, checkpoint_version)` before mutable runtime state is restored. A stale version, an existing live claim, or an already consumed checkpoint is rejected before Approval transition or provider invocation. Claim release, completion, and recovery are persisted through an injected Checkpoint Port; in-memory locking alone is insufficient.

### RD-005 Clock and Expiration

Runtime time is obtained only from an injected clock. Persisted and signed instants are timezone-aware UTC values. Naive timestamps are invalid. Evidence is expired when `clock.now() >= expires_at`; `expires_at=None` means the evidence has no time expiry but does not waive write-time Business Preconditions. Replay uses recorded time evidence and does not consult the live clock for a historical decision.

### RD-006 Approval Evidence Integrity

The `evidence_integrity` string is opaque evidence, not proof by presence. An injected verifier validates authenticity and binds the decision to tenant, execution, BusinessOperationId, ApprovalId, ApprovalRequestId, skill semantic hash, payload hash, write intent hash, decision, issuer, decision time, and expiry. Missing, invalid, expired, or mismatched evidence cannot produce `ALLOWED`.

### RD-007 Two-stage Business Preconditions

Runtime WriteGuard validates that required precondition evidence is present, correctly bound, and fresh enough to invoke the provider. The Canonical Business API then enforces the actual current business precondition atomically with the WRITE through a version, revision, ETag, transactional revalidation, or equivalent mechanism. Runtime pre-call validation alone never claims that the provider-side condition passed.

### RD-008 Status and Outcome Transitions

| Condition | Durable execution result |
|---|---|
| Required CHECK is FAIL or UNKNOWN | no Approval-to-WRITE transition; controlled blocked result |
| Approval missing or pending after all required CHECKs pass | `WAITING_FOR_APPROVAL` with durable checkpoint |
| Approval rejected | completed business outcome with no WRITE |
| Approval expired | new Approval Request required; no WRITE |
| Provider returns `COMMITTED` | preserve `COMMITTED`; run optional verification |
| Provider returns `NOT_COMMITTED` | controlled write failure; no automatic new-key retry |
| Provider outcome is uncertain | `RECONCILIATION_REQUIRED` with `UNKNOWN_COMMIT_STATE` |
| Verification fails after commit | keep `COMMITTED`; report typed verification failure |

Queue states such as `RESUME_QUEUED` and internal attempt states such as `WRITE_IN_PROGRESS` are not public durable `ExecutionStatus` values.

### RD-009 Idempotency Retention and Recovery

The provider idempotency retention declared by the Tool Contract must cover the platform's maximum suspend, resume, retry, and reconciliation horizon. Runtime must not invoke a WRITE when that guarantee is absent or known to have expired. Such an operation requires reconciliation or an explicit new BusinessOperationId chosen by the Platform; Runtime does not silently mint a new key.

### RD-010 Canonical Package Trust Evidence

The runtime-facing `PackageTrustEvidence` contains `package_id`, `package_hash`, `publisher_id`, `signing_key_id`, `verified_at`, `trust_status`, and `trust_policy_id`. Production WRITE requires `trust_status=VERIFIED` under the referenced trust policy. `trust_status` and `trust_policy_id` are complementary and neither field replaces the other.

### RD-011 Preserved Coverage Quality

The v0.2 development and release gate requires statement coverage of at least 99 percent and branch coverage of at least 98 percent. This preserves the verified v0.1 quality level and intentionally strengthens the historical v0.1 SRS floor of 95 percent and 90 percent. A change that falls below either independent threshold cannot be committed or pushed.

## 6. Quality Gate

Every v0.2 change must keep both requirement baselines valid and run exactly:

```powershell
.\.venv\Scripts\python.exe tools\run_quality.py
```

The gate requires zero regression failures, statement coverage at least 99 percent, and branch coverage at least 98 percent. It also verifies the v0.2 authority hashes, Change Ledger coverage, and all RD-001 through RD-011 evidence anchors.
