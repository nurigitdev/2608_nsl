# NSL v0.2 Trust and Evidence Implementation Plan

**Version:** 1.0<br>
**Trust and Evidence slices:** 0055-0060<br>
**Status:** IN PROGRESS (Slices 0055-0059 complete)<br>
**Normative requirements:** `03_NeX_Skill_Language_v0.2_Interpreter_Runtime_SRS_v2.0_Draft.md`<br>
**Machine traceability:** `requirements/nsl_v0_2_traceability.json`

## 1. Purpose

The Trust and Evidence phase closes the security and evidence boundaries that
surround a v0.2 WRITE after the core Approval and Write Execution phases. It
establishes reusable Signed NSP trust evidence, blocks untrusted Production
WRITE execution, performs independent post-write verification, persists the
required v0.2 audit evidence, and guarantees that Replay cannot repeat a live
business side effect.

This phase contains 17 SRS requirements across six independently testable
Slices. `RD-010` is a FROZEN implementation decision and does not increase the
123-requirement baseline.

This phase does not implement publisher key provisioning, certificate
authority operations, tenant entitlement decisions, business reconciliation
decisions, provider-specific database or network clients, or Agent/Platform
workflow orchestration.

## 2. Rebaseline Decisions

The original four-Slice outline is redistributed for the following reasons:

- Slice 0054 is already complete as Commit Outcomes and Reconciliation, so
  this phase starts at Slice 0055.
- `NSL-IR-209` is part of the Signed NSP trust boundary and must be traced with
  the package trust requirements.
- Trust evidence construction and Production WRITE authorization are separate
  responsibilities and receive separate Slices.
- `NSL-VFY-004` spans both structured Runtime Result and persistent Audit, so
  it is implemented with their shared evidence integration.
- Protected Replay evidence composition is separate from the hard prohibition
  against Live WRITE execution during Replay.
- SRS section 23 defines a normative Audit event contract but introduces no
  independent `NSL-AUD-*` Requirement IDs. Verification IDs may be added for
  the event contract, but new SRS Requirement IDs must not be invented.

The rebaseline changes only target Slice allocation and verification planning.
Requirement text, priority, membership, and the SRS fingerprint remain
unchanged.

## 3. Ownership Boundaries

- Signed NSP parsing, package integrity, and publisher signature verification
  remain in the NSP verification boundary.
- Runtime consumes immutable package and skill trust evidence. It does not
  reimplement a second signature system for raw IR or `SkillObject` values.
- Production execution policy is provided through an injected profile or
  policy boundary. Runtime modules must not infer Production mode from an
  environment variable or customer endpoint.
- Post-write verification uses an injected read-only verification Port. It
  must not call `WriteExecutionPort` or mutate provider state.
- `CommitState` and `VerificationStatus` are independent typed values.
  Verification cannot rewrite a confirmed commit outcome.
- Audit Core owns event identity, classification, retention, and hash-chain
  semantics. Persistence remains behind the existing Audit sink boundary.
- Replay consumes protected snapshots and recorded outcomes. Replay must not
  receive a usable Live WRITE capability.
- Runtime and Core remain independent of filesystem, network, SQL, MCP, LLM,
  NeX-AE, and web-framework implementations.

## 4. Dependency Order

```text
0054 Commit Outcomes and Reconciliation
  -> 0055 Signed NSP Trust Evidence and Compatibility
  -> 0056 Production WRITE Trust Preflight
  -> 0057 Post-write Verification Core
  -> 0058 Verification Result and Persistent Audit Evidence
  -> 0059 Protected Replay Evidence Composition
  -> 0060 Replay and Mock No-live-write Safety
```

Slice 0056 consumes the immutable evidence produced by Slice 0055. Slice 0057
consumes the explicit commit outcome from Slice 0054. Slice 0058 consumes the
Approval, Write, Commit, and Verification evidence produced by prior phases.
Slices 0059-0060 then compose and replay those protected records without
granting a live side-effect capability.

Each Slice is an independent structural review, implementation, regression,
traceability, commit, and synchronization unit.

## 5. Trust and Evidence Slices

