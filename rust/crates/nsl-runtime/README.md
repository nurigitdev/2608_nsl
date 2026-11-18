# NSL Runtime models and execution context

## R6-S01 scope and ownership

This Slice provides `ExecutionIdentity`, `ExecutionPrincipal`, `DataHandlingPolicy`, `ExecutionSecurityContext` (also exported as `SecurityContext`), `ExecutionRequest`, `CheckResult`, `ValidationSummary`, `EmitRecord`, `ResourceUsage`, `RuntimeErrorInfo`, `ExecutionResult`, `ExecutionContext`, `RuntimeClock` and `ResourceGuard`. It also provides the typed optional result summaries and named approval/write/idempotency/checkpoint state containers required to represent the Oracle's existing context and result schemas. These are data models and validation/resource components; Tool Execution Port integration, expression evaluation, READ, CHECK, FOREACH/EMIT execution, approval/resume and WRITE execution remain the following slices.

`ExecutionRequest::new` owns its input and runtime-context maps and preserves original execution/business-operation identity strings. It screens credential fields and text using the existing Core detector without expanding Decimal/Money tokens. Principal/security metadata is separate from those maps. Like the Oracle, constructing a request or security-context model does not itself validate or elevate principal authority. `validate_for_execution(require_verified)` explicitly invokes principal validation. Principal verification state is host-supplied metadata; a current authorization decision and WRITE authority are not created by a model constructor.

`ExecutionContext::new(Arc<SkillObject>, request)` uses the system monotonic clock. `with_clock` accepts a trusted host clock port for deterministic observation. Contexts share immutable typed IR and own independent mutable namespaces, paired value/CHECK frames, checks, outputs, completeness and resource state. Approval, write-journal, idempotency and checkpoint members are isolated per-context containers with no I/O or service objects. The resource meter has one owner inside the guard; `usage()` returns a snapshot, avoiding a separately mutable counter copy. Runtime depends on Core/IR and cannot reach Compiler/Source.

### Bindings and completeness

`bind(BindingNamespace::{Input, Context, Variable, Check}, symbol, envelope)` rejects any symbol already active in any namespace. Failure retains the prior value and returns `BindingError::Immutable`, with detail code `IMMUTABLE_BINDING_ERROR`. `resolve` follows Oracle order: value frames from inner to outer, CHECK frames from inner to outer, CONTEXT, then INPUT. `push_frame`/`pop_frame` keep the two frame families paired and reject popping the root. Names removed by a completed frame can be reused in a sibling frame.

Observed completeness is monotone: UNKNOWN remains UNKNOWN; PARTIAL replaces COMPLETE; COMPLETE never upgrades an observed partial/unknown result. Completed executions report observed completeness. Other statuses report PARTIAL when a check or output exists, otherwise UNKNOWN. Actual CHECK evaluation and FOREACH's depth enforcement are later work; the existing `MAX_FOREACH_NESTING_DEPTH = 16` baseline is exported for that integration.

### Resource semantics

`ResourceGuard` uses an injected `RuntimeClock::monotonic_ns()` and exact integer arithmetic. The system implementation uses `std::time::Instant`, without wall-clock or request timestamps. All resource limits, counters and nanosecond calculations preserve arbitrary precision through the existing pinned `num-bigint`; no machine-integer wrap or floating-point duration conversion is introduced.

The deadline is exclusive: remaining nanoseconds `<= 0` reject with `LimitExceeded(Duration)`. A positive remaining timeout is rounded up to milliseconds. Tool/loop consumption checks the deadline first and rejects at `current >= limit`, preserving counters on failure. `before_emit` checks without reserving/incrementing a row; the caller pairs it with `record_emit` only after an emission succeeds. `record_emit` itself is the Oracle's unconditional accounting operation. `check_collection` permits equality without updating the peak. `observe_collection` records the maximum before checking deadline/size, so an over-limit observation remains visible after rejection. These low-level call-order rules are deliberately preserved for later Runtime statement integration.

