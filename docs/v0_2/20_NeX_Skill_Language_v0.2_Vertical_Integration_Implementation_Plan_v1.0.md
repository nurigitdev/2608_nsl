# NSL v0.2 Vertical Integration Implementation Plan

**Version:** 1.0<br>
**Vertical Integration slices:** 0061-0069<br>
**Status:** IN PROGRESS (Slice 0062 COMPLETE)<br>
**Normative requirements:** `03_NeX_Skill_Language_v0.2_Interpreter_Runtime_SRS_v2.0_Draft.md`<br>
**Machine traceability:** `requirements/nsl_v0_2_traceability.json`<br>
**Implementation readiness:** `requirements/nsl_v0_2_readiness.json`

## 1. Purpose

The Vertical Integration phase connects the independently verified v0.2
Compiler, Runtime, Approval, WRITE, Trust, Audit, and Replay boundaries into
complete business scenarios. It proves the architecture first with Voucher
Draft, verifies cross-domain reuse with Project Draft, exposes an explicit and
safe CLI developer workflow, and closes the v0.2 release with architecture,
compatibility, performance, negative-acceptance, and SRS certification.

This phase does not add Agent or Platform workflow orchestration, Production
approval routing, user interface behavior, provider-specific network or SQL
clients, key provisioning, business reconciliation decisions, or automatic
Production approval.

The 123-requirement SRS baseline is unchanged. `AC-W01` and `AC-W02` are
Acceptance Cases, and `RD-011` is a FROZEN readiness decision; none of them is
a new SRS Requirement ID.

## 2. Rebaseline Decisions

The four remaining coarse roadmap items previously numbered 0061-0064 are
redistributed into nine independently verifiable Slices:

- Voucher Draft is divided into READ/CHECK validation, Approval lifecycle,
  and trusted WRITE/Commit/Verification completion.
- Project Draft is divided into its domain contract and validation scenario,
  followed by cross-domain end-to-end reuse acceptance.
- CLI development is divided into execution/resume workflow and
  trust/mock/diagnostic workflow.
- Final certification is divided into architecture/backward compatibility and
  performance/negative acceptance/final SRS audit.

This redistribution changes only future target Slice allocation and
verification planning. Requirement text, priority, membership, and the SRS
fingerprint remain unchanged. This document supersedes the remaining-roadmap
table in section 10 of the Trust and Evidence Implementation Plan.

`NSL-ARC-201` retains Slice 0033 as its implementation owner. Slice 0068 adds
the final adoption and dependency evidence needed to close its current
`PARTIAL` status. `NSL-ARC-202..203` move from Slice 0064 to Slice 0068, and
`RD-011` moves to the final certification boundary at Slice 0069.

## 3. Ownership Boundaries

- Scenario composition may connect public Compiler and Runtime contracts but
  must not bypass or duplicate their internal safety checks.
- Business fixtures, fake approvals, recorded outcomes, and Mock Tool behavior
  belong to examples and test harnesses, not Production Runtime policy.
- Voucher and Project integrations use canonical Tool contracts and injected
  Ports. They must not add domain-specific branches to the common Runtime.
- Approval routing, notification, scheduling, and user interaction remain in
  the Agent or Platform Control Plane.
- Production CLI execution cannot auto-approve a WRITE. A fake approval is
  allowed only through an explicit development or test profile.
- Runtime remains independent of Compiler AST, LLMs, NeX-AE, web frameworks,
  direct filesystem access, direct network access, SQL, and provider SDKs.
- Final certification may discover a defect but cannot substitute a waiver or
  documentation claim for missing implementation evidence.

## 4. Dependency Order

```text
0060 Replay and Mock No-live-write Safety
  -> 0061 Voucher Scenario Contract and READ Validation
  -> 0062 Voucher Approval Suspend and Resume
  -> 0063 Voucher Write Commit and Verification E2E
  -> 0064 Project Scenario Contract and Validation
  -> 0065 Project Draft Generalization E2E
  -> 0066 CLI v0.2 Run and Resume Workflow
  -> 0067 CLI Trust Inspection Mock and Diagnostics
  -> 0068 Architecture and Backward Compatibility Certification
  -> 0069 Performance Negative Acceptance and SRS Certification
```

