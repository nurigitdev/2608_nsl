# NSL Runtime models, context, tools, expressions and READ

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
| `NSL-RUST-CTX-007` | Completeness propagation component implemented. Context and result models match Oracle; CHECK propagation is implemented and verified in R6-S05 below. |
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

## R6-S03 expressions and immutable LET

`ExpressionEvaluator::evaluate(&mut context, &expression)` evaluates the pure Typed IR variants: literal, symbol reference, field, projection, closed built-in call and binary operation. `execute_let(context, target_symbol_id, expression)` evaluates first and then binds through `ExecutionContext`'s existing immutable Variable namespace. The evaluator borrows the IR and uses a work stack rather than recursive expression calls. READ/WRITE return a typed `Unsupported { node_id }` at this component boundary; provider/READ integration belongs to R6-S04 and governed WRITE to R7. Block statement bookkeeping, version/profile preflight, input/context admission, CHECK, FOREACH/EMIT and execution-result/audit projection remain their planned integration slices.

Evaluation order matches the frozen Oracle: check the deadline on entry, evaluate children left to right (including every call argument before resolving the built-in), calculate, check envelope type equality, observe completeness, check the deadline again, and check a list result's collection size. Pure expression checks do not increment tool/loop/emit counts or observe the maximum-collection meter. A completed child can affect context completeness even if a parent fails; a result observed before a post-evaluation deadline/size rejection also retains that observation. Source bindings and values are preserved.

Metadata follows the Oracle:

- Literals are PUBLIC and COMPLETE. Symbol references preserve the stored envelope and reject a differing declared envelope type with `EXPRESSION_TYPE_MISMATCH` before observing its completeness. Like the Oracle component, envelope consistency does not revalidate the actual value against its declared type; admission is a separate boundary.
- Field results are PRESENT even when the selected value is null or an empty list. Projection is EMPTY only for an empty resulting list. Both preserve the source completeness, classification and ordered provenance, including duplicates.
- Calls are PRESENT and preserve the single argument's metadata, including for an empty typed sum. Binary results combine UNKNOWN before PARTIAL before COMPLETE, select the highest classification and deduplicate provenance in stable left-then-right order.
- LET preserves the result envelope. A failed evaluation leaves the target unbound; a duplicate in any active namespace preserves the existing binding. Completed sibling frames can reuse a name. Statement/current-node changes belong to the block driver, not `execute_let`.

### Arithmetic, comparisons and built-ins

The closed call set is `count`, `min`, `max`, `sum`. Signature validation preserves Oracle names, arity, List requirements, sum item-type restrictions and result-type checks. Empty Int/Decimal sums use typed zero; Money sums call Core `sum_money` and preserve the declared currency. Empty min/max fail. A singleton record min/max returns that record; comparing multiple records fails. Bool, string/domain/enum, Date, fixed-offset DateTime, and lexicographic list observations match the frozen Python operations. Different naive/aware DateTime values compare unequal and cannot be ordered. DateTime business-value comparisons interpret Python ISO text to microsecond instants and retain the naive/aware distinction; this is not the security/evidence timestamp admission boundary.

Integer arithmetic is arbitrary precision. Decimal/Money operations use the existing fixed 28-significant-digit `ROUND_HALF_EVEN` Core implementation, including ordered accumulation and signed-zero/scale behavior. Runtime Money ordering preserves the Oracle's reflected `>`/`>=` operand order, including currency error messages. Structural Money equality includes amount and currency, as in the Oracle dataclass; Core's checked comparison API is unchanged. No Float representation or numeric dependency is added.

Ordinary arithmetic/field/projection/conversion failures return `ExpressionEvaluationError` with the failing expression's node ID and `EXPRESSION_EVALUATION_ERROR`. Child errors keep their original identity. Built-in contract errors and controlled Money errors remain distinct. Missing symbols and malformed Money operations that the Oracle propagates as unexpected errors retain their class/detail in `ExpressionRuntimeError::Unexpected`; its `Display` is always `An unexpected runtime error occurred.` Operand values, internal causes and backtraces are not exposed through the public error source chain. Complete RuntimeEngine diagnostic/audit projection remains R6-S07.

### Approved Oracle difference: Int division

The frozen Oracle accepts an external Binary IR with Int operands and a falsely declared non-Decimal result. For `1 / 2` declared as Int it returns Python float `0.5`; its NSO schema accepts that declaration too. Rust intentionally rejects it under the user's R6-S03 decision, keeping the NSL numeric model free of floating point:

```text
ExpressionEvaluationError
node_id: the DIV expression's node_id
detail_code: EXPRESSION_TYPE_MISMATCH
message: expression type mismatch at <node_id>
```

The check runs after child evaluation and before division. Int operand declarations must have a Decimal result; an actual Int/Int pair also cannot produce a non-Decimal quotient. This rejection takes priority over division-by-zero once both children have evaluated. Valid Int division returns Core Decimal and remains Oracle-compatible. The Oracle revision and its implementation are unchanged.

[The exception fixture](tests/fixtures/expression-division-exceptions.tsv) records **24 actual Oracle observations**, not patched expectations: six non-Decimal result declarations crossed with divisors -1/0/1/2. Eighteen observations return float and six fail ordinary division-by-zero evaluation; Rust consistently rejects all 24 with the approved mismatch/node identity. [The boundary test](tests/expression_boundaries.rs) also shows that NSO 1.0 and 1.1 can encode/decode this IR with correct integrity hashes before the Runtime rejects it, and verifies that failed LET does not publish a value.

### R6-S03 verification and traceability

The reference remains **`28a949ec96581cbdc52c8133527b23bb5b72ee0a`**. [The generator](tests/fixtures/generate_expressions.py) imports its unmodified Runtime/Core/built-in code and uses synthetic values and exact scripted clocks. Its small postfix notation is only a test-fixture representation used to construct the same owned IR on both sides.

| Verification ID | Evidence |
| --- | --- |
| `R6-S03-EXPR-ORACLE` | **719 matching observations** in [the differential test](tests/expression_oracle.rs): all ten binary operators; arbitrary integers; Decimal rounding/overflow; Money arithmetic/currency/reflected errors; all completeness/classification pairs; provenance order; field/projection/null/empty behavior; closed built-ins and malformed values; fixed-offset/naive DateTime equality; error priority and node/detail identity; immutable LET; exact deadline-clock calls, context observations and collection limits. |
| `R6-S03-DIVISION-EXCEPTION` | **24 approved compatibility exceptions**, separately stored and asserted in [the differential test](tests/expression_oracle.rs). Ordinary Decimal-result Int division remains in the matching corpus. |
| `R6-S03-BOUNDARIES` | **8 tests** in [the boundary suite](tests/expression_boundaries.rs): actual NSO 1.0/1.1 admission, 0/1/31/32/255/256/1,024/4,096 expression depth on a 128 KiB evaluator thread stack, atomic failed LET and sibling reuse, unsupported provider nodes, safe unexpected errors, malformed/oversized ISO components and leap-day rules, 5,001-digit integer division without float conversion, collection limit 0/1/MAX-1/MAX/MAX+1 observations and concatenation output limits, Core Money ordered failures/typed zero/overflow, and mismatched host values under an Int declaration. NSO/Context preparation, assertions and teardown run on the normal test stack; only evaluation runs on the restricted stack. Deep-tree teardown is iterative and the evaluator borrows caller-admitted IR. |

| Rust SRS | R6-S03 status and evidence |
| --- | --- |
| `NSL-RUST-RUN-001/002` | Pure Typed-IR expression component implemented with Core/IR dependencies and no compiler/AST, LLM or business-system access. Architecture guard and inspection. |
| `NSL-RUST-RUN-005` | LET component implemented: evaluation before immutable binding, preserved envelopes and failures, namespace collisions and sibling frames verified. Block-driver integration remains later slices. |
| `NSL-RUST-RUN-011`, `NSL-RUST-CTX-005/006` | Expression deadline/collection checks implemented, with exact call order and failure-side observations verified. Complete runtime operation accounting remains R6-S04 onward. |
| `NSL-RUST-RUN-012/013/014` | Pure-component evidence: iterative expression traversal, typed unsupported/malformed failures, generic unexpected-error display and protected detail retention. Complete RuntimeEngine admission, result projection and debug-sink enforcement remain R6-S07. |
| `NSL-RUST-RUN-017` | Deterministic pure evaluation, scripted clocks, immutable sources and repeated fixture generation verified. Whole-program differential gate remains R6-S07. |
| `NSL-RUST-CORE-009`, `NSL-RUST-CTX-007`, `NSL-RUST-SEC-007` | Expression metadata/classification/completeness propagation implemented and differentially verified. CHECK/result/audit propagation remains later integration. |
| `NSL-RUST-MONEY-001/002` | No floating-point semantic path. Approved Int-division rejection is explicitly tested and documented separately from matching Oracle cases. |
| `NSL-RUST-MONEY-003/004/005/006`, `NSL-RUST-CORE-013` | Runtime reuses exact Core arithmetic and the new pure `sum_money`; ordered invalid-value/currency/overflow behavior and typed zero verified. |

Regenerate from the repository root with Python 3.12 and a fresh archive:

```bash
oracle_dir=$(mktemp -d)
git archive 28a949ec96581cbdc52c8133527b23bb5b72ee0a nsl | tar -x -C "$oracle_dir"
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_expressions.py "$oracle_dir" expressions > "$oracle_dir/expressions-oracle.tsv"
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_expressions.py "$oracle_dir" division > "$oracle_dir/expression-division-exceptions.tsv"
cmp "$oracle_dir/expressions-oracle.tsv" rust/crates/nsl-runtime/tests/fixtures/expressions-oracle.tsv
cmp "$oracle_dir/expression-division-exceptions.tsv" rust/crates/nsl-runtime/tests/fixtures/expression-division-exceptions.tsv
```

Both files were generated twice; each pair and the checked-in file matched byte-for-byte and by SHA-256. Explicit LF attributes preserve the fixture bytes across operating systems.

