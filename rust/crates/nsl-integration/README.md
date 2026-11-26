# R8-S05 — Pre-invocation safety gate

R8-EXIT composes the native R8 boundaries against frozen Python Oracle
**28a949ec96581cbdc52c8133527b23bb5b72ee0a**. The integration crate adds only
**development** dependencies on the existing Core/IR/Contract/Runtime/WRITE
workspace crates and reviewed SHA-256 package. Its production library remains
empty. There is no business-provider, HTTP, SQL, filesystem-storage, commit-state
or post-WRITE verification implementation in this Slice.

## Required guard binding and approved difference

The combined gate exposed a gap in both the frozen Oracle and the R8-S03 native
Guard: current contract evidence could be self-consistent while referring to a
contract different from the one declared in the already approved Skill. A real
Oracle voucher prefix/intent was retained, the contract timeout changed from
30,000 to 30,001 ms, and policy/precondition/reservation evidence was rebound to
the new contract. Both old boundaries returned ALLOWED and the mock route reached
one invocation despite differing declared/current contract hashes.

The user approved preserving Oracle behavior as an explicit difference while
closing this native boundary. `WriteGuardRequest` now **requires `skill`**: the
immutable schema/integrity-admitted Skill from the current Runtime context. Both
`evaluate` and `authorize` require the same field; there is no optional/unbound
Guard mode. After existing principal/context/current-contract/scope/risk checks,
and before policy-port access, Guard requires:

* the intent's `skill_semantic_hash` equals the admitted Skill's semantic hash;
* exactly one declaration matches the intent's `tool_ref`;
* declaration tool ID, version, canonical contract hash, WRITE capability,
  risk level and required scope match the actual current contract.

Semantic drift returns BLOCKED / `WRITE_SKILL_BINDING_MISMATCH`; missing,
ambiguous or foreign declarations return BLOCKED /
`WRITE_DECLARED_CONTRACT_MISMATCH`. These paths have zero policy, reservation,
storage or mock-provider calls. They require recompilation against the current
contract and a new approval for the resulting intent. The recovery pair verifies
that a recompiled Skill/new intent still cannot proceed with its old approval,
then succeeds with matching new approval evidence.

The borrowed Skill must come from the existing NSO/Runtime admission boundary.
Guard does not authenticate a caller-created raw Skill or manufacture package
trust from its stored hash. The full gate uses strict `NsoCodec::decode`, which
checks canonical schema, resource bounds and semantic integrity; the immutable
borrow remains valid throughout Guard evaluation. Earlier S03 predicate tests
retain their original observation bytes and use explicit abstract unit Skill
projections to isolate predicate comparisons. Those projections are not claimed
as admitted wire artifacts. No original Oracle fixture is patched to a native
outcome and no Python implementation or Oracle lock is changed.

## Composition and evidence

The test pipeline uses a genuine frozen compiled voucher Skill (IR 1.1), its
recorded evaluated argument envelopes and prefix predicate observations. Native
`StrictRuleEvaluator` and `ValidationSummary` recreate CHECK results, exact counts,
status and reasons; R6's existing gates separately certify expression/prefix
execution. This Slice does not replace the Runtime expression evaluator with a
second implementation.

The remaining path is composed from actual public components:

```text
strict NSO admission
→ StrictRuleEvaluator / ValidationSummary
→ WriteIntentBuilder
→ ApprovalEvidenceValidator + injected verifier/clock
→ WriteGuard::authorize + injected current-policy port
→ IdempotencyCoordinator + injected atomic reservation port
→ WriteCallRequestBuilder
→ WriteRequestedCoordinator + durable receipt checks
→ private DurableWriteRequest consumed by a test-only provider spy
```

The policy port checks the exact eight-field request projection against the
original Oracle before returning its decision. The spy can consume only a
validated durable handoff. All adapters are deterministic in-memory test ports;
there is no real business mutation, production storage durability claim or new
provider execution API. Actual database durability, authenticated policy/proof
issuers and later live orchestration retain their documented host contracts.
The handoff certifies persistence, not authority after arbitrary time passes;
actual provider orchestration and commit/uncertainty semantics remain R9.

The normal corpus contains **54 scenarios**, including **6 positive controls**:
normal pass, explicit no-CHECK Skill, one microsecond before each approval/policy/
precondition expiry, and recompile with new approval. Failed/unknown/mixed CHECKs,
invalid/rejected/expired/foreign approval, policy deny/foreign/future/expiry,
missing/foreign/stale/non-atomic preconditions, reservation conflict, unavailable
retention, storage errors and invalid receipts prevent the mock invocation.
Old `write_request_durable = true` flags cannot bypass a new failed receipt.

For all 54 cases, exact CHECK records, validation summaries, approval validation,
Guard eligibility/reason, terminal phase, record SHA-256, flag preservation,
policy/reservation/storage/provider counts and event order match the original
Oracle. Guard/approval/CHECK/policy/precondition failures produce zero reservation
and storage calls. Idempotency conflict produces zero requested-evidence storage
and zero mock invocation. Successful paths require `WRITE_REQUESTED` before the
mock invocation and retain the exact frozen record hash.

The **5 exception scenarios** retain original Oracle success observations:
current contract drift (new user-approved binding difference), missing current
WRITE scope/unverified principal (R8-S03), and completed/pending REUSED reservations
(R8-S04). Native contract/principal/scope failures occur before policy access;
REUSED/PENDING do not store or invoke a new WRITE. Ordinary no-CHECK behavior is
preserved, not silently converted to CHECK failure. Fine-grained unit cases also
exercise every declaration field, semantic drift and 0/1/2 matching declarations.