| Slice | Status | Title | Primary requirements | Governing decision | Completion boundary |
|---|---|---|---|---|---|
| 0055 | COMPLETE | Signed NSP Trust Evidence and Compatibility | `NSL-IR-209`, `NSL-SIG-002..004`, `NSL-SIG-006` | `RD-010`, `CHG-010`, `CHG-031` | Canonical NSP integrity and trusted publisher signature verification produce immutable package/skill trust evidence compatible with the v0.1 trust boundary. |
| 0056 | COMPLETE | Production WRITE Trust Preflight | `NSL-SIG-001`, `NSL-SIG-005`, `NSL-ARC-210` | `RD-010`, `CHG-010` | Production WRITE fails closed before provider invocation unless current verified package and skill trust evidence matches the exact executable artifact and trust policy. |
| 0057 | COMPLETE | Post-write Verification Core | `NSL-VFY-001..003`, `NSL-VFY-005` | `CHG-029` | A committed WRITE can invoke a declared read-only verification method and return an independent typed verification outcome without changing CommitState. |
| 0058 | COMPLETE | Verification Result and Persistent Audit Evidence | `NSL-VFY-004`, SRS section 23 | `CHG-029`, `CHG-030` | Verification appears in structured Result and persistent hash-chained Audit together with the complete v0.2 event taxonomy and protected identity references. |
| 0059 | COMPLETE | Protected Replay Evidence Composition | `NSL-RPL-202` | `CHG-025` | Approval, Write, Commit, and Verification evidence can be assembled into a deterministic tenant-bound protected Replay bundle. |
| 0060 | PLANNED | Replay and Mock No-live-write Safety | `NSL-RPL-201`, `NSL-RPL-203`, `NSL-ARC-207` | `CHG-025` | Replay accepts only recorded or mock outcomes, cannot access a Live WRITE provider, and cannot repeat the original business side effect. |

## 6. Slice Boundaries

### 6.1 Slice 0055: Signed NSP Trust Evidence and Compatibility

#### Part A: Existing Trust Boundary Review

- Reuse the v0.1 NSP canonical package hash, signature metadata, trusted-key
  verification, and package verification Ports.
- Preserve unsigned development-package behavior only where an explicit
  non-Production policy permits it.
- Reject duplicate, ambiguous, or non-canonical signature metadata.

#### Part B: Immutable Trust Evidence

- Define immutable runtime-facing package and skill trust evidence containing
  `package_id`, `package_hash`, `publisher_id`, `signing_key_id`,
  `verified_at`, `trust_status`, and `trust_policy_id`.
- Bind skill evidence to the exact canonical artifact and semantic identity
  selected from the verified package.
- Keep raw signature bytes and trust-store services outside Runtime context and
  checkpoint state.

#### Part C: Tamper and Compatibility Safety

- Reject untrusted keys, invalid signatures, package hash mismatches, artifact
  substitutions, stale evidence, and cross-package skill evidence.
- Prove that any package or artifact integrity change invalidates prior trust
  evidence.
- Preserve v0.1 Signed NSP loading and verification behavior.

**Completion evidence:** `PackageTrustEvidence` and `VerifiedSkillArtifact`
provide immutable Runtime-facing identities without carrying signature bytes or
trust-store services into IR. The existing canonical NSP verifier remains the
sole cryptographic boundary, and verified artifacts are bound to package,
artifact, and semantic hashes. Automated tests cover trusted Ed25519 keys,
tampering, artifact substitution, malformed evidence, deterministic identity,
and explicitly permitted unsigned v0.1 development packages.

### 6.2 Slice 0056: Production WRITE Trust Preflight

#### Part A: Explicit Execution Profile

- Introduce an explicit trusted execution profile or policy input that
  distinguishes Production WRITE from development/test execution.
- Keep tenant entitlement, MFA, Approval routing, and publisher key management
  outside the profile.

#### Part B: Fail-closed Trust Gate

- Require `trust_status=VERIFIED` under the referenced trust policy for every
  Production WRITE.
- Match package, skill artifact, semantic hash, publisher, signing key, and
  trust policy identities before WRITE eligibility can become ALLOWED.
- Consume verified package/skill evidence instead of re-verifying a raw
  `SkillObject` through a duplicate signature path.

#### Part C: Invocation Ordering

- Evaluate trust before durable `WRITE_REQUESTED` persistence and before any
  provider invocation.
- Preserve existing Approval, policy, precondition, idempotency, and WriteGuard
  requirements; trust evidence is additional and cannot bypass them.
- Prove zero provider calls for missing, malformed, expired, revoked, stale,
  cross-tenant, or artifact-mismatched trust evidence.

**Completion evidence:** `ExecutionTrustProfile` makes Production and
Development policy explicit, while `RuntimePreflightValidator` verifies the
current tenant, package, artifact, semantic hash, publisher, signing key,
trust policy, status, and evidence lifetime. The canonical
`TrustedWriteExecutionCoordinator` performs this check before delegating to
the durable `WRITE_REQUESTED` boundary. Automated negative acceptance proves
zero evidence and provider calls for missing, unverified, revoked, stale,
future, cross-tenant, and identity-mismatched trust evidence.

### 6.3 Slice 0057: Post-write Verification Core