| Fixture | SHA-256 |
| --- | --- |
| `expressions-oracle.tsv` | `097413639c33f60a832d7d1a662633768f5e6304695e406ce92b66b136e529f0` |
| `expression-division-exceptions.tsv` | `65ab95305268cd9b138eb6be72e2dec436bd03e318a0429d6cd94dc5d210b914` |

Local **Rust 1.99.1** passes fmt, locked workspace check, all-target/all-feature Clippy with warnings denied, the Architecture Dependency Guard and its **7 tests**, **154 Rust tests**, and a frozen workspace build. The mandatory Python gate ran **exactly once for R6-S03**: **2,333 passed**, statement **99.29%**, branch **98.14%**. Python production sources are unchanged.

The [first Windows CI run](https://github.com/nurigitdev/2608_nsl/actions/runs/44277299750) failed with `0xc00000fd / STATUS_STACK_OVERFLOW` in the original small-stack test. That test included NSO admission and Context construction inside the 128 KiB thread, so the failure did not isolate the expression evaluator. The continuation moves preparation and teardown outside that thread, retains every depth and the same stack budget, and names each evaluator thread with its depth. The correction changes only test isolation and this evidence record; production evaluator semantics, Oracle fixtures and dependencies are unchanged. The existing Python gate is retained across this R6-S03 continuation without a second pytest invocation.

Pinned `nightly-2026-11-15` LLVM instrumentation reports workspace executable-line coverage (the Rust statement figure) **99.19%** (8,711/8,782), branch **98.38%** (1,277/1,298). Core Money reaches **100.00%/100.00%**. Defensive work-stack/empty-collection/comparison guards remain in the totals; physical test code and generated Unicode constant data are excluded consistently with previous slices. Rust and Python use different instrumentation and are reported separately.

No package, dependency edge, version, feature or checksum changes in this Slice. The 17-package external inventory is unchanged. `Cargo.lock` SHA-256 remains **`628268a256b8144d2e6f26a9db9835b620896fed75eec7a3a92b05fae7dfe562`** before and after the frozen build.

## R6-S04 canonical READ execution

`ReadEvaluator` adds asynchronous READ and READ-containing LET to the existing Typed-IR expression component. It shares `ExpressionMachine` with the pure `ExpressionEvaluator`; the machine pauses before a READ's arguments and resumes after provider validation. The pure facade still returns its existing typed unsupported-service error for READ/WRITE. WRITE expressions remain unsupported by the READ driver. The before/after refactor regression suites both passed all 154 existing Rust tests, including the 719-expression corpus and the same 128 KiB pure-evaluator stack boundary.

The caller injects an immutable `ToolContractCatalog`, `ToolExecutionPort`, `ReadAuthorizationPort` and an explicit Decimal encoding byte budget. Business-system I/O is confined to the injected Tool Port. The component assumes caller-admitted bounded IR and context. It does not replace production principal verification, skill/profile preflight or artifact trust admission; the host must explicitly validate those boundaries. `StaticReadAuthorizer` mirrors the Oracle component's principal validation, exact scope membership and deterministic default-deny decision reference. It returns the reference used in `ToolCallRequest`, without adding an authorization timestamp or wire schema. A reference by itself is not authority. A host can inject current authorization policy; denial stops before evaluating any arguments or nested READs.

### Admission and execution order

1. Check the expression deadline, find its required tool and resolve the exact catalog version.
2. Enforce the approved security rule: **both declared and resolved capability must be READ, and declared scope must exactly equal the resolved contract scope**. Reject with `TOOL_CONTRACT_MISMATCH` before authorization, child evaluation, resource/invocation accounting or provider dispatch. Do not normalize scope text or treat an unregistered capability as READ.
3. Validate the argument-name set and reject duplicates, including a duplicate alongside every otherwise required argument. Authorize using the **actual contract scope** and then evaluate arguments in IR order. Each nested READ follows the same admission procedure.
4. Consume one tool-call allowance and increment the arbitrary-precision invocation counter. Format its ID as the Oracle's minimum-width-four integer (`inv0001`, `inv10000`, etc.). Compute canonical argument SHA-256 even without an audit sink, retaining the Oracle's encoding-failure/accounting order. Construct the request with original execution/node/tool/version, principal, tenant and authorization correlation.
5. Validate the request hash/types, then invoke once with the smaller of contract timeout and the ceiling remaining duration. Each invocation owns a fresh cooperative cancellation signal. R6-S02 cancellation/cleanup semantics are preserved; cancellation suppression can return a value, cleanup errors retain their type, and cleanup is awaited without a second deadline. One timed-out invocation cannot cancel a later sibling. `TOOL_TIMEOUT` becomes a duration `LimitExceeded` only when the remaining execution duration selected that timeout. No timeout, transport or cancellation result infers a business commit outcome or retries WRITE.
6. Validate structured identity/snapshot metadata, contract output type/classification/value, and canonical result hash, in that order. Apply the required/empty policy, then check the deadline. Closed Rust metadata types exclude Python wrong-type envelope fields; an adapter must return a typed malformed-result error for an unstructured response.
7. Observe list size, attach `prov:<invocation_id>:<first 12 hash hex digits>`, and finish the outer expression's envelope-type/completeness/deadline/list checks. A collection overflow records its maximum size before failing and does not publish a LET value. Read-specific collection failure precedes observation of that result's completeness, matching the Oracle. PARTIAL and UNKNOWN are preserved even when `accept_partial` is false; R6-S05 owns strict CHECK interpretation.

`ReadExecutionError` retains typed expression, Tool, authorization, catalog and encoding failures. Its public source chain does not disclose provider internals. Tool codes/messages and expression node/detail identities are differentially checked. Catalog comparison uses kind/tool ID/version; the fixture normalizer renders the Oracle's KeyError text, while the existing Rust catalog `Display` stays generic. Existing explicit Decimal budgets and the 4,300-digit canonical integer bound return typed encoding errors. Complete execution-result/debug projection remains R6-S07. Audit recording and snapshot persistence remain their planned later slices; no new audit or snapshot wire contract is introduced here.

### Approved security exception and current Python issue

Frozen Oracle `28a949e` and the current Python `nsl/runtime.py`, `nsl/tools.py`, and `nsl/security.py` had identical relevant source at review. Their READ path authorizes `required.required_scope` and does not compare the required capability/scope to the resolved contract's capability/scope. A correct hash of an actual WRITE contract can therefore be paired with forged READ/weaker-scope metadata. Hash and NSO integrity checks alone do not detect that disagreement.

[The reproducible probe](tests/fixtures/probe_python_read_metadata.py) passes complete NSO encoding/decoding and the ordinary Python `RuntimeEngine.execute` path in IR 1.0 and 1.1. The principal has `nsl:skill:execute` and `test:read`, lacks `test:write`, and there are no approvals. The actual contract is WRITE/CREATE requiring `test:write`, while its IR declaration advertises READ/`test:read`. Both Python executions return COMPLETED and invoke the synthetic mock handler once. The probe constructs no external provider and makes no business mutation.

This is a runtime authorization/contract-binding vulnerability requiring a Python security fix. The probe establishes the missing core check, not exposure or compromise of a deployed service; trusted-artifact restrictions, catalog contents and independent provider authorization can constrain practical exploitation. Those layers do not remove the need to enforce the Runtime boundary.

Under the user's explicit R6-S04 decision, Rust rejects this behavior instead of reproducing it. The 22 frozen security observations are kept separate from matching Oracle cases; tests assert `TOOL_CONTRACT_MISMATCH`, zero authorizer/provider calls, no argument evaluation, zero charged tool calls/invocation increments and no LET publication. Actual NSO 1.0/1.1 round-trip tests include a nested READ argument and verify that the outer invalid metadata is rejected before the nested provider can run.

**The subsequent, separately committed [Python security patch](../../../docs/security/read_contract_metadata_patch.md) closes this issue in the current Python tree.** It compares resolved capability/scope in preflight and both request validation APIs, enforces READ-only capability again in `_execute_read` (including a READ inside the controlled pipeline), and authorizes the actual contract scope. The probe below still reproduces the original one-call behavior against frozen Oracle `28a949e`; current Python now rejects it with zero calls. The Oracle, wheel lock and frozen security-exception fixtures remain unchanged. This remediation is separate from R6-S04; no next Rust Slice is started here.

To reproduce against a fresh frozen archive or the current tree:

```bash
oracle_dir=$(mktemp -d)
git archive 28a949ec96581cbdc52c8133527b23bb5b72ee0a nsl | tar -x -C "$oracle_dir"
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/probe_python_read_metadata.py "$oracle_dir"
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/probe_python_read_metadata.py .
```

### R6-S04 verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R6-S04-READ-ORACLE` | **273 matching READ observations** in [the differential suite](tests/read_oracle.rs): IR 1.0/1.1; all required/accept-partial/empty policies with COMPLETE/PARTIAL/UNKNOWN and scalar/list/empty-record results; identity/type/class/value/hash/snapshot failures; missing/duplicate argument priority; missing/version/changed contracts; exact request envelopes, authorization correlation and tenant/subject/scopes; -2/-1/0/998/999/9,998/9,999/100-digit invocation seeds; tool-call and collection boundaries; scripted deadline checks; nested/sibling READ and aggregation; error-side resources and completeness. |
| `R6-S04-AUTH-ORACLE` | **10 matching authorization observations** in [the boundary suite](tests/read_boundaries.rs): deterministic allow/deny references, missing/Unicode scopes, blank action/scopes, malformed principal and credential admission. |
| `R6-S04-SECURITY-EXCEPTION` | **22 approved differences** in [the differential suite](tests/read_oracle.rs): actual WRITE/TRANSACTION/unregistered capability, weaker declared scope, combined WRITE/scope substitution, and non-READ declaration; safe Rust rejection before all child/provider effects. These are unmodified Oracle observations, not patched expectations. |
| `R6-S04-BOUNDARIES` | **8 boundary tests**: NSO 1.0/1.1 forged-metadata admission and nested-call isolation, injected current denial, full-name-set duplicate rejection, explicit input/result encoding budgets and charged attempts, safe error displays/chains, WRITE-expression rejection, MockToolExecutor/immutable LET and independent contexts, timeout cancellation/suppression/cleanup-error measurement, late values after execution expiry, and fresh cancellation for sibling calls. |

| Rust SRS | R6-S04 status and evidence |
| --- | --- |
| `NSL-RUST-RUN-004`, `NSL-RUST-TOOL-005/006/007/008` | Canonical READ expression/LET component implemented through injected ports, request/result validation, exact hashes and provenance. CHECK is added by R6-S05 and FOREACH/EMIT/block dispatch by R6-S06 below; whole-engine admission/result integration remains R6-S07. |
| `NSL-RUST-TOOL-004` | Exact missing/incompatible catalog failures and changed-contract rejection verified; provider is not invoked. |
| `NSL-RUST-TOOL-009`, `NSL-RUST-ASYNC-003` | Per-call timeout/cancel/cleanup and duration mapping verified without commit inference or business retry. |
| `NSL-RUST-CTX-001/002` | Execution/principal/tenant/subject/authorization correlation preserved; injected current Tool authority enforced. Production verified-principal/skill admission remains the host/integration boundary. |
| `NSL-RUST-CTX-004/005/006/007`, `NSL-RUST-RUN-011` | Charged attempts, exact invocation counters, deadline/collection accounting and completeness/error-side observations verified. |
| `NSL-RUST-SEC-001/002/003/005` | No direct enterprise access or dynamic execution. READ cannot dispatch a resolved WRITE contract or substitute a weaker required scope; new Rust security guard closes the demonstrated path. Governed WRITE remains R7. |
| `NSL-RUST-SEC-006` | Request identity/correlation isolation evidence; provider business-data tenant filtering remains its trusted responsibility, not a claim made by this component. |
| `NSL-RUST-ASYNC-001/002/004/005`, `NSL-RUST-TOOL-010` | Existing pinned Tokio/time boundary reused, no new Tokio wire/model types, Tool and current-authorization ports explicitly Send+Sync. |
| `NSL-RUST-RUN-005/017` | Immutable READ-containing LET, pure-expression regression and deterministic corpus generation verified. Whole-program differential gate remains R6-S07. |

The fixed reference remains `28a949ec96581cbdc52c8133527b23bb5b72ee0a`. Generate fixtures from the repository root with Python 3.12:

```bash
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_reads.py "$oracle_dir" normal > "$oracle_dir/reads-oracle.tsv"
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_reads.py "$oracle_dir" auth > "$oracle_dir/read-authorization-oracle.tsv"
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_reads.py "$oracle_dir" security > "$oracle_dir/read-security-exceptions.tsv"
```

Each fixture was generated twice and matched the checked-in file and SHA-256. The existing 719-case expression fixture was regenerated byte-for-byte too. Explicit LF attributes preserve fixture bytes across operating systems.

| Fixture | SHA-256 |
| --- | --- |
| `reads-oracle.tsv` | `7885d6d3c515deb4e1d947e025e2de3063a5ee3a901564d3e0b73ffd9401d5fb` |
| `read-authorization-oracle.tsv` | `262c0f8b49d7ed2ad0b038324e3ac199fd886a0eb60b20225f7be71b4de398b9` |
| `read-security-exceptions.tsv` | `2ca3117be5b717cffa47936c977d15a6339d8f5ef5549e6ea95d37e22e5ea297` |

Local stable **Rust 1.99.1** passes fmt, locked workspace check, all-target/all-feature Clippy with warnings denied, the Architecture Dependency Guard and its **7 tests**, **164 Rust tests**, and frozen workspace build. The mandatory Python quality gate ran **exactly once across R6-S04 and its security-decision continuation**: **2,333 passed**, statement **99.29%**, branch **98.14%**. Its coverage data was directed to the repository-local `test/` directory. Python production sources were unchanged in R6-S04; the subsequent Python security patch has its own verification described above.

R6-S04 commit `2c0e84bc403c928f6990ebac454fd181ad4ded1f` passed both Linux and Windows jobs in [Rust Quality Gate run 44294640749](https://github.com/nurigitdev/2608_nsl/actions/runs/44294640749), including pinned Rust 1.99.1, all 164 Rust tests and the 7 guard tests. Remote branch HEAD was verified to equal that commit before starting the separate Python patch.

Pinned `nightly-2026-11-15` LLVM instrumentation reports workspace executable-line coverage (the Rust statement figure) **99.12%** (9,027/9,107), branch **98.13%** (1,309/1,334). The authorization module reaches **100.00%/100.00%**. Defensive internal traversal/READ-finish branches remain included; physical test code and generated Unicode constant data are excluded consistently with previous slices. Rust and Python use different instrumentation and are reported separately.

External inventory, dependency edges, features and checksums are unchanged (17 packages). `Cargo.lock` SHA-256 is still `628268a256b8144d2e6f26a9db9835b620896fed75eec7a3a92b05fae7dfe562`, identical before and after frozen build.


## R6-S05 — CHECK / Partial / Unknown semantics

`StrictRuleEvaluator` implements deterministic CHECK evaluation. It requires
both the exact primitive Bool declaration and a `Value::Bool`; integers (including
0/1), strings, null, Decimal, collections and domain/enum aliases cannot use
truthiness. A wrong declaration/value produces typed `CheckEvaluationError`
with the Oracle message `CHECK predicate is not Bool`.

| Predicate completeness | Bool value | Status | Reason |
| --- | --- | --- | --- |
| COMPLETE | true | PASS | null |
| COMPLETE | false | FAIL | null |
| PARTIAL | either | UNKNOWN | PARTIAL_INPUT |
| UNKNOWN | either | UNKNOWN | UNKNOWN_COMPLETENESS |

Presence is preserved independently; it is not a truthiness or completeness
substitute. A complete known-empty aggregate may pass, while the same aggregate
with partial/unknown facts cannot pass. Severity and `on_fail` do not override
these rules. The evaluator retains the Oracle's legacy `NSL-0.1-STRICT` name for
both supported language/IR profiles; this does not alter their admission rules.

`ReadEvaluator::execute_check` evaluates the condition through the existing
iterative READ-capable expression machine, records the structured `CheckResult`,
and immutably binds its status in the current CHECK namespace. Results preserve
check ID, severity, message, condition node ID, presence, completeness, ordered
provenance (including duplicates) and reason. The bound value has the exact
`CheckResult {status: CheckStatus(PASS, FAIL, UNKNOWN)}` type, PRESENT/COMPLETE,
INTERNAL classification and the predicate provenance. The status is a known
enum even for UNKNOWN facts; the context still retains the original incomplete
facts. This distinction matches the frozen Oracle.

Condition/type/provider/authority/resource failures do not append a check or
publish a binding. A binding collision happens *after* appending the result,
matching the Oracle's error-side state; the original immutable value remains.
FAIL/UNKNOWN are outcomes, not thrown errors or a new execution-stop policy.
`CheckExecutionError` preserves typed evaluation, READ and binding errors without
chaining provider details. A non-CHECK statement is rejected before any service
or argument effect.

This Slice adds no statement dispatcher, FOREACH/EMIT, WRITE admission/execution,
audit wire format or final public-result projection. CHECK summary inputs are
ready for later controlled WRITE integration: `ValidationSummary::from_checks`
retains HAS_FAIL, HAS_UNKNOWN and HAS_FAIL_AND_UNKNOWN separately from ALL_PASS
and NO_CHECK. The actual WriteGuard/eligibility integration remains R8-S03;
`NSL-RUST-RUN-007` is not claimed complete by evaluating CHECK alone. Raw internal
result metadata continues the R6-S01 model contract; final user-safe projection
remains R6-S07. Existing READ capability/scope security guards are unchanged.

### R6-S05 verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R6-S05-RULE-ORACLE` | **288 observations** in `tests/check_oracle.rs`: complete Bool true/false, both presences, all three completeness states and four classifications, empty/single/ordered duplicate provenance, malformed Bool values and declarations, messages/Unicode, severity/on_fail metadata and exact errors. |
| `R6-S05-RUNTIME-ORACLE` | **114 observations** in `tests/check_oracle.rs`: IR 1.0/1.1, READ partial acceptance on/off, known-empty and incomplete aggregation, exact result/binding/provenance, Bool failures, collisions in all four namespaces, missing scopes, provider/type/hash/timeout failures, tool-call and collection boundaries, monotonic deadline boundaries and error-side resource/clock/invocation state. |
| `R6-S05-BOUNDARIES` | **4 tests** in `tests/check_boundaries.rs`: both NSO versions round-trip with true/false × all completeness states and a real deterministic MockToolExecutor; known status lookup; invalid statement and safe typed errors; immutable binding/append order, sibling frames and independent contexts; empty/one/mixed actual CHECK summaries. |
| `R6-S05-DETERMINISM` | Both fixture files generated twice from a fresh archive of the pinned Oracle; bytes and SHA-256 match. Their LF attributes preserve bytes on Windows. No expected result patching or new Oracle exception. |

| Rust SRS | R6-S05 status and evidence |
| --- | --- |
| `NSL-RUST-RUN-006` | IMPLEMENTED for the CHECK component: PASS/FAIL/UNKNOWN, exact Bool, partial false-PASS prevention and structured outcomes; all 402 frozen observations match. |
| `NSL-RUST-CTX-007` | IMPLEMENTED for CHECK propagation: condition facts, result provenance and context completeness retained, with exact COMPLETE status binding. Whole-engine Result integration remains R6-S07. |
| `NSL-RUST-RUN-005`, `NSL-RUST-CTX-008` | CHECK binding uses the existing immutable, execution-local namespace; collisions, sibling scopes and independent contexts verified. |
| `NSL-RUST-RUN-007` | PARTIAL: actual CHECK outcomes feed the existing validated summary without upgrading FAIL/UNKNOWN to ALL_PASS. Provider WRITE gating/eligibility remains R8-S03. |
| `NSL-RUST-RUN-011`, `NSL-RUST-CTX-004/005/006` | Existing READ/collection/tool/deadline accounting and monotonic clock order remain intact; no CHECK-only charged tool calls. |
| `NSL-RUST-RUN-012/014` | Typed malformed predicate/non-CHECK rejection and safe error-chain tests; no new panic path from external CHECK values. |
| `NSL-RUST-RUN-017`, `NSL-RUST-TOOL-010`, `NSL-RUST-SEC-001/002/003` | Deterministic fixtures, injected provider/authority only, no direct business-system access, dynamic execution, new dependencies or crate edges. |

The reference remains `28a949ec96581cbdc52c8133527b23bb5b72ee0a`.
Generate from the repository root with Python 3.12 and an archive of that commit:

```bash
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_checks.py "$oracle_dir" rules
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_checks.py "$oracle_dir" runtime
```

| Fixture | SHA-256 |
| --- | --- |
| `check-rules-oracle.tsv` | `43888823c8a6f50e39cf4f08a9f1b600da80d69873a185591d723a0cf7e04328` |
| `check-runtime-oracle.tsv` | `60c12a434c40f55b0fef622778da67ee388d7aff2106f16d923ed6b7e9cef57c` |

Local stable **Rust 1.99.1** passes fmt, locked workspace check, all-target /
all-feature Clippy with warnings denied, the dependency guard and **7 guard
tests**, **170 Rust tests** (164 before this Slice), and frozen workspace build.
The mandatory Python quality gate ran once for R6-S05: **2,391 passed**,
statement **99.28%**, branch **98.11%**, including traceability, architecture,
performance and certification checks. Existing Python sources and their
separate READ security patch are unchanged.

Pinned `nightly-2026-11-15` LLVM instrumentation reports workspace executable-line
coverage (the Rust statement figure) **99.14%** (9,114/9,193) and branch coverage
**98.14%** (1,321/1,346). The new CHECK module is **100.00% / 100.00%**
(86/86 lines and 12/12 branches). The same production-source aggregation as the
previous Slice is used; test code is excluded. These are separate measurements
from Python coverage.

Oracle commit/wheel lock, previous compatibility fixtures, Python SRS fingerprints
and statuses, external package inventory (17 packages) and crate edges are
unchanged. `Cargo.lock` SHA-256 remains
`628268a256b8144d2e6f26a9db9835b620896fed75eec7a3a92b05fae7dfe562`.


## R6-S06 — bounded FOREACH and typed EMIT

`ReadEvaluator::execute_block`, `execute_foreach` and `execute_emit` add statement
execution using Typed IR and the existing injected READ/authority ports. The
block driver handles LET, CHECK, FOREACH and EMIT with an explicit work stack;
it does not recursively await child blocks. Approval remains an unsupported
controlled-service statement. Whole-engine profile/hash/input admission, final
public result/error projection and audit/snapshot wire integration remain R6-S07
and later slices; no live WRITE service is introduced.

### Iteration, scopes and resource order

FOREACH checks the existing **16-level** depth bound before evaluating its
collection. After expression evaluation, it requires both a List declaration
and a List value, checks the per-node maximum with exact integers, then validates
all elements recursively before entering any iteration. Each iteration consumes
one global loop allowance before pushing a frame and immutably binding its
iterator. Elements inherit collection completeness, classification and ordered
provenance; presence is PRESENT, matching the Oracle. Completed sibling frames
can reuse names; active ancestors cannot be shadowed. Iterator, local LET and
CHECK bindings disappear when their iteration ends; emitted/check results remain
execution-wide.

A scope-restoration guard also unwinds frames and restores the caller's original
FOREACH depth on errors or a dropped pending driver future. It retains completed
work and charged READ/iteration counters. Successful statement completion
restores the prior current-node identity; failure keeps the failing node, as in
the Oracle. Tool timeout/cancellation/cleanup semantics remain the R6-S02 port
boundary; scope cleanup on driver drop makes no new provider-cleanup promise.

Block deadlines, collection evaluation, per-node bounds, global loop limits and
EMIT limits retain the Oracle's order. Existing expression/deadline failures can
preempt statement validation while obtaining a value. Counts and per-node limits
are arbitrary-precision integers, including direct-component zero/negative
boundary observations; strict NSO positive-limit admission is unchanged.

### Output and approved type-validation differences

EMIT checks deadline/row allowance first, then validates the exact output name
set, including duplicate declarations/fields. It evaluates expressions in EMIT
field order, checks each declaration, recursively validates values against the
existing Core `TypeRef::accepts`, and enforces classification flow. It publishes
one `EmitRecord` in **declared output order** and increments `emitted_rows` only
after every field passes. Empty output rows still consume one row. Values retain
actual classification, presence, completeness and provenance. A failed later
field publishes no partial row; earlier READ effects and accounting are retained.

The user explicitly approved stricter malformed-value rejection while retaining
Oracle `28a949e`. Python's `_validate_runtime_type` skips List/Record shape and
recursive checks, and FOREACH uses Python len/iteration without requiring a List.
[The mock-only probe](tests/fixtures/probe_python_collection_types.py) crosses
NSO encode/decode and ordinary full `RuntimeEngine.execute` in both IR versions:
List<Int> input `"ab"` iterates twice; a wrong list element, wrong Record field,
extra field, or even scalar `17` under a List output declaration can complete.
Both frozen and current Python reproduce all **12 observations per tree**, with
zero Tool/provider calls. This is a confirmed type-validation gap, not a claim
of deployment compromise. Current Python is not patched by this Rust Slice.

Rust instead rejects non-List/mismatched FOREACH collections and malformed nested
EMIT values. `StatementTypeError { node_id, target }` retains the **statement**
node identity, has no diagnostic code, and contains no rejected value. Targets
are FOREACH collection, EMIT declaration or EMIT value/field. `StatementLimitError`
retains per-node/depth limit identity with the exact Oracle messages;
`StatementExecutionError` preserves typed READ, CHECK, binding, model, limit and
contract errors without exposing provider source chains. Public error projection
is still the later engine boundary.

The **68 approved exceptions** retain the Oracle's original successful outcomes
in a separate fixture, not rewritten rejection expectations. Tests require Rust
type rejection before body/provider effects, loop charging, output publication or
row charging. Normal observations compare structured outputs/checks, exact
metadata/provenance, current node, surviving bindings, frame/depth restoration,
resource counts, monotonic clock calls and captured Tool authorization/requests.
The component normalizer compares Oracle error class/message and existing READ
identities; extra typed statement-node identity is asserted independently.

### R6-S06 verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R6-S06-STATEMENT-ORACLE` | **306 matching observations** in `tests/statement_oracle.rs`: both IR versions, empty/one/multi-item iteration, LET/CHECK integration, all completeness/classification states, exact field order, sibling scopes and escape/collision rejection, -1/0/1/MAX-1/MAX/MAX+1 limits, depth/current-node/clock ordering, Tool and authorization failures, output/schema/type/classification errors and retained partial progress. |
| `R6-S06-TYPE-EXCEPTION` | **68 explicitly approved differences** in the same suite: non-List values or declarations, wrong elements, nested lists/records and missing/extra/wrong-typed Record fields. Rust type errors carry the original statement node; no body/provider or output-row side effects. |
| `R6-S06-API-BOUNDARIES` | Component entrypoints reject wrong statement kinds before effects. Tests use the same scripted-clock environment for matching and malformed observations, covering generic API branches without excluding code from LLVM measurement. |
| `R6-S06-SCOPE-BOUNDARIES` | **6 tests** in `tests/statement_boundaries.rs`: NSO 1.0/1.1 byte round-trip and output metadata/order; staged EMIT with retained prior READ costs; dropped pending nested driver and caller-state restoration; depth 15/16/17 on a 256 KiB stack, 100-digit limits and a 3^16 branching tree stopped at 100 charged iterations; safe typed errors and Send futures; **128 bounded generated inputs** combining malformed last elements and collection/loop/row limits. |
| `R6-S06-DETERMINISM` | Both fixture files generated twice from a fresh archive of the pinned Oracle; bytes and SHA-256 match the repository copies. Explicit LF attributes preserve them on both CI operating systems. |

| Rust SRS | R6-S06 status and evidence |
| --- | --- |
| `NSL-RUST-RUN-008` | IMPLEMENTED for bounded statement execution: per-node/global/depth/collection limits, exact integer accounting and cleanup verified. |
| `NSL-RUST-RUN-009` | IMPLEMENTED for EMIT component: exact names/types/classification, recursive approved validation, field evaluation/declaration ordering and atomic row publication verified. |
| `NSL-RUST-RUN-005/006`, `NSL-RUST-CTX-007/008` | Immutable iteration-local LET/CHECK binding and completeness propagation integrated; whole-engine Result integration remains R6-S07. |
| `NSL-RUST-RUN-011`, `NSL-RUST-CTX-004/005/006` | Resource and monotonic clock ordering, prior-effect accounting and worst-case bounded iteration verified. |
| `NSL-RUST-RUN-012/014/017` | Typed malformed-value rejection, scope unwinding, safe errors, bounded generated-input tests and deterministic frozen observations. Wider fuzz/fault campaigns remain their planned slices. |
| `NSL-RUST-RUN-002`, `NSL-RUST-SEC-001/002/003`, `NSL-RUST-TOOL-010` | Only Typed IR and existing injected ports; no Compiler/AST, new dependency edge, dynamic evaluation or direct business-system access. |
| `NSL-RUST-RUN-003/010/013/016/018` | Existing admission/result models preserved. Complete engine preflight, input/context admission and user-safe public projection are not marked implemented by this component work. |

The reference remains `28a949ec96581cbdc52c8133527b23bb5b72ee0a`.
Generate with Python 3.12 and an archive of that commit:

```bash
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_statements.py "$oracle_dir" normal
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_statements.py "$oracle_dir" types
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/probe_python_collection_types.py "$oracle_dir"
```

| Fixture | SHA-256 |
| --- | --- |
| `statements-oracle.tsv` | `453cbe1f00ec83e27bdebf8e1e3a0e86707d3dd30043c34c310fc1c3daca96bb` |
| `statements-type-exceptions.tsv` | `1aaf1276e97a65d218ca363c54d583def6294b2e54d4343c35d28269c3fa518e` |

Local stable **Rust 1.99.1** passes fmt, locked workspace check, all-target /
all-feature Clippy with warnings denied, the dependency guard and **7 guard
tests**, **179 Rust tests** (170 before this Slice), and frozen workspace build.
The mandatory Python quality gate ran **once** across R6-S06 and its approved
type-decision continuation: **2,391 passed**, statement **99.28%**, branch
**98.11%**, including traceability, architecture, performance and certification.

Pinned `nightly-2026-11-15` LLVM instrumentation reports workspace executable-line
coverage (the Rust statement figure) **99.15%** (9,322/9,402) and branch coverage
**98.12%** (1,356/1,382). The statement module reaches **99.52%** (208/209 lines)
and **97.22%** (35/36 branches). Its private validated-iteration invariant guard
remains included and uncovered; no source/test exclusion or changed aggregation
was used. The same production-source aggregation as previous slices is retained.
An initial branch measurement was below target; exact-name-set cases and API
boundary coverage in the shared scripted-clock harness were added before this
final measurement. Rust and Python are measured separately.

Oracle commit/wheel lock, previous compatibility fixtures, current Python's
separate READ security patch, Python SRS fingerprints/statuses, external package
inventory (17 packages) and crate dependency edges are unchanged. `Cargo.lock`
SHA-256 remains
`628268a256b8144d2e6f26a9db9835b620896fed75eec7a3a92b05fae7dfe562`.

## R6-S07 — pure runtime differential gate and R6 exit

`RuntimeEngine` composes the existing Typed IR, execution/context/resource models,
READ port and authority, expression/statement driver, CHECK results and public
result/semantic-view codecs. It accepts injected contracts, provider,
authorizer, measurements and monotonic clock; `execute` also supplies a system
monotonic clock. The caller explicitly selects production/development principal
verification, bounded semantic-IR `NsoLimits` and the Decimal encoding budget.
Wire callers must first use the existing strict NSO decoder. Runtime admission
checks the language/IR/profile pair, semantic hash, bounded analysis, registered
contract versions/hashes and actual capability/scope before binding or execution.
Extra undeclared request fields retain the Oracle's existing behavior.

Ordinary execution rejects controlled content, including nested Approval/WRITE.
`execute_pre_write_prefix` requires business identity and the Oracle's single
top-level Approval/WRITE ordering and reference, then executes only the statements
before Approval. Its result contains no approval, WRITE, suspension or eligibility
claim. Checkpoint capture, persisted evidence and resume remain R7; no live WRITE,
audit transport or snapshot storage is introduced. The Oracle generator calls
its real `execute_controlled_prefix` and compares that operation's `.result`.
The Python checkpoint side effect is outside this Slice's Rust API.

### Approved admission and summary boundaries

The pinned Oracle is unchanged. Two confirmed gaps and their user-approved Rust
handling are covered separately:

* Python `_validate_runtime_type` leaves compound List/Record shape unchecked,
  including unused Input/Context declarations. Rust recursively validates every
  declared input and resolved context before running any body statement. All
  bindings are staged before publication. Invalid values return
  `RuntimeTerminalKind::InputType(RuntimeInputTypeError { symbol_id, location })`,
  with the original Input name or Context path, no fabricated statement node,
  diagnostic code or rejected value. Missing paths/required inputs retain the
  Oracle's ordinary contract failures. Collection limits retain their existing
  order and accounting.
* Repeated CHECK IDs in NSL 0.2 can make `ValidationSummary::from_checks` fail:
  IDs must remain unique and nonempty. Rust keeps that invariant and every
  completed CHECK and output; it neither renames/deduplicates checks nor invents
  an ALL_PASS/NO_CHECK result. Summary failure returns the codeless typed terminal
  `InvalidValidationSummary` error, with `RuntimeEvidence` containing original
  checks, outputs, resource counters, completeness and current node. Measurements
  have already reached the injected sink and are not discarded. NSL 0.1 repeated
  CHECKs still complete without a validation summary, as in the Oracle.

The exception fixture retains **72 original accepted compound Input/Context
observations**, plus **2 earlier-approved statement declaration rejections** and
**2 earlier-approved malformed Int-division differences**. Original Oracle
outcomes are not patched. Eight additional observations retain the earlier
R6-S04 actual-contract capability/scope security difference: Rust rejects before
Tool authorization/provider effects. Current Python, its separate READ security
patch, and Oracle commit/wheel lock remain unchanged by R6-S07.

Expected contract/expression/authorization/tool/resource failures retain Oracle
status, code, category, message, detail and node rules. Unexpected errors return
only `NSL-E8002` and the generic user-safe message. Confidential/Restricted live
values and Core `redact_text` patterns are redacted from public error text.
Protected debug details require an explicitly configured sink, also redact live
sensitive values, and ignore sink failure. No default logger or public error
source chain reveals internal details. Terminal errors expose evidence explicitly
for trusted callers; their Display and Debug omit CHECK/output payloads.

### R6-S07 verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R6-S07-RUNTIME-ORACLE` | **246 full-engine observations** in `tests/runtime_gate.rs`: NSL 0.1 READ-only and NSL 0.2 pre-WRITE prefix, empty/scalar/list/nested/record values, all completeness states, PASS/FAIL/UNKNOWN and mixed summaries, READ/LET/CHECK/FOREACH/EMIT, missing/version/hash/profile/authority failures, provider/type/hash/identity/timeout/measurement failures, public/semantic results, exact requests, authority order, measurements and clock calls. |
| `R6-S07-SUMMARY-ORACLE` | **12 observations** across 0/1/2/3 repetitions, NSL 0.1 ordinary and NSL 0.2 ordinary/prefix. Eight produce matching normal results; four retain the Oracle's ValueError reason through a typed summary terminal error. Tests assert original IDs, every CHECK/output, one charged/measured READ and exact loop/row counters. Error-class normalization is confined to the test adapter. |
| `R6-S07-ADMISSION-EXCEPTION` | **76 frozen observations** retain original accepted/failure results separately. Native tests assert recursive Input/Context rejection before body/provider/output effects, symbol/path identity, existing statement-node identity and Int-division detail code. A malformed final binding blocks an otherwise valid READ body. |
| `R6-S07-SECURITY-EXCEPTION` | **8 frozen observations** verify actual-contract capability/scope binding before provider effects. Production verification, development unverified principals, tenant/actor validity, skill authorization, READ scopes, sensitive redaction and opt-in/failing debug sinks are exercised. |
| `R6-S07-API-BOUNDARIES` | Explicit zero IR byte/depth/node budgets, Decimal fixed-point budgets 0/1/MAX-1/MAX/MAX+1, Optional contract encoding rejection, typed cancellation/encoding terminal evidence, mismatched contracts, duplicate bindings, non-record intermediate Context paths and system-clock entrypoint. Controlled admission failures occur before authority/clock/provider use; nested controlled content is rejected by ordinary execution. |
| `R6-S07-WORST-CASE` | A 3^16 nested FOREACH workload on a 256 KiB thread stack stops at exactly 100 charged iterations, with retained partial output and zero provider calls. Repeating execution has independent resource/context state. Existing R6-S06 depth 15/16/17, per-node/global limits, driver-drop cleanup and 128 generated bounded cases also pass. |
| `R6-S07-DETERMINISM` | All four fixtures generated twice from a fresh archive of the pinned commit; exact files and SHA-256 match. LF attributes preserve these bytes on Linux and Windows. No expectation rewriting or production Python import from the current tree. |

| R6 exit condition | Evidence/result |
| --- | --- |
| v0.1 compatibility | PASS: full-engine normal results/semantic views and all existing frozen source/compiler/IR/runtime tests. |
| READ/CHECK/EMIT differential | PASS: 258 full-engine/summary observations, with the typed summary adapter and 84 separately documented approved exceptions. |
| resource worst-case | PASS: exact accounting, admission/collection/loop/row/tool/duration boundaries and the bounded branching-tree/cleanup tests. |
| no live WRITE available | PASS: ordinary execution rejects controlled content; prefix stops before Approval/WRITE and the suffix READ. No WRITE/provider request is captured in the matching corpus. |

| Rust SRS | R6-S07 status and evidence |
| --- | --- |
| `NSL-RUST-RUN-003/010` | IMPLEMENTED for pure runtime: compatibility/hash/contract admission and recursive Input/Context validation before body execution. |
| `NSL-RUST-RUN-004/005/006/008/009/011/016/017`, `NSL-RUST-CTX-004/005/006/007/008` | IMPLEMENTED for READ-only/pre-WRITE result integration: exact public/semantic results, immutable bindings, checks, bounded statements, completeness, resources and repeatability. |
| `NSL-RUST-RUN-013/014` | IMPLEMENTED for engine error projection: generic public unexpected errors, sensitive redaction and explicit protected debug sink. |
| `NSL-RUST-RUN-012/018` | PARTIAL overall: typed malformed-value/cancellation/encoding handling and existing credential admission verified; wider external fuzz/fault and Trace/Replay integration remain planned. |
| `NSL-RUST-RUN-007` | PARTIAL overall: summaries preserve FAIL/UNKNOWN/NO_CHECK and summary construction can terminate safely; controlled WRITE eligibility remains R8-S03. |
| `NSL-RUST-RUN-001/002/015`, `NSL-RUST-TOOL-010`, `NSL-RUST-SEC-001/002/003` | Typed IR and injected ports only; no new crate edges/dependencies, compiler/AST dependency, dynamic evaluation or direct enterprise access. |

Generate using Python 3.12 and an archive of the fixed
`28a949ec96581cbdc52c8133527b23bb5b72ee0a`:

```bash
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_runtime_gate.py "$oracle_dir" normal
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_runtime_gate.py "$oracle_dir" summary
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_runtime_gate.py "$oracle_dir" admission
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_runtime_gate.py "$oracle_dir" security
```

| Fixture | SHA-256 |
| --- | --- |
| `runtime-gate-oracle.tsv` | `37ff61e7573dba817b1c2874f8d4969c3dc2bce42cd5b193c883d9f5f8d9b5cb` |
| `runtime-summary-oracle.tsv` | `9fe6474db07e51a2c24bc578f82a90ab20ee3519a61d74b66d255705076643c1` |
| `runtime-admission-exceptions.tsv` | `b35f5ceaaa93fe9fd40c6e34582eabdea5b8ebc7770111e21ec31ced980cca27` |
| `runtime-security-exceptions.tsv` | `cbe3ebe3d5c695f7d161337cdf6a882a2a3a92ccfd20001eb544ce61325e40f9` |

Stable **Rust 1.99.1** passes fmt, locked workspace check, all-target/all-feature
Clippy with warnings denied, dependency guard and **7 guard tests**, **187 Rust
tests** (179 before this Slice), and frozen workspace build. The mandatory Python
quality gate ran **once** for R6-S07 and its approved decision continuation:
**2,391 passed**, statement **99.28%**, branch **98.11%**, with traceability,
readiness, certification, architecture and performance checks passing.

Pinned `nightly-2026-11-15` LLVM instrumentation reports production workspace
executable-line coverage (the Rust statement figure) **99.21%** (9,888/9,967)
and branch coverage **98.22%** (1,432/1,458). The same production-source aggregation
as previous slices is used, without source/branch exclusions. Rust and Python
are measured independently. Additional real admission, per-node limit, binding,
Money-error and sensitive-value boundaries improved the initial Rust measurement
of 99.11% / 98.01% before the final gates.

Oracle lock, previous fixtures, Python sources and SRS fingerprints/statuses,
external package inventory (17 packages), dependency directions and `Cargo.lock`
are unchanged. Lockfile SHA-256 remains
`628268a256b8144d2e6f26a9db9835b620896fed75eec7a3a92b05fae7dfe562`.

## R7-S01 — approval request and evidence contracts

`ApprovalRequest` and `ApprovalEvidence` add immutable, owned approval metadata
in the Runtime layer. `ApprovalBindings` keeps eight identities together:
tenant, execution, business operation, static approval ID, per-execution approval
request ID, skill semantic hash, payload hash and write-intent hash. Request
metadata also retains skill/version, WRITE node/tool, risk, message, expiry,
classification, protected payload reference and display-safe summary. Evidence
retains APPROVED/REJECTED, approver, decision/expiry instants and opaque integrity
material. Private admitted models expose borrowed metadata and detached structured
`to_data` snapshots; changing an original input/spec cannot mutate a model.

BusinessOperationId, ApprovalRequestId and PayloadHash use existing Core newtypes.
Semantic/payload hashes retain the Oracle's lowercase SHA-256 validation;
`write_intent_hash` and `evidence_integrity` retain the Oracle constructor's
nonempty, credential-free opaque strings. Canonical WriteIntent construction and
hash recomputation remain R8-S01. Static approval identity and request identity
are not interchangeable. Required-string validation preserves Python whitespace,
credential screening, field identity and rejection precedence (including multiple
invalid fields); typed enum fields exclude unregistered risk/classification/
decision values before model construction.

`ApprovalEvidence::check_bindings` compares business operation, request identity,
intent and payload in the Oracle's order, followed by tenant/execution/static
approval/skill identity. Typed mismatches preserve the existing reason strings.
This is a **structural model check**, not evidence authentication or authority to
execute WRITE. The differential adapter uses the frozen Python validator with a
deterministic true verifier solely to observe binding/time/decision semantics.
Rust proof verification, current-principal checks, claim/release/consume and
replay prevention remain R7-S04. No approval publish, checkpoint, suspension,
resume or provider path is added by this Slice.

### UTC and TTL boundaries

Core `UtcInstant` is separate from NSL business `Value::DateTime`. Its private
civil fields enforce Gregorian years 1..9999, valid calendar/time components and
microseconds 0..999999 without a binary float or timezone-framework dependency.
Explicit UTC civil construction and canonical ISO UTC parsing are supported;
naive/nonzero-offset timestamps are rejected. Z and negative-zero offsets
normalize to `+00:00`. Serialization matches Python `datetime.isoformat()`:
no fraction at zero, otherwise exactly six fractional digits.

Evidence rejects `expires_at < decided_at`; equality is admitted and is already
expired at that instant. Request and evidence expiry predicates use the exact
`now >= expires_at` boundary. `None` means no expiry. Models do not consult a
system clock, infer local time, invent a default TTL, or turn expiry checks into
Runtime duration accounting. UTC instants are explicit arguments. Like the fixed
Oracle, a request may represent an already-expired request and evidence model
construction has no independent "now"/future-decision policy.

### User-approved integer-only summary difference

The fixed Oracle accepts finite Python floats recursively in
`display_safe_summary`, including `0.5`, `1.0`, `-0.0` and exponent notation; it
rejects NaN/Infinity. The transition contract forbids primitive floats and JSON
float round trips. The user explicitly approved a narrower Rust summary boundary
while retaining Oracle `28a949e`:

* JSON metadata numbers are integers only. Decimal display is explicit text,
  such as `"0.5"`. Bool, null, strings, arrays and objects retain their meanings.
* `ApprovalSummaryInput::Number` carries a raw number lexeme, never an `f32`,
  `f64`, Decimal substitution or rounded value. Fractional/exponent/non-finite
  tokens are rejected, including integral `1.0` and signed float zero. JSON
  integer `-0` normalizes to integer zero, matching Python's JSON integer value.
* The codeless `ApprovalSummaryError { kind: IntegerRequired, path }` identifies
  a root or nested value, e.g. `display_safe_summary.nested.items[1].ratio`.
  Errors contain no rejected numeric value or nested payload/source chain.
* `SafeApprovalSummary` freezes an owned, immutable snapshot. Callers explicitly
  bound depth, node count and aggregate UTF-8 key/text/number bytes through
  `ApprovalSummaryLimits`; admission and freezing use explicit work stacks.
  Credential screening retains the null/empty-string exceptions and rejects
  credential-bearing keys/text. Debug omits the summary payload.

Original Oracle observations are preserved in a separate exception fixture;
expected successes are not rewritten to Rust rejections. Safe Core credential
errors omit the Oracle's detailed rejected material/path; the test adapter
normalizes only that existing error-category difference and typed datetime/API
error representations, not successful data or binding outcomes.

### R7-S01 verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R7-S01-MODEL-ORACLE` | **536 observations**: exact request/evidence structured data, all classifications, R0/R2/R5 metadata, optional expiry/reference, empty/nested/mixed summaries, wide exact integers, required IDs, whitespace, credential material, malformed/uppercase/non-hex/wrong-length hashes, rejection precedence, rejected decision and decision/expiry chronology. |
| `R7-S01-BINDING-TTL-ORACLE` | **360 observations**: each of eight binding mismatches and combined mismatch precedence, APPROVED/REJECTED, expiry absent/at decision/one hour later, and -1/0/+1 microsecond around decision and expiry. Test projection matches the frozen validator's validation/status/reason values; it does not implement resume authentication. |
| `R7-S01-UTC-ORACLE` | **11 observations**: year 1/9999, leap/century dates, zero/partial/nonzero microseconds, Z/negative-zero normalization, naive and positive/negative nonzero offsets. Exact UTC serialization or typed rejection checked. |
| `R7-S01-SUMMARY-EXCEPTION` | **14 observations** preserve eight finite-float successes and six original non-finite ValueErrors. Rust rejects every numeric token at its exact root/nested path without a diagnostic code; decimal display strings remain accepted. |
| `R7-S01-BOUNDARIES` | Runtime budget tests at 0/1/MAX-1/MAX/MAX+1, empty metadata, caller-owned mutation isolation, identity getters, exact expiry, safe Debug/Display/error chains and **128 bounded generated nested summary inputs**. Core tests cover invalid/calendar/clock/fraction boundaries, microsecond/year/leap ordering and malformed Unicode/ISO text parsing without panics. |
| `R7-S01-DETERMINISM` | All four fixtures generated twice from independent archives of the fixed commit; exact bytes and SHA-256 match. LF attributes retain bytes on both CI operating systems. |

| Rust SRS | R7-S01 status and evidence |
| --- | --- |
| `NSL-RUST-GATE-001` | IMPLEMENTED for request/evidence models: separate identities, human decision, protected references and opaque integrity fields; no boolean-only approval model. |
| `NSL-RUST-GATE-002/005` | PARTIAL overall: structural business/request/payload/intent/identity binding and mismatches verified. Authenticated proof validation and WRITE blocking remain R7-S04/R8. |
| `NSL-RUST-GATE-004` | PARTIAL overall: rejected decision and exact expiry represented and tested; execution enforcement remains later slices. |
| `NSL-RUST-CORE-005/011` | Existing identity newtypes reused; IMPLEMENTED UTC evidence instant boundary with exact microsecond canonical serialization and chronology. Business DateTime behavior remains unchanged. |
| `NSL-RUST-GATE-003/006/007/008/009/010` | Remaining publish/checkpoint/suspension/current-identity/resume/claim responsibilities retain their planned slices; not marked implemented by these model tests. |
| `NSL-RUST-MONEY-001`, `NSL-RUST-SEC-001/002/003`, `NSL-RUST-RUN-001/002/014/018` | No floating-point business values, new external dependencies/crate edges, dynamic execution or direct business-system access. Core credential admission and payload-safe diagnostics retained. |

Generate with Python 3.12 and an archive of
`28a949ec96581cbdc52c8133527b23bb5b72ee0a`:

```bash
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_approval_models.py "$oracle_dir" models
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_approval_models.py "$oracle_dir" bindings
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_approval_models.py "$oracle_dir" time
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_approval_models.py "$oracle_dir" floats
```

| Fixture | SHA-256 |
| --- | --- |
| `approval-models-oracle.tsv` | `1acc98a0747d015ca5fd43bbd333560821c9f5c502b0e0978192db8b38a7cf47` |
| `approval-bindings-oracle.tsv` | `f632f75c9f59e2ae286f8e51dcb49126ddacadd10cc95ed2bb668f7a79280d10` |
| `approval-time-oracle.tsv` | `892f357eea539af33b15ccb5110f8ec0b7ac9856e175656fb11116083e272372` |
| `approval-summary-exceptions.tsv` | `cbfe66e8126de35349b7f2939637a9b9ea962ac392a068955b18edcc60f3b7e2` |

Stable **Rust 1.99.1** passes fmt, locked workspace check, all-target/all-feature
Clippy with warnings denied, the architecture guard and **7 guard tests**,
**196 Rust tests** (187 before R7-S01), and frozen workspace build. The mandatory
Python quality gate ran **once** for R7-S01 and its approved summary-decision
continuation: **2,391 passed**, statement **99.28%**, branch **98.11%**, including
traceability, readiness, architecture, performance and certification checks.

Pinned `nightly-2026-11-15` LLVM instrumentation reports production workspace
executable-line coverage (the Rust statement figure) **99.23%** (10,365/10,445)
and branch coverage **98.31%** (1,512/1,538). The same production-source
aggregation as earlier slices is used; no source or branch exclusion is added.
Rust and Python are independent measurements.

The Oracle commit/wheel lock, Python sources, prior frozen fixtures, SRS
fingerprints/statuses, external package inventory (17 packages), dependency
edges and Cargo.lock remain unchanged. Lockfile SHA-256 remains
`628268a256b8144d2e6f26a9db9835b620896fed75eec7a3a92b05fae7dfe562`.

## R7-S02 — integrity-protected execution checkpoints

The contract is specified in [CHECKPOINT.md](CHECKPOINT.md). `ExecutionCheckpoint`
owns execution/business identity, positive arbitrary-precision checkpoint version,
skill/version/semantic/IR/runtime identity, exact state bytes and a SHA-256 digest.
Business identity uses the existing Core `BusinessOperationId` newtype internally.
The canonical JSON envelope has schema **integer 1** and standard base64 state;
the digest covers canonical identity material and base64 of the **exact state
bytes**, excluding only the digest itself. Header whitespace/key order and
reference-compatible base64 aliases can normalize on re-encoding; the state blob
itself is never silently reserialized while verifying its digest. This is an
integrity checksum, not authenticated storage, a signature or WRITE authority.

The default checkpoint `runtime_version` remains the Oracle audit constant
**`0.1.0`**, distinct from the distribution **0.2.1**. A caller may explicitly
supply a different coordinator runtime identity; restore requires an exact match.
No package/runtime version substitution is made to the frozen hash material.

### Capture, restore and limits

`CheckpointCoordinator::capture` snapshots exact principal/policy, input/context
bindings, paired variable/CHECK frames, CHECK outcomes, output rows, resource
usage, completeness, invocation/depth/current-node state, approvals, write journal,
idempotency maps and checkpoint progress/request/payload/intent mappings. It
increments the source context version only after serialization, integrity and
state validation succeed. Failures retain the original context/version.

`restore` verifies integrity first, then execution/business/skill/version/semantic/
IR/runtime bindings and exact current security context. It decodes a validated
state before creating a fresh context. The restored guard receives the stored
meter, retaining charged work and observed collection maxima; the injected
monotonic clock is read **once** to start the restored execution segment. The
reference does not store elapsed duration, and no new wall-clock/duration policy
is introduced. Frames, iteration-local values and CHECK namespaces retain their
original order and resolution behavior; independent restores do not share mutable
state. State JSON object fields, including restored output fields, follow the
canonical sorted-key ordering of the Oracle's restored dictionaries.

Callers explicitly provide `CheckpointLimits` for outer document bytes/depth/nodes,
decoded state bytes/depth/nodes, and Decimal token expansion. Base64 decoded size
is bounded before allocation. Native capture preflights borrowed values/types,
raw journals, provenance and metadata/counters before recursive Core projection
or cloning, then checks exact canonical JSON cost before allocating output bytes.
Large counters are bounded before version increment. State and outer limits
remain separate. No implicit resource budget is selected by the library.

The existing iterative, duplicate-key-rejecting UTF-8/JSON parser was extracted
from IR into Core `parse_json_with_limits` for reuse. The NSO facade preserves
its limits, canonical behavior, byte offsets and existing error messages. All
**196 pre-Slice tests** passed before and after that extraction. The new generic
JSON error path is constructed only on failure, avoiding repeated long-ancestor
key copies on successful values (16 KiB key / 2,000 children test), and is
credential-redacted; NSO continues projecting its existing
error surface. No new dependency or crate edge is needed.

### Approved strict restoration differences

The Oracle remains `28a949ec96581cbdc52c8133527b23bb5b72ee0a`. The user explicitly
approved strict state admission rather than reproducing these malformed states:

* bool/float aliases of integer schema/version fields are rejected;
* every envelope must recursively conform to its Core TypeRef; scalar-for-list,
  wrong elements, primitive floats and non-finite Decimal values are rejected;
* credential-bearing state/metadata is rejected through existing Core screening;
* FOREACH depth must fit the existing **0..=16** bound; huge integers never truncate;
* CHECK message/provenance/container kinds are validated without Python coercion;
* caller-specified size/depth/node limits reject excessive wire and native states.

`CheckpointError { kind, path, message, offset }` carries a safe path and typed
kind with no NSL diagnostic code, rejected payload or underlying source chain.
Checkpoint/Spec Debug omit state and identity payloads. Stronger validation occurs
before a context is published or its clock is started; unlike the Python loader,
invalid state cannot trigger clock construction before a later field fails.

Captured idempotency/payload/intent strings retain the Oracle's model boundary;
actual WRITE identity validation still belongs to the existing Core/WRITE boundary.
The journal decoder preserves Python's finite Decimal/Money and business Date/
DateTime normalization, including signed zero, offset precision and the frozen
Python 3.12 subsecond-zero-offset/fourth-colon normalization behavior. Security
UTC instants remain the separate R7-S01 contract.

This Slice adds no checkpoint store, approval publisher, suspension coordinator,
resume claim/consumption, provider invocation or automatic retry. These remain
R7-S03/R7-S04 and subsequent WRITE slices. The pre-existing READ/prefix API is not
silently expanded into a durable approval lifecycle.

### R7-S02 verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R7-S02-CHECKPOINT-ORACLE` | **64 observations** in `tests/checkpoints.rs`: IR 1.0/1.1, empty/populated/typed-value/completeness states, checkpoint versions from 0/1/2 and a 41-digit counter. Exact state bytes, canonical envelope, integrity hash and post-restore recapture bytes match. Both fresh native snapshots and restored historical snapshots are exercised; historical resource usage may exceed current limits and remains preserved, matching the Oracle. |
| `R7-S02-NEGATIVE-ORACLE` | **28 original rejections**: resigned execution/business/skill/semantic/IR/runtime and security mismatches, malformed frames/resources/checkpoint state, invalid values/statuses, unknown fields, duplicate JSON keys, invalid UTF-8, base64 and integrity failures. Typed JSON diagnostic text is native; acceptance/rejection and identity paths are checked rather than equating Python line/column messages. |
| `R7-S02-STRICT-EXCEPTION` | **13 original Oracle successes** retained separately: outer/inner schema/version aliases, recursive type mismatch, float/NaN/credential values, depth 17/huge counter, coerced CHECK provenance/message. Native errors are codeless, payload-safe and reject before clock/context publication. |
| `R7-S02-BASE64-ORACLE` | **24 observations**: binary payload lengths 0/1/2/3/4/5/63/64/65/255/256/257, exact envelope/digest identity, reference-compatible pad-bit/padding aliases and malformed alphabet/padding rejection. |
| `R7-S02-VALUE-ORACLE` | **39 observations**: decoded journal values and exact recapture normalization; Decimal/Money, extra tag fields, nested bool/null/empty records, dates, naive/UTC/offset/fractional-second business times, malformed calendars, values and time fields. |
| `R7-S02-BOUNDARIES` | Outer/state byte/depth/node limits at 0/1/MAX-1/MAX/MAX+1, Decimal budgets, snapshot version rollback on every capture failure, restored scope exit and guard meter, independent contexts, missing business identity, custom runtime identity, malformed header/schema fields, huge/deep native values/types/journals/counters and **128 bounded generated corruptions**. |
| `R7-S02-JSON-REUSE` | Existing NSO regression/fuzz suites pass through the shared Core parser; dedicated Core tests check nested float path, duplicate-safe canonicalization, limit errors and credential-redacted JSON paths. |
| `R7-S02-DETERMINISM` | All five fixtures generated twice from independent archives of the fixed commit; exact files and SHA-256 match. LF attributes preserve bytes on Linux and Windows. No frozen expected outcomes are rewritten. |

| Rust SRS | R7-S02 status and evidence |
| --- | --- |
| `NSL-RUST-GATE-007` | IMPLEMENTED for checkpoint codec/capture/restore: exact state/identity/integrity, canonical bytes, versioning and corruption rejection. Durable storage and authenticated resume remain their planned boundaries. |
| `NSL-RUST-GATE-008` | PARTIAL: checkpoint/request/skill/runtime and exact principal/policy binding implemented; current-authority/proof checks and claim lifecycle remain R7-S04. |
| `NSL-RUST-CTX-001/003/004/007/008` | Execution/business identity, execution-local namespaces, frames, original counters/completeness and independent restored state verified. |
| `NSL-RUST-CTX-005/006` | Restored monotonic guard starts once and retains the original meter; no wall-clock accounting or elapsed-time inference is introduced. |
| `NSL-RUST-CORE-005`, `NSL-RUST-RUN-012/014/018` | Core business identity reused, bounded parsing/capture, typed safe failures, credential screening and no payload/debug/source-chain exposure for this boundary. Wider fault/fuzz/Trace/Replay work remains planned. |
| `NSL-RUST-GATE-003/004/005/006/009/010` | No new implementation claim for approval policy, proof, human workflow or duplicate resume/provider effects. |

Generate with Python 3.12 and an archive of the fixed Oracle:

```bash
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_checkpoints.py "$oracle_dir" normal
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_checkpoints.py "$oracle_dir" negative
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_checkpoints.py "$oracle_dir" exceptions
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_checkpoints.py "$oracle_dir" wire
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_checkpoints.py "$oracle_dir" values
```

| Fixture | SHA-256 |
| --- | --- |
| `checkpoints-oracle.tsv` | `23cbe6b77e50511ce937af58ee27fc8266245526ff12b1361c0bf6be707944ee` |
| `checkpoints-negative-oracle.tsv` | `be8397f72337e3cae6fee7894516187183333f4d08363d32e717e3ffd6573501` |
| `checkpoints-exceptions.tsv` | `2825eb5eb5eec222ebc83017a31779a039fd2b00f11c6cb08406e284ded50a5c` |
| `checkpoints-wire-oracle.tsv` | `f6edbe1853d756598792e57d0874c12e10290f4aa16885491865bc120a93d5d3` |
| `checkpoints-values-oracle.tsv` | `7eb0e670f779c0980e7ea9f76d33254f51c08f9fed3337ab56b097abc562a25f` |

Stable **Rust 1.99.1** passes fmt, locked workspace check, all-target/all-feature
Clippy with warnings denied, the dependency guard and **7 guard tests**, **211
Rust tests** (196 before R7-S02), and frozen workspace build. The mandatory Python
quality gate ran **once** for R7-S02 and its approved strict-validation
continuation: **2,391 passed**, statement **99.28%**, branch **98.11%**, including
traceability/readiness/certification, architecture and performance checks.

Pinned `nightly-2026-11-15` LLVM instrumentation reports the existing workspace
`crates/**/src/` executable-line aggregation (the Rust statement figure) **99.08%**
(11,727/11,836) and branch coverage **98.29%** (1,720/1,750). The same measurement
script/aggregation is used, with no added source or branch exclusions. Rust and
Python are measured separately. Initial measurements were below target; concrete
malformed-state, budget/preflight, header/base64 and temporal boundary cases were
added before the final passing measurement.

Oracle commit/wheel lock, existing Python sources and SRS fingerprints/statuses,
previous compatibility fixtures, external package inventory (17 packages), crate
dependency directions and Cargo.lock remain unchanged. Lockfile SHA-256 remains
`628268a256b8144d2e6f26a9db9835b620896fed75eec7a3a92b05fae7dfe562`.

## R7-S03 — Approval Suspension Coordinator

`ApprovalSuspensionCoordinator` is a standalone execution-context component with
injected async `CheckpointPort::save` and `ApprovalPort::publish` boundaries. Its
checkpoint coordinator requires the existing explicit `CheckpointLimits`. There
is no runtime filesystem/network access, background task or human-decision wait.
A successful call returns `ApprovalSuspension` containing the captured checkpoint
and a `WAITING_FOR_APPROVAL` result, releasing the worker after the two port calls.
The result retains checks, outputs and resource usage, uses the existing partial/
unknown completeness rules, and includes Pending approval and `APPROVAL_REQUIRED`
suspension metadata. Saved state has `approval_request_published=false`; only the
live context becomes true after publication acknowledges success, as in Python.

Before mutation the coordinator checks tenant, execution, business operation,
skill ID/version and semantic hash. For language 0.2 it additionally requires an
explicit `PreWriteEvidence`, an exact summary of current unique CHECK IDs,
`ALL_PASS`, COMPLETE data, all nine host evidence controls and an approval status
that requires approval (NotRequested/Pending/Expired). Approved and Rejected are
not suspension eligibility states. `approval_evidence_valid` does not convert an
Approved state into a suspension. The evidence constructor defaults all controls
to false, matching Python. These booleans are a **trusted host snapshot**, not
proof authentication, inferred authority or permission to execute WRITE. Full
WriteGuard/evidence construction remains R8-S03. The Oracle's legacy language
0.1 component behavior is retained: it does not require this v0.2 evidence and
omits validation/eligibility extensions. The READ/prefix engine remains unchanged.

### Approved retry binding difference

The fixed Oracle remains **28a949ec96581cbdc52c8133527b23bb5b72ee0a**. For a WRITE
node already bound to the same ApprovalRequestId, both stored payload and intent
bindings must exist and equal the incoming request. Rust rejects missing or
mismatched values **before any context mutation, checkpoint capture/save or
approval publication**. It returns `ApprovalSuspensionErrorKind::Type` with no
NSL diagnostic code, safe static text and a logical path, for example:

```text
$.checkpoint.payload_hashes_by_write_node["write0001"]
$.checkpoint.write_intent_hashes_by_write_node["write0001"]
```

All state is preserved, including approval records, suspension/publication flags,
version, namespaces, checks, outputs and counters. If both bindings are invalid,
payload is reported first. A different prior ApprovalRequestId retains the
Oracle's earlier binding rejection. The original Oracle outcomes are retained in
`suspensions-exceptions.tsv`: changing a payload/intent first saves version 2,
then an idempotent publisher rejects the ID collision; removing a map entry is
silently repaired and republished by Python. These original outcomes have not
been rewritten into Rust expectations. Existing Python and the Oracle are not
patched by this Slice.

An unchanged retry remains valid, including recovery from an unpublished saved
checkpoint. It captures a new version and calls save then publish again; the
publisher must provide idempotence by ApprovalRequestId. The tests retain one
published request across retries and verify exact Oracle checkpoint/result bytes.
There is no implicit retry. Capture failure retains the staged fields but does
not advance the version; save/publication error or dropped pending I/O retains
the captured version and staged Pending state. No rollback or durable-outcome
inference is performed: an adapter can fail after accepting the operation. An
opaque `SuspensionPortError` prevents transport messages, credentials and source
chains from entering public component errors. Checkpoint and suspension wrapper Debug omit data.

### R7-S03 verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R7-S03-SUSPEND-ORACLE` | **81 scenarios / 91 observations** from the frozen Oracle: IR 1.0/1.1, complete/partial/unknown and populated/empty states, 41-digit versions, all approval statuses, all nine controls individually denied, default/missing/mismatched evidence, no/failing/unknown/duplicate CHECKs, all six identity mismatches and prior request-ID conflict. Exact result data, semantic view, checkpoint wire/state bytes, event order, versions and publisher records match. Error comparisons retain original Python errors and check native typed kind/path and rejection rather than Python exception wording. |
| `R7-S03-RETRY-BINDING` | **8 original Oracle exception scenarios** (both IR versions) and **36 native rejection cases** across published/unpublished state, versions 0/1/41 digits, missing/changed payload, intent and both. Full canonical state is identical before/after rejection; no save/publication occurs. Unchanged retry after restoring the saved unpublished checkpoint succeeds. |
| `R7-S03-FAULTS` | Save and publication errors both before and after adapter acceptance, explicit successful retries, idempotent publication, pending save/publish Future drop, no background work or human wait, no journal/provider/idempotency effects. Staged/captured state follows the Oracle on I/O failure; errors do not claim rollback. |
| `R7-S03-BOUNDARIES` | Caller checkpoint outer/state byte budgets at 0/1/MAX-1/MAX/MAX+1; over-budget capture never reaches either port and a later explicit retry succeeds. Result status admission, immutable request/suspension data, safe escaped paths, source-chain suppression and binding-error precedence are checked. Existing checkpoint suites continue covering depth/node budgets. |
| `R7-S03-DETERMINISM` | Both fixture files generated twice from independent git archives of the fixed Oracle; byte-for-byte and SHA-256 identity verified. LF attributes preserve fixture bytes on both CI systems. |

| Rust SRS | R7-S03 status and evidence |
| --- | --- |
| `NSL-RUST-GATE-001/002/005` | PARTIAL: suspension request identity, payload/intent retry bindings and Pending metadata implemented. Actual approval proof/WRITE guard remains R7-S04/R8. |
| `NSL-RUST-GATE-007` | Existing integrity-protected codec reused for deterministic pre-publication snapshots, exact save/publish order, version and failure state; injected storage adapter is host-owned. |
| `NSL-RUST-GATE-010` | IMPLEMENTED for suspension: the component returns after port acknowledgement and never owns human workflow orchestration/waiting. |
| `NSL-RUST-RUN-007/016/017`, `NSL-RUST-CTX-001/004/007/008` | Suspension pre-WRITE CHECK/completeness evidence, identity, retained resources/result semantics and deterministic retry/state behavior verified. Full live WRITE is not claimed. |
| `NSL-RUST-RUN-012/014/015/018` | Typed limits/failures, opaque adapter errors, payload-safe Debug, dependency isolation and no direct enterprise I/O in this component. |
| `NSL-RUST-GATE-003/004/006/008/009` | No new claim of authenticated resume, proof/current principal validation, resume claim/release/consumption or provider effects. These remain R7-S04 and later slices. |

Generate the original observations with Python 3.12 and a fixed-commit archive:

```bash
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_suspensions.py "$oracle_dir" normal
.venv/bin/python -I -B rust/crates/nsl-runtime/tests/fixtures/generate_suspensions.py "$oracle_dir" exceptions
```

| Fixture | SHA-256 |
| --- | --- |
| `suspensions-oracle.tsv` | `797df6995a7a5667bc389c63d8d915beab057579cc546e02d57fec55cb53cc90` |
| `suspensions-exceptions.tsv` | `1ca9d6ddbc8dc6fc1580eb52ce3d1016f682120db61404b5bd35683a2be6cf82` |

Stable **Rust 1.99.1** passes fmt, locked workspace check, all-target/all-feature
Clippy with warnings denied, the dependency guard and **7 guard tests**, **217
Rust tests** (211 before R7-S03), and frozen workspace build. An initial Clippy
finding in test Mutex scope was corrected before the final passing checks.
The mandatory Python quality command ran **once** locally for R7-S03 and passed:
**2,391 tests**, statement **99.28%**, branch **98.11%**, including the existing
traceability/readiness/certification, architecture and performance checks. The
pre-existing v0.1 CONDITIONAL findings remain unchanged.

Pinned `nightly-2026-11-15` LLVM instrumentation, using the same workspace
`crates/**/src/` executable-line aggregation (the Rust statement figure), reports
**99.05%** (12,011/12,126); branch coverage is **98.36%** (1,741/1,770). No new
source or branch exclusions were introduced. The new suspension source itself
covers all **20/20 instrumented branches**. Python and Rust remain separate
measurements. Existing embedded source-unit-test aggregation is unchanged.

Oracle lock/wheel, previous fixtures, Python implementation, frozen requirement
fingerprints/statuses, crate dependencies, 17 external packages and Cargo.lock
remain unchanged. Lockfile SHA-256 remains
`628268a256b8144d2e6f26a9db9835b620896fed75eec7a3a92b05fae7dfe562`.