## Corpus inventory and reproducibility

`tests/fixtures/write-gate-manifest.json` binds:

* the unchanged Oracle commit and exact `python-oracle-lock.json` SHA-256;
* all **19 prior R8 observation files** (R8-S01 through R8-S04);
* four new gate artifacts: base bindings, canonical NSO, normal and exception TSVs;
* the existing R7 approval gate manifest, linking its proof/claim/retry evidence;
* four explicit approved difference groups: snapshot metadata admission,
  current principal/scope, reservation disposition and declared contract binding.

The inventory test checks all **24 file digests** and each TSV's pin/row count.
Two independent `git archive` exports of the frozen commit generated byte-for-byte
identical new artifacts and SHA-256 hashes. The manifest generator is deterministic.
JSON/TSV use LF and NSO stays binary to preserve digests on Windows/Linux.

Using Python 3.12 and an unmodified frozen archive:

```bash
.venv/bin/python -I -B rust/crates/nsl-integration/tests/fixtures/generate_write_gate.py "$oracle_dir" base
.venv/bin/python -I -B rust/crates/nsl-integration/tests/fixtures/generate_write_gate.py "$oracle_dir" skill
.venv/bin/python -I -B rust/crates/nsl-integration/tests/fixtures/generate_write_gate.py "$oracle_dir" normal
.venv/bin/python -I -B rust/crates/nsl-integration/tests/fixtures/generate_write_gate.py "$oracle_dir" exceptions
.venv/bin/python -I -B rust/crates/nsl-integration/tests/fixtures/generate_write_gate.py "$oracle_dir" manifest
```

The first four modes emit the selected artifact; the last hashes the existing
recorded repository fixtures. Keep original observations separate from native
expectations in the gate assertions.

## Rust traceability

| Requirement | R8-S05 status / verification |
| --- | --- |
| `NSL-RUST-WRITE-002/005/008` | IMPLEMENTED/VERIFIED at the Guard boundary for admitted Skill/intent/declaration/current contract binding and fail-closed current authority. `R8-S05-DECLARATION`, `R8-S05-ORACLE`, `R8-S05-RECOVERY`. |
| `NSL-RUST-WRITE-006/007` | VERIFIED precondition identity/currentness/atomic-enforcement admission; actual provider atomic behavior remains the host/R9 obligation. `R8-S05-ORACLE`. |
| `NSL-RUST-WRITE-009/010` | VERIFIED native request/evidence composition and exact record identity before the spy consumes the durable handoff. `R8-S05-ORACLE`. |
| `NSL-RUST-GATE-005`, `NSL-RUST-IDEM-005` | VERIFIED approval drift/new-approval recovery and idempotency-conflict rejection before mock invocation. `R8-S05-RECOVERY`, `R8-S05-ORACLE`. |
| `NSL-RUST-SEC-005/006/010` | VERIFIED current authority/tenant-bound evidence and preserved replay-denial axis in this component profile. Full production issuer/package/replay/provider certification remains later integration. |
| `NSL-RUST-DIFF-010` | VERIFIED 54 ordinary scenarios and five explicitly approved exception scenarios, with original bytes preserved. `R8-S05-ORACLE`, `R8-S05-EXCEPTIONS`. |
| `R8-EXIT` | PASS for the bounded pre-invocation component gate: each planned CHECK/approval/idempotency/policy/contract/precondition failure yields zero mock invocation, with positive controls proving the route is connected. |
| `NSL-RUST-WRITE-011`, `NSL-RUST-COMMIT-*`, live provider E2E | No new completion claim; actual provider execution, business commit classification and post-WRITE verification remain R9 and later integration. |

No frozen Python requirement status/fingerprint is changed. This Rust component
ledger records the approved change and the evidence's practical scope.


| New artifact | SHA-256 |
| --- | --- |
| `write-gate-base.json` | `7a7d8fbfba5f6ab071929dadd25fde56d813e4aa05f3d7233fdb7b1dd9aaef8a` |
| `write-gate.nso` | `1fb3d2f97b8067b5e5d0abd92d6a413758b7fe8a472898b5cd4176ba98df3d83` |
| `write-gate-normal-oracle.tsv` | `302f19aac23cb9f1400a3203bb7e82d31a407c7945addbff7f3be9461b02789c` |
| `write-gate-exceptions-oracle.tsv` | `401b0f13371c78e9b1e3990071a303fcfdb91ced58654d4a7515a978d62a5249` |


Pinned **Rust 1.99.1** passes fmt, locked workspace check, architecture guard and
**8 guard tests**, all-target/all-feature Clippy with warnings denied, **277 Rust
tests** (272 baseline plus five R8 gate tests), and frozen workspace build. The
final inventory also passes after its deterministic manifest update. The mandatory
Python quality command ran **exactly once locally for R8-S05**: **2,391 passed**,
statement **99.28%**, branch **98.11%**, with traceability/readiness, architecture,
performance and certification checks. Existing Python v0.1 CONDITIONAL findings
are unchanged.

Pinned **nightly-2026-11-15** LLVM instrumentation retains the existing workspace
`crates/**/src/` executable-line aggregation for the Rust statement figure:
**99.15%** (14,395/14,519); branch **98.47%** (2,064/2,096). Guard admission/body
covers **212/212 executable lines and 80/80 instrumented branches**, including the
new declaration checks. No source/branch exclusions were added; test-only integration
code is outside the unchanged source aggregation and is not counted as production
coverage. Embedded source-unit-test aggregation is unchanged. Rust and Python
are independent measurements.