Each Slice is an independent structural review, implementation, regression,
traceability, commit, and synchronization unit.

## 5. Vertical Integration Slices

| Slice | Status | Title | Primary authority | Completion boundary |
|---|---|---|---|---|
| 0061 | COMPLETE | Voucher Scenario Contract and READ Validation | `AC-W01` prefix, SRS sections 10, 13, and 26 | Canonical Voucher source, Tool contracts, profiles, and fixtures deterministically exercise account, project, period, amount, and partial-data validation without a WRITE call. |
| 0062 | COMPLETE | Voucher Approval Suspend and Resume | `AC-W01` Approval path | A passing Voucher validation requests approval, durably suspends, and resumes only from valid evidence; rejection, expiry, tampering, and duplicate resume produce zero WRITE calls. |
| 0063 | PLANNED | Voucher Write Commit and Verification E2E | `AC-W01` | Trusted approval proceeds through idempotent WRITE, durable request evidence, commit outcome, `FINANCE.GET_VOUCHER` verification, Audit/Replay evidence, and structured `voucher_no` output. |
| 0064 | PLANNED | Project Scenario Contract and Validation | `AC-W02` prefix, SRS section 26 | Canonical Project source, Tool contracts, profiles, and fixtures exercise duplicate, parent, period/date-range, team-authority, and partial-data validation without domain logic in Runtime. |
| 0065 | PLANNED | Project Draft Generalization E2E | `AC-W02` | The same public Runtime pipeline performs approval, idempotent Project WRITE, verification, Audit/Replay evidence, and structured `project_code` output without a Project-specific Runtime branch. |
| 0066 | PLANNED | CLI v0.2 Run and Resume Workflow | SRS section 24 | CLI compile/run exposes suspension evidence and an explicit resume workflow while preserving existing v0.1 command behavior and prohibiting implicit Production approval. |
| 0067 | PLANNED | CLI Trust Inspection Mock and Diagnostics | SRS section 24 | CLI diagnoses Signed NSP trust, runs explicit Mock/Fake adapters, emits stable safe errors and exit codes, and preserves pretty deterministic JSON output. |
| 0068 | PLANNED | Architecture and Backward Compatibility Certification | `NSL-ARC-202..203`, SRS section 27 | Runtime dependency tests close `NSL-ARC-201` adoption evidence, implement `NSL-ARC-202..203`, and certify all v0.1 Source, IR, Context, Status, Result, and Replay compatibility paths. |
| 0069 | PLANNED | Performance Negative Acceptance and SRS Certification | SRS section 25, `RD-011` | Performance and resource boundaries, the complete negative-acceptance matrix, 123-requirement evidence, and the 99/98 quality gate pass with no `PARTIAL` or `PLANNED` SRS requirement remaining. |

## 6. Slice Boundaries

### 6.1 Slice 0061: Voucher Scenario Contract and READ Validation

#### Part A: Canonical Scenario Assets

- Add a canonical v0.2 Voucher Draft `.ns` example and deterministic input,
  context, Tool Contract, principal, profile, and Mock fixture assets.
- Use only the canonical Tool names defined by the accepted scenario:
  `ACCOUNT.GET_ACCOUNT`, `PROJECT.GET_PROJECT`,
  `FINANCE.GET_ACCOUNTING_PERIOD`, `FINANCE.CREATE_VOUCHER_DRAFT`, and
  `FINANCE.GET_VOUCHER`.
- Keep credentials, tenant endpoints, and customer-specific policy values out
  of Source, IR, fixtures, trace output, and Replay bundles.

#### Part B: READ and CHECK Acceptance