Clocks are trusted host ports governed by a monotonic contract. Request data cannot select or adjust them. Signed fake-clock values support frozen component observations; the guard does not add a new clock-rollback policy absent from the Oracle. `with_usage` models existing/restored resource state; checkpoint admission and restore validation remain later slices.

### Approved raw identity / validated identity boundary

Result and state models preserve raw identity strings as approved for this Slice. `WriteSummary::new` applies the Oracle's nonblank and credential checks and preserves spelling, including legacy examples such as `idem-short` and `not-a-payload-hash`. `IdempotencyState` and checkpoint containers retain raw stored strings. Model/snapshot representation does not authorize use of those strings for a WRITE.

`WriteSummary::validate_for_write` and `IdempotencyState::validate_for_write(node)` return `ValidatedWriteIdentities` containing the existing Core `IdempotencyKey` and `PayloadHash`. Conversion rejects malformed or missing values without rewriting the raw model. Core rules remain unchanged: idempotency prefix plus 64-character suffix; payload hash prefix plus exactly 64 lowercase hexadecimal digits. Future execution/WRITE integration must use these validated types at its admission boundary before side effects; no provider execution is introduced by R6-S01.

### Result representation

`ExecutionResult::to_data(max_decimal_bytes)` / `to_json(max_decimal_bytes)` match frozen result schemas 1.0 and 1.1. A nonempty optional extension enables schema 1.1 and all six extension keys, including nulls. `semantic_view` omits execution identity and CHECK provenance and projects output values as in the Oracle. `EmitRecord` preserves field order in the wire field array and owns immutable envelopes; its value/classification projections are snapshots. Constructor checks preserve empty-versus-blank distinctions. Closed Rust enums and typed collections replace Python wrong-type states.

Decimal expansion retains the Core caller-supplied token budget and returns typed `ResultEncodingError`; JSON integer encoding retains the 4,300-digit conversion bound. Model fields and result views retain the Oracle's supplied semantic data. RuntimeEngine input/type validation, exception projection, protected debug handling and final execution-result security enforcement are later Runtime integration work, not claims made by these data-only model constructors.

## Verification and traceability

The frozen reference is `28a949ec96581cbdc52c8133527b23bb5b72ee0a`. `generate_runtime.py` invokes its public model/context/guard code without implementation patches and uses fixed clocks. The **515 differential cases pass**:

| Verification ID | Cases | Evidence |
| --- | ---: | --- |
| `R6-S01-MODELS-ORACLE` | 436 | [Model tests](tests/model_oracle.rs): identities/requests, explicit principal validation and priority, policy bounds, all CHECK combinations through length four, summary consistency/ordering/overlap, EmitRecord, raw summary identities, optional result models, every execution status/completeness, all 64 extension combinations, exact result JSON and semantic view. |
| `R6-S01-GUARD-ORACLE` | 48 | [Guard tests](tests/guard_oracle.rs): -1/0/1/MAX and over-limit counters/sizes, exact nanosecond deadline, ceiling timeout, deadline priority, failed-observation accounting, split EMIT accounting and 100-digit integers. |
| `R6-S01-CONTEXT-ORACLE` | 31 | [Context tests](tests/context_oracle.rs): every namespace collision pair, lookup, root-pop rejection, scope escape/sibling reuse, monotone completeness and all execution-result status combinations. |

[Boundary tests](tests/boundaries.rs), verification ID `R6-S01-BOUNDARIES`, additionally verify explicit Core conversion, no mutation after failed conversion/binding, missing state, per-context state/security isolation, system-clock monotonicity and request-wall-clock independence, 0/1/255/256/257 frame depths, 1,000/5,000-digit resource counts, credential screening without million-digit decimal expansion, safe error chains, duplicate/blank CHECK IDs, result encoding budgets, invalid Optional encoding and the 4,301-digit JSON boundary. Credential rejections compare the rejection category; Core's safe error intentionally omits source field paths. Other constructor errors compare Oracle messages exactly. This suite exercises model components and does not claim to execute providers or governed WRITE.