#### Part A: Verification Plan

- Resolve the optional verification method declared by the canonical Tool
  Contract after a confirmed `COMMITTED` result.
- Carry the business reference and the minimum protected provider identity
  required for read-after-write verification.
- Produce `NOT_REQUESTED` when no verification method is declared.

#### Part B: Read-only Verification Port

- Define an injected verification Port that cannot expose WRITE capability.
- Permit only the declared verification method and bind its response to the
  committed write identity.
- Normalize verified, failed, unavailable, malformed, timeout, and dependency
  failure outcomes without using Python truthiness.

#### Part C: Independent State Semantics

- Preserve `CommitState.COMMITTED` when verification is `FAILED` or `UNKNOWN`.
- Distinguish verification failure from commit rejection and commit
  uncertainty in public Runtime decisions.
- Preserve a Business Reference whenever the provider supplied one.

**Completion evidence:** `PostWriteVerifier` consumes only a confirmed
`WriteResultEnvelope`, resolves the optional Tool Contract verification
method, and invokes a dedicated read-only `PostWriteVerificationPort`.
Canonical request hashes bind the complete committed WRITE identity and
Business Reference. Success, mismatch, timeout, malformed response, and
dependency failure produce independent typed verification outcomes while
`CommitState.COMMITTED` remains invariant.

### 6.4 Slice 0058: Verification Result and Persistent Audit Evidence

#### Part A: Structured Result Integration

- Add typed `VerificationStatus` and protected verification evidence to the
  structured v0.2 Result surface.
- Preserve v0.1 Result compatibility and existing commit-state invariants.
- Reject inconsistent combinations such as verification success without a
  committed WRITE identity.

#### Part B: v0.2 Audit Event Contract

- Support the SRS section 23 event set:
  `WRITE_INTENT_CREATED`, `APPROVAL_REQUESTED`, `EXECUTION_SUSPENDED`,
  `APPROVAL_APPROVED`, `APPROVAL_REJECTED`, `EXECUTION_RESUMED`,
  `WRITE_REQUESTED`, `WRITE_COMMIT_CONFIRMED`, `WRITE_COMMIT_UNKNOWN`,
  `WRITE_VERIFIED`, and `WRITE_VERIFICATION_FAILED`.
- Bind every applicable event to execution, business operation, Approval,
  node, skill semantic hash, idempotency key, payload hash, and write intent
  hash.
- Use explicit Audit acceptance Verification IDs without inventing new SRS
  Requirement IDs.

#### Part C: Durable and Protected Evidence

- Preserve append-only sequence, hash-chain, retention, restart verification,
  tenant isolation, and persistence-failure behavior.
- Keep raw WRITE payloads and credentials out of generic events.
- Store protected references, display-safe summaries, hashes, and data
  classification instead.

**Completion evidence:** `WriteAuditRecorder` constrains the complete SRS
section 23 event taxonomy to immutable WRITE identities and protected evidence
fields, validates event-specific Approval and idempotency bindings, and
persists through the existing hash-chained Audit sink. `VerificationResult`
projects to a typed `VerificationSummary`, while requested outcomes are
durably recorded as `WRITE_VERIFIED` or `WRITE_VERIFICATION_FAILED` before the
summary is returned. Acceptance tests cover every event, restart verification,
tamper detection, persistence failure, tenant mismatch, credential rejection,
and the evidence-reference `0/1/MAX/MAX+1` boundary without adding synthetic
SRS requirements.

### 6.5 Slice 0059: Protected Replay Evidence Composition

#### Part A: v0.2 Snapshot Model

- Define immutable snapshot records for Approval, Write request, Commit
  outcome, and Verification outcome evidence.
- Bind each record to tenant, execution, business operation, skill semantic
  hash, payload hash, write intent hash, and idempotency identity as applicable.

#### Part B: Protected Replay Bundle

- Store sensitive values only through protected snapshot references.
- Compose a deterministic bundle manifest with version, ordering, integrity,
  and source execution identity.
- Reject missing, duplicate, cross-tenant, tampered, unsupported-version, and
  partially bound evidence.

#### Part C: Deterministic Rehydration

- Rehydrate recorded Approval, Write, Commit, and Verification outcomes
  without consulting live business systems.
- Preserve `UNKNOWN_COMMIT_STATE` and reconciliation evidence exactly; Replay
  must not reinterpret uncertainty as failure or success.