- Exercise valid account, valid project, open period, and valid amount paths.
- Fail safely for invalid account, invalid project, closed period, invalid
  amount, missing data, malformed data, partial reads, authorization failure,
  tenant mismatch, and dependency failure.
- Preserve strict false-pass prevention: partial or unknown evidence cannot
  become a passing validation summary.

#### Part C: No-write Boundary

- Compile and run the validation prefix deterministically.
- Prove that every success and failure case in this Slice invokes the WRITE
  provider zero times.

**Verification:** `TEST-AC-W01-READ-VALIDATION`.<br>
**Completion evidence:**
`examples/voucher_draft_validation/voucher_draft_validation.ns`,
`examples/voucher_draft_validation/tools.json`,
`examples/voucher_draft_validation/profile.json`,
`examples/voucher_draft_validation/scenarios.json`,
`tests/test_v0_2_voucher_validation.py::test_ac_w01_v0_2_read_contract_compiles_deterministically`,
`tests/test_v0_2_voucher_validation.py::test_ac_w01_invalid_reference_data_fails_only_its_check`,
`tests/test_v0_2_voucher_validation.py::test_ac_w01_amount_boundary_zero_and_one`,
`tests/test_v0_2_voucher_validation.py::test_ac_w01_incomplete_read_cannot_false_pass`,
`tests/test_v0_2_voucher_validation.py::test_ac_w01_missing_malformed_and_dependency_data_fail_closed`,
`tests/test_v0_2_voucher_validation.py::test_ac_w01_authorization_and_missing_input_stop_before_tools`,
`tests/test_v0_2_voucher_validation.py::test_ac_w01_tenant_mismatch_and_dependency_crash_are_isolated`,
`tests/test_v0_2_voucher_validation.py::test_ac_w01_direct_runtime_rejects_controlled_nodes_before_tool_call`,
and
`tests/test_v0_2_voucher_validation.py::test_ac_w01_cli_scenario_suite_is_deterministic_and_no_write`.

### 6.2 Slice 0062: Voucher Approval Suspend and Resume

#### Part A: Approval Request and Suspension

- Convert only a complete passing validation summary into
  `PreWriteEligibility` and an Approval Request.
- Persist the checkpoint before publishing approval and release the worker
  without polling or blocking.
- Prove idempotent publication and deterministic protected evidence identity.

#### Part B: Resume Validation

- Accept approved evidence only when request, operation, intent, payload,
  principal, tenant, policy, package, skill, expiry, and checkpoint bindings
  match the current execution.
- Cover rejection, expiry boundaries, malformed or tampered evidence,
  duplicate resume, cross-tenant claims, and dependency failure.

#### Part C: Pre-write Safety

- Stop before provider WRITE execution in this Slice.
- Prove that suspend, reject, expire, tamper, duplicate, and failed-resume
  paths all invoke the WRITE provider zero times.

**Verification:** `TEST-AC-W01-APPROVAL-LIFECYCLE`.<br>
**Completion evidence:**
`examples/voucher_draft_validation/approval.json`,
`nsl/runtime.py::RuntimeEngine.execute_checkpointed`,
`nsl/approval.py::ApprovalSuspensionCoordinator`,
`nsl/approval.py::ApprovalResumeCoordinator`,
`tests/test_v0_2_voucher_approval_lifecycle.py::test_ac_w01_passing_validation_persists_before_approval_publication`,
`tests/test_v0_2_voucher_approval_lifecycle.py::test_ac_w01_nonpassing_validation_cannot_publish_approval`,
`tests/test_v0_2_voucher_approval_lifecycle.py::test_ac_w01_valid_evidence_resumes_once_without_write`,
`tests/test_v0_2_voucher_approval_lifecycle.py::test_ac_w01_rejection_and_expiry_boundaries_never_write`,
`tests/test_v0_2_voucher_approval_lifecycle.py::test_ac_w01_tampering_and_cross_tenant_evidence_release_claim`,
`tests/test_v0_2_voucher_approval_lifecycle.py::test_ac_w01_resume_dependency_failure_releases_for_retry`,
and
`tests/test_v0_2_voucher_approval_lifecycle.py::test_ac_w01_checkpoint_failure_blocks_publish_and_publish_retry_is_idempotent`.