| Rust SRS | R6-S01 status and evidence |
| --- | --- |
| `NSL-RUST-CTX-001/002` | Implemented at the model/context boundary. Identity correlation and typed security/input separation, explicit principal validation, credential admission and isolated context state are covered by model and boundary tests. Authorization and current-authority integration remain later work. |
| `NSL-RUST-CTX-003` | Implemented. All active namespace duplicates are rejected, prior values survive, and paired frames permit sibling reuse. |
| `NSL-RUST-CTX-004/005/006` | Implemented in the guard/context components. Usage snapshots, exact monotonic elapsed time, exclusive deadlines and request-clock independence are tested. Runtime operation/timeout integration remains R6-S02 onward. |
| `NSL-RUST-CTX-007` | Completeness propagation component implemented. Context and result models match Oracle; CHECK evaluation remains R6-S05. |
| `NSL-RUST-CTX-008` | Isolated mutable scope: request/IR ownership, paired frames, guard-owned meter and independent state containers. |
| `NSL-RUST-RUN-002/015` | Architecture evidence: Core/IR-only Runtime dependencies and data-only context state. No enterprise transaction service or state owner is added. |
| `NSL-RUST-RUN-005/011` | Binding/resource components implemented and differentially verified. Actual statement and provider execution remain later slices. |
| `NSL-RUST-RUN-016/017` | Model-level result JSON/semantic compatibility and deterministic fixed-clock observations verified; complete Runtime execution remains R6-S07. |
| `NSL-RUST-RUN-018`, `NSL-RUST-SEC-004` | Identity/request credential screening and typed validation-boundary evidence. Full execution/Trace/Replay output enforcement remains later work. |

Regenerate with Python 3.12 from a fresh archive of the frozen revision:

```bash
oracle_dir=$(mktemp -d)
git archive 28a949ec96581cbdc52c8133527b23bb5b72ee0a nsl | tar -x -C "$oracle_dir"
for mode in models guards contexts; do
  .venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_runtime.py "$oracle_dir" "$mode" > "$oracle_dir/$mode-oracle.tsv"
  cmp "$oracle_dir/$mode-oracle.tsv" "rust/crates/nsl-runtime/tests/fixtures/$mode-oracle.tsv"
done
```

Two generations matched each other and the committed files byte-for-byte; explicit LF attributes preserve the fixtures across operating systems.

| Fixture | SHA-256 |
| --- | --- |
| `models-oracle.tsv` | `7ffda205e6e63719dd9adf16a3e4280a66c108fa7bdc727ceb24ce6b5c010398` |
| `guards-oracle.tsv` | `4b57587c7cdb576bc2b2c14ee1084d61b410784e5555358374470597b81c105d` |
| `contexts-oracle.tsv` | `b3a6cd648eaa9a41e2d2db5f59378302712fac82ce709a473d8efabd81ed1d4c` |

Stable **Rust 1.99.1** passes fmt, locked workspace check, all-target/all-feature Clippy with warnings denied, the Architecture Dependency Guard and its **7 tests**, **136 Rust tests**, and a frozen workspace build. The mandatory Python gate ran exactly once across R6-S01 and its identity-model decision checkpoint: **2,333 passed**, statement **99.29%**, branch **98.14%**. Python production sources are unchanged.

Pinned `nightly-2026-11-15` LLVM instrumentation reports workspace executable-line coverage (the statement figure) **99.19%** (7,728/7,791) and branch coverage **98.55%** (1,090/1,106). Context, resource guard, structured model validation and request-value screening modules each reach **100.00%/100.00%**. Defensive result-object shape branches remain in coverage totals. Physical test code and generated Unicode constant data are excluded using normalized paths, consistently with preceding slices.

Runtime adds only Core, IR and the already reviewed `num-bigint = 0.4.8` dependency edge. All **15 external package identities and checksums are unchanged**. Updated `Cargo.lock` SHA-256: `2690dd1904248e45e9f6754d68e9fae34dacab2d4f4d39963f86249eb771acd2`.
