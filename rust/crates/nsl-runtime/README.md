# NSL Runtime models, context and tool execution

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

## R6-S02 Tool Execution Port

This Slice implements `ToolExecutionPort`, `ToolCallRequest`, `ToolResultEnvelope`, `ToolContractValidator`, `MockToolExecutor`, result SHA-256/provenance, measurements and the timeout boundary. The port is object-safe and explicitly `Send + Sync`; its boxed future is `Send`. An owned request snapshot carries execution/invocation/node/tool/tenant correlation and authorization-decision reference. These are raw Oracle model strings; constructing a tool request does not authorize execution or confer WRITE authority. Actual RuntimeEngine READ integration belongs to R6-S04, and expressions belong to R6-S03.

The validator preserves the Oracle's ordering: contract hash, argument-name set, then argument envelopes in **contract declaration order**. Direct request validation does not additionally compare tool identity/version or authorize the principal; catalog resolution and the execution security boundary own those checks. Result structure validates the invocation/tool/version before snapshot metadata; `None` represents an unstructured adapter response. Closed Core enums and typed envelopes exclude Python wrong-type metadata states. Empty snapshot text is rejected, but whitespace text is accepted. Result metadata/value and result hash are separate checks, matching the Oracle's public validator stages.

Tool value conformance uses Core rules except that internal `Optional` is rejected whenever reached, including a null value; an empty `List<Optional<T>>` passes vacuously. Core's more permissive internal Optional model is unchanged. Result hashes use `sha256:` plus the digest of Core canonical JSON of the encoded value. Callers supply the existing Decimal token byte budget; integer encoding retains the 4,300-digit limit. Original result metadata is preserved, and `to_value` attaches exactly one supplied provenance reference.

The deterministic mock owns its catalog and handler map. It performs exact catalog resolution and validates the request before invoking a fixture, counts the attempt **before** invoking that fixture, validates the returned value, and constructs the contract-derived envelope/hash. Missing contracts, incompatible versions, bad requests and missing fixtures leave the count unchanged. Handler/output/encoding failures retain the increment. Default fixture completeness is COMPLETE; PARTIAL and UNKNOWN are preserved. Empty lists have EMPTY presence; an empty record has PRESENT presence, as in the Oracle. The mock is synchronous once first polled and has no cancellation suspension point, matching the Oracle mock's async body without awaits. It performs no filesystem, SQL or network access.

### Cooperative cancellation and cleanup

`CancellationSignal` is sticky, cloneable, idempotent and supports multiple waiting providers. It hides its Tokio implementation. `execute_with_timeout(port, request, timeout_ms, signal)` requires a host Tokio time driver. A nonpositive timeout rejects without starting the provider. A positive timeout polls the operation and timer; expiry **requests cancellation and continues awaiting the same future**. It neither drops/aborts the provider nor starts a replacement call.

| Provider's final result after cancellation | Timeout helper result | Measurement outcome |
| --- | --- | --- |
| Propagates cancellation after cleanup | `TOOL_TIMEOUT` | `CANCELLED` |
| Suppresses cancellation and returns a value | That value | `RETURNED` |
| Cleanup raises an ordinary provider error | That error, with its protected detail preserved | `ERROR` |
| Cleanup raises provider `TimeoutError` | `TOOL_TIMEOUT`, matching Oracle's outer exception translation | `ERROR` |

Cleanup may exceed the original deadline, and no second deadline is imposed. If a provider never completes cleanup, the caller keeps waiting. Hosts must request explicit cancellation and await the returned future; dropping a future, aborting a task, panics and process termination are not cooperative cleanup. Direct port cancellation returns the provider's final result without manufacturing a timeout. The port and timeout helper contain no commit outcome, business retry, or WRITE-state transition; a timeout is never evidence of either commit or non-commit.

`MeasuringToolExecutor` records once after delegate completion, including all cleanup time. Outcome comes from the final result rather than whether cancellation was requested. Nanoseconds are arbitrary-precision and clamped at zero on a backwards clock observation; milliseconds use **floor division**, unlike ResourceGuard's ceiling timeout calculation. Measurement identifiers and nonnegative duration are explicitly validated. As in Oracle's `finally`, a failing measurement sink overrides a returned value or previous provider/cancellation error. Unexpected provider/sink details remain in typed errors for protected host handling; `Display` and the error source chain do not disclose them. Runtime diagnostic/result projection remains R6-S07.

### R6-S02 verification and traceability

The unmodified reference remains **`28a949ec96581cbdc52c8133527b23bb5b72ee0a`**. [The generator](tests/fixtures/generate_tools.py) produces **657 differential observations** using synthetic values, event-controlled cleanup and fixed measurement clocks:

| Verification ID | Cases | Evidence |
| --- | ---: | --- |
| `R6-S02-TOOLS-ORACLE` | 642 | [Tool differential test](tests/tool_oracle.rs): 513 type/value pairs, 27 result hashes, 11 request checks, 9 structure checks, 18 result/value/hash checks, provenance, 12 mock scenarios, 30 identifier validations and 21 duration/outcome boundaries. |
| `R6-S02-TIMEOUT-ORACLE` | 15 | [Timeout differential test](tests/timeout_oracle.rs): cancellation propagation/suppression/cleanup errors/provider timeout/sink failure; immediate completion/error/cancellation/backwards clock; -1/0/1/MAX timeout bounds. The outer future stays pending and no measurement is recorded until cleanup is released, including after 60 virtual seconds beyond expiry. |
| `R6-S02-BOUNDARIES` | 6 tests | [Boundary suite](tests/tool_boundaries.rs): sticky multi-waiter cancellation, direct host cancellation, completion before expiry, exact correlation, hash budgets, 4,299/4,300/4,301 digits, million-digit Decimal budget rejection, failed-encoding call counts, Optional boundaries, safe error chains, measurement validation and sink-error precedence. |

| Rust SRS | R6-S02 status and evidence |
| --- | --- |
| `NSL-RUST-TOOL-004` | Exact catalog failures verified through the mock; no provider attempt on missing/incompatible contract. `R6-S02-TOOLS-ORACLE`. |
| `NSL-RUST-TOOL-005/006` | Port/mock component implemented. Trait injection and provider-isolated business access; architecture guard plus source inspection. RuntimeEngine integration remains R6-S04. |
| `NSL-RUST-TOOL-007` | Request/result validation components and mock pre/post checks implemented and differentially verified. Engine admission of external provider envelopes remains R6-S04. |
| `NSL-RUST-TOOL-008` | Canonical result hash and one-reference provenance conversion implemented. Hash mismatch, metadata preservation and encoding limits verified. |
| `NSL-RUST-TOOL-009`, `NSL-RUST-ASYNC-003` | Timeout/cancel boundary implemented without commit inference or business retry. All three approved cleanup outcomes and late/error/sink variants verified. WRITE integration remains later slices. |
| `NSL-RUST-TOOL-010`, `NSL-RUST-ASYNC-001/002/005` | Reviewed exact Tokio 1.54.1 lock, minimal features, opaque cancellation/timers, no Tokio types in semantic models, explicit Send/Sync boundaries. Dependency review and compile/architecture tests. |
| `NSL-RUST-ASYNC-004` | Tool port component implemented. WRITE, verification and persistence ports are later slices. |

Regenerate from an archive of the pinned revision, using Python 3.12:

```bash
oracle_dir=$(mktemp -d)
git archive 28a949ec96581cbdc52c8133527b23bb5b72ee0a nsl | tar -x -C "$oracle_dir"
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_tools.py "$oracle_dir" tools > "$oracle_dir/tools-oracle.tsv"
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_tools.py "$oracle_dir" timeout > "$oracle_dir/timeouts-oracle.tsv"
cmp "$oracle_dir/tools-oracle.tsv" rust/crates/nsl-runtime/tests/fixtures/tools-oracle.tsv
cmp "$oracle_dir/timeouts-oracle.tsv" rust/crates/nsl-runtime/tests/fixtures/timeouts-oracle.tsv
```

Both files were generated twice and matched each other and the checked-in fixtures byte-for-byte. Explicit LF attributes preserve their bytes on Windows and Linux.

| Fixture | SHA-256 |
| --- | --- |
| `tools-oracle.tsv` | `b9c3045be8456440f594e89764c1d0224fefb59dfb4160a30f598cce90f601f5` |
| `timeouts-oracle.tsv` | `a040dcf3b4a4566b8af357595115e5030df6cd8e081382467174995aac694427` |

Stable **Rust 1.99.1** passes formatting, locked workspace check, all-target/all-feature Clippy with warnings denied, the Architecture Dependency Guard and its **7 tests**, **144 Rust tests**, and a frozen workspace build. The mandatory Python quality gate ran **exactly once across R6-S02 and its cancellation decision checkpoint**: **2,333 passed**, statement **99.29%**, branch **98.14%**; Python production sources are unchanged.

Pinned `nightly-2026-11-15` LLVM instrumentation reports workspace executable-line coverage (the Rust statement figure) **99.23%** (8,118/8,181) and branch coverage **98.52%** (1,135/1,152). All four new production modules reach **100.00% executable-line coverage**. The mock's defensive null-presence branch remains in the branch denominator even though contract conformance rejects null before it can reach presence construction. Physical test code and generated Unicode constant data are excluded consistently with prior slices. These LLVM and Python coverage figures use different instrumentation and are reported separately.

The [dependency review](../../DEPENDENCIES.md#r6-s02-asynchronous-execution-dependency-review) records two new external packages, unchanged prior checksums, minimal features, licenses, unsafe-code/build-script review and the immutable RustSec snapshot. `Cargo.lock` SHA-256 is **`628268a256b8144d2e6f26a9db9835b620896fed75eec7a3a92b05fae7dfe562`** before and after the frozen build.