### 6.3 Slice 0063: Voucher Write Commit and Verification E2E

#### Part A: Trusted Write Execution

- Resume from valid Approval evidence under current package, skill, principal,
  tenant, and runtime policy evidence.
- Re-evaluate WriteGuard and provider business preconditions immediately
  before WRITE.
- Reuse the same deterministic idempotency key across resume and retries, and
  persist `WRITE_REQUESTED` evidence before provider invocation.

#### Part B: Commit and Verification

- Preserve committed, rejected/not-committed, and indeterminate/unknown commit
  outcomes without unsafe automatic retry.
- Invoke `FINANCE.GET_VOUCHER` only after a committed outcome and keep
  verification status independent from commit state.
- Emit a structured result containing `voucher_no` only when allowed by the
  confirmed business and verification outcome.

#### Part C: Evidence Completion

- Persist the required Approval, WRITE, Commit, and Verification Audit events.
- Seal protected Replay evidence and prove deterministic no-live-write Replay.
- Cover happy path, approval rejection/expiry, idempotent resume, unknown
  commit, verification failure, partial data, and dependency failure.

**Planned verification:** `TEST-AC-W01-E2E`.

### 6.4 Slice 0064: Project Scenario Contract and Validation

#### Part A: Canonical Scenario Assets

- Add a canonical v0.2 Project Draft `.ns` example and deterministic scenario
  assets using `PROJECT.GET_PROJECT`, `PROJECT.CHECK_DUPLICATE`, and
  `PROJECT.CREATE_PROJECT_DRAFT` plus the declared read-only verification
  contract.
- Keep all Project-specific rules in Source, Tool contracts, or injected Mock
  behavior rather than common Runtime branches.

#### Part B: Validation Acceptance

- Validate active parent, duplicate absence, valid period/date range, and team
  authority.
- Cover empty, single, maximum, and over-limit inputs together with duplicate,
  inactive parent, invalid period/date range, unauthorized team, partial data,
  tenant mismatch, and dependency failure.

#### Part C: No-write Boundary

- Compile and execute the validation prefix deterministically.
- Prove that all validation scenarios invoke the WRITE provider zero times.

**Planned verification:** `TEST-AC-W02-VALIDATION`.

### 6.5 Slice 0065: Project Draft Generalization E2E

#### Part A: Shared Pipeline Reuse

- Execute the Project scenario through the same Approval, checkpoint, resume,
  WriteGuard, idempotency, durable request, Commit, Verification, Audit, and
  Replay contracts used by Voucher.
- Reject any implementation that adds a Project-specific conditional to the
  common Runtime.

#### Part B: Project Outcomes

- Emit `project_code` for the accepted committed scenario.
- Preserve rejected and indeterminate commit states and independent failed or
  unknown verification states.
- Cover duplicate, parent, period/date-range, team-authority, approval,
  idempotency, provider, verification, Audit, and Replay failures.

#### Part C: Generalization Proof

- Run Voucher and Project acceptance scenarios in the same regression suite.
- Prove that domain variation is supplied by Source, contracts, and Port data,
  not by Runtime ownership changes.

**Planned verification:** `TEST-AC-W02-E2E`,
`TEST-V02-CROSS-DOMAIN-GENERALIZATION`.

### 6.6 Slice 0066: CLI v0.2 Run and Resume Workflow

#### Part A: Compatibility and Invocation

- Preserve existing `parse`, `check`, `compile`, `inspect`, `run`, `replay`,
  and `test` command behavior.
- Compile Source Language 0.1 to IR 1.0 and Source Language 0.2 to IR 1.1
  deterministically through the same public compiler boundary.