**Completion evidence:** `WriteReplayEvidenceComposer` validates the complete
lifecycle before writing any snapshot, seals typed Approval, WRITE request,
Commit, and Verification outcomes through `ProtectedSnapshotStore`, and emits
an immutable, ordered, tenant-bound manifest containing only protected
references and hashes. `WriteReplayEvidenceLoader` revalidates principal,
tenant, type, identity, snapshot integrity, request/outcome binding, and
lifecycle consistency while preserving rejected, not-committed,
`UNKNOWN_COMMIT_STATE`, and committed outcomes exactly. Tests cover the
`0/1/MAX/MAX+1` record boundary, missing, duplicate, reordered, partial,
cross-tenant, wrong-type, unsupported-version, and tampered evidence.

### 6.6 Slice 0060: Replay and Mock No-live-write Safety

#### Part A: Capability Isolation

- Construct Replay without a Live `WriteExecutionPort` capability.
- Reject attempts to inject or resolve a live side-effect provider in Replay
  mode.
- Preserve the existing prohibition against direct network, SQL, filesystem,
  and MCP access from Runtime.

#### Part B: Recorded and Mock Outcomes

- Serve Approval, WRITE, Commit, and Verification results only from the
  verified Replay bundle or an explicit test mock.
- Require exact invocation identity and deterministic call ordering.
- Fail closed for missing, extra, reordered, or mismatched recorded calls.

#### Part C: Negative Side-effect Acceptance

- Prove that Replay executes with zero Live WRITE provider calls for committed,
  rejected, unknown, failed-verification, partial-data, and dependency-failure
  scenarios.
- Prove that repeated Replay runs cannot create or mutate a business side
  effect and cannot mint a new idempotency key.
- Keep business reconciliation decisions in the Agent/Platform Control Plane.

## 7. Cross-slice Safety Invariants

- Production WRITE never executes from an unsigned, untrusted, stale,
  mismatched, or unverified package/skill identity.
- Package trust evidence is immutable, tenant-safe where applicable, and bound
  to canonical package and artifact hashes.
- Runtime does not implement a parallel raw-IR signature path.
- Verification is read-only and never changes a confirmed CommitState.
- Audit and Replay never copy raw WRITE payloads or credentials into generic
  records.
- Audit outcome persistence occurs before execution-local evidence is treated
  as durable.
- Replay cannot resolve, receive, or invoke a Live WRITE provider.
- No test or development profile can silently weaken Production behavior.

## 8. Test Design

Every Slice uses Boundary Value Analysis and robustness testing appropriate to
its contracts. Required cases include:

- empty, one-item, maximum-size, and over-limit package and evidence sets;
- exact validity and expiry boundaries for trust evidence;
- untrusted, revoked, unknown, and mismatched signing keys;
- package, artifact, semantic hash, publisher, and trust-policy tampering;
- missing verification method, successful verification, failed verification,
  timeout, malformed response, and dependency failure;
- committed WRITE with `FAILED` or `UNKNOWN` verification;
- every required v0.2 Audit event and every optional identity field;
- audit append failure, restart validation, retention boundary, hash-chain
  corruption, and cross-tenant access;
- complete, partial, duplicate, reordered, tampered, and cross-tenant Replay
  bundles; and
- repeated Replay with an instrumented Live WRITE Port proving zero calls.

Tests must explicitly cover empty and malformed protected references, maximum
evidence counts, persistence failure, authorization failure, partial data,
worst-case Replay size, and unexpected dependency exceptions.

## 9. Traceability and Status Rules

- `IMPLEMENTED` requires complete repository behavior and named automated
  evidence for the individual Requirement ID.
- `PARTIAL` is used only when useful behavior exists but an assigned contract
  still depends on a later Slice.
- `PLANNED` remains when no complete repository evidence exists.
- FROZEN decisions receive explicit tests but do not increase the SRS
  requirement count.
- The SRS section 23 Audit event list receives named acceptance tests and
  documentation evidence without synthetic Requirement IDs.
- Requirement membership, text, priority, and fingerprint are unchanged by
  this Slice redistribution.

## 10. Subsequent Slice Renumbering

The two additional Trust and Evidence boundaries shift the remaining roadmap:

| Previous Slice | New Slice | Title |
|---|---|---|
| 0059 | 0061 | Voucher Draft Vertical Slice |
| 0060 | 0062 | Project Draft Vertical Slice |
| 0061 | 0063 | CLI v0.2 Developer Experience |
| 0062 | 0064 | Architecture Compatibility and SRS Certification |

## 11. Quality Gate

After each Slice, run exactly:

```powershell
.\.venv\Scripts\python.exe tools\run_quality.py
```

The required result is zero regression failures, statement coverage of at
least 99.00 percent, and branch coverage of at least 98.00 percent. Pytest
temporary files and coverage artifacts remain under the repository-local
`test/` directory. A Slice is not committed or synchronized when this gate
fails.