#### Part B: Suspension and Resume

- Make a suspended run return a structured result and protected references
  required for a later explicit resume command.
- Add a resume workflow that consumes checkpoint and Approval evidence through
  the public Runtime contract.
- Never treat a process-local object or transient CLI memory as durable
  approval state.

#### Part C: Approval Safety

- Reject Production WRITE when no external Approval evidence is supplied.
- Permit fake approval only through an explicit development or test profile
  and make that profile visible in structured output and Audit evidence.

**Planned verification:** `TEST-V02-CLI-RUN-RESUME`.

### 6.7 Slice 0067: CLI Trust Inspection Mock and Diagnostics

#### Part A: Package Trust Diagnostics

- Provide `verify` and signature/trust inspection behavior for Signed NSP and
  Package Trust Evidence without inventing a second `.nso` signature scheme.
- Report package, publisher, signing key, trust policy, artifact, and semantic
  identity using display-safe structured fields.

#### Part B: Explicit Test Adapters

- Load Mock Tool, Fake Approval, recorded WRITE, and Replay behavior only from
  an explicit development or test profile.
- Prove that these adapters cannot silently weaken Production preflight.

#### Part C: Safe Developer Experience

- Keep deterministic pretty JSON on stdout and structured user-safe errors on
  stderr with stable exit-code classes.
- Cover missing files, malformed JSON, invalid package/signature, bad
  checkpoint/evidence, authorization failure, timeout, and dependency failure
  without exposing credentials, raw protected payloads, or tracebacks.

**Planned verification:** `TEST-V02-CLI-TRUST-DIAGNOSTICS`.

### 6.8 Slice 0068: Architecture and Backward Compatibility Certification

#### Part A: Architecture Closure

- Prove Compiler and Runtime consume the shared Core and IR semantic models,
  adding final adoption evidence for `NSL-ARC-201` without moving its Slice
  0033 implementation ownership.
- Implement and verify `NSL-ARC-202`: Runtime has no Compiler AST dependency.
- Implement and verify `NSL-ARC-203`: Runtime does not invoke an LLM.
- Re-run all module dependency, forbidden import, direct infrastructure, Tool
  Port, and Agent/Platform ownership checks.

#### Part B: v0.1 Compatibility

- Certify v0.1 Source compilation under v0.2, Language 0.1 to IR 1.0 mapping,
  IR 1.0 loading and execution, Principal/DataPolicy preservation,
  `ExecutionStatus` compatibility including `VALIDATION_ERROR`, structured
  Result compatibility, and deterministic Replay.
- Certify deterministic Language 0.2 to IR 1.1 mapping, including write-less
  v0.2 Source.

#### Part C: Negative Architecture Acceptance

- Attempt forbidden AST, Compiler, LLM, filesystem, network, SQL, provider,
  workflow-orchestration, and web-framework dependency paths and prove they
  fail the architecture gate.

**Primary requirements:** `NSL-ARC-202..203`.<br>
**Closure evidence:** `NSL-ARC-201`.<br>
**Planned verification:** `TEST-V02-ARCHITECTURE-CERTIFICATION`,
`TEST-V02-BACKWARD-COMPATIBILITY`.

### 6.9 Slice 0069: Performance Negative Acceptance and SRS Certification

#### Part A: Performance and Resource Boundaries

- Measure parsing, compilation, NSO loading, Runtime overhead, Tool timing,
  protected evidence handling, and representative 1,000-iteration execution
  using deterministic fixtures and documented environments.
- Apply `0`, `1`, `MAX-1`, `MAX`, and `MAX+1` boundaries to relevant input,
  collection, evidence, checkpoint, Audit, and Replay limits.

#### Part B: Complete Negative Acceptance

- Execute every mandatory negative test listed in SRS section 25 together with
  malformed input, invalid state, missing data, dependency failure,
  authorization, tenant isolation, partial data, replay, approval, trust,
  idempotency, commit-unknown, verification, persistence, and crash-isolation
  cases.
- Prove that no negative path creates an unapproved or duplicate business
  side effect and that unknown outcomes are never reported as success.

#### Part C: Final Certification

- Audit all 123 SRS requirements against named repository evidence.
- Require zero `PARTIAL` and zero `PLANNED` SRS requirements before release.
- Certify the independent `RD-011` statement and branch coverage thresholds.
- Produce the final Requirement, Acceptance Case, compatibility, performance,
  and negative-acceptance evidence summary without changing the SRS
  fingerprint.

**Governing decision:** `RD-011`.<br>
**Planned verification:** `TEST-V02-PERFORMANCE-ACCEPTANCE`,
`TEST-V02-NEGATIVE-ACCEPTANCE`, `TEST-V02-SRS-CERTIFICATION`.

## 7. Acceptance Verification Model

Acceptance Cases use verification records rather than synthetic Requirement
IDs:

| Acceptance case | Prefix verification | Lifecycle verification | Completion verification |
|---|---|---|---|
| `AC-W01` Voucher Draft | `TEST-AC-W01-READ-VALIDATION` | `TEST-AC-W01-APPROVAL-LIFECYCLE` | `TEST-AC-W01-E2E` |
| `AC-W02` Project Draft | `TEST-AC-W02-VALIDATION` | Reuses the certified Approval lifecycle | `TEST-AC-W02-E2E` |

An Acceptance Case is complete only when its final end-to-end verification
passes and all failure scenarios prove their expected provider call count,
commit state, verification state, Audit evidence, and Replay behavior.

## 8. Cross-slice Safety Invariants

- A failing, partial, unknown, unauthorized, or cross-tenant validation cannot
  create `PreWriteEligibility` or invoke a WRITE provider.
- An Approval Request is published only after durable checkpoint persistence.
- Rejection, expiry, tampering, duplicate resume, and evidence mismatch invoke
  the WRITE provider zero times.
- Production WRITE requires current verified package and skill trust evidence.
- `WRITE_REQUESTED` is durable before the canonical provider is invoked.
- An uncertain provider outcome is not retried automatically and is never
  emitted as committed success.
- Post-write verification is read-only and cannot change CommitState.
- Generic Result, Audit, CLI, and Replay records never expose credentials or
  raw protected WRITE payloads.
- Replay and Mock execution cannot reach a Live WRITE provider.
- Voucher and Project domain behavior cannot enter common Runtime control
  flow as hard-coded branches.

## 9. Test Design

Every Slice applies Boundary Value Analysis, robustness, worst-case, negative
path, authorization, tenant-isolation, partial-data, dependency-failure,
state-transition, and Replay cases where relevant.

Required collections and resource-bound cases include empty, one-item,
`MAX-1`, `MAX`, and `MAX+1`. Required state cases include pass, fail, unknown,
suspend, approve, reject, expire, committed, not committed, indeterminate,
verification success/failure/unknown, persistence failure, duplicate
invocation, tampered evidence, and process or dependency failure.

The complete regression suite, including all v0.1 unit, regression, golden,
CLI, package, and Replay tests, runs after every Slice.

## 10. Traceability and Status Rules

- `IMPLEMENTED` requires complete repository behavior and named automated
  evidence for the individual Requirement ID.
- `PARTIAL` is used only when useful behavior exists but a requirement still
  depends on a later Slice.
- `PLANNED` remains when no complete repository evidence exists.
- Acceptance Cases and FROZEN decisions receive named verification evidence
  but do not increase the 123-requirement baseline.
- `NSL-ARC-201` remains owned by Slice 0033 while Slice 0068 supplies closure
  evidence. `NSL-ARC-202..203` target Slice 0068. `RD-011` targets Slice 0069.
- Requirement membership, text, priority, and fingerprint are unchanged by
  this Slice redistribution.
- Slice 0069 cannot pass while any SRS requirement remains `PARTIAL` or
  `PLANNED`.

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
