# nsl-write — Canonical Write Intent and Idempotency

`WriteIntent` owns an immutable snapshot of evaluated argument envelopes and the
Oracle's tenant/business/node/tool/approval identities. It validates both supplied
hashes before publishing a model. `WriteIntentBuilder` resolves exactly one
matching declared IR tool, requires exactly WRITE capability, and checks argument
names/cardinality against the WRITE expression. Missing business identity,
unknown/duplicate/read/transaction tool declarations, missing/extra/wrong argument
names and a non-WRITE expression are rejected. The context is a borrowed Runtime
projection (`tenant_id`, optional business ID and admitted `SkillObject`); this
crate depends on Core/IR/Contract and the existing SHA-256 package, without a Runtime edge.

## Oracle identity contract

The unmodified fixed Oracle remains
**28a949ec96581cbdc52c8133527b23bb5b72ee0a**.

* `canonical_write_arguments` emits sorted compact UTF-8 JSON from **envelope
  values only**. TypeRef, presence, completeness, classification and provenance
  stay in the retained model and do not enter payload bytes. Map order is
  irrelevant. Decimal/Money scale and signed zero, typed temporal tags and exact
  integers are preserved. Core finite Decimal/currency and integer rules apply.
* PayloadHash is SHA-256 of `NSL-WRITE-PAYLOAD-V1\0` followed by those bytes.
* WriteIntentHash is SHA-256 of `NSL-WRITE-INTENT-V1\0` followed by canonical JSON
  containing exactly `skill_semantic_hash`, `write_node_id`, `tool_id`,
  `payload_hash`. Both hashes use the existing Core lowercase `sha256:` newtype.
* Tenant, BusinessOperationId, approval ID and tool ref are separately retained
  model bindings. Changing them alone leaves the two hashes unchanged, matching
  Python. A declared contract change in an admitted Skill changes its semantic
  hash and hence intent identity; current contract/policy checks remain R8-S03.
* `require_approval_identity` compares both admitted hashes. Drift returns
  `NEW_APPROVAL_REQUIRED`; malformed hashes are identity errors. This narrow
  comparison opens no approval/policy/provider boundary. Full proof/identity/TTL
  validation remains the existing R7 boundary and later WriteGuard.

The builder characterizes the same direct-model boundary as the Oracle; NSO
schema/semantic-hash admission stays with the existing IR codec. It evaluates no
expressions and performs no reservation, durable WRITE_REQUESTED or provider call.
Those remain R8-S02/R8-S03/R8-S04 and R9.

## Separate value and snapshot metadata budgets

Callers explicitly supply `WriteLimits { canonical: JsonLimits,
max_decimal_bytes }` for canonical values. The argument object is depth 0 and
one JSON node; generated typed tag objects/fields count toward exact output
depth/nodes. Borrowed value/collection/text/Decimal preflight happens before
projection/cloning; the encoder checks exact final byte size. No Float/bytes/set
or cyclic values inhabit the owned Core Value enum, and non-finite Decimal/Money
is rejected by Core construction. Naive/invalid business DateTime is rejected at
the WRITE boundary. CPython's 4,300/4,301 integer conversion boundary is retained.

Model construction additionally requires:

```rust
WriteSnapshotLimits {
    canonical: write_limits,
    metadata: SnapshotMetadataLimits {
        max_depth: 128,
        max_nodes: 100_000,
        max_text_bytes: 1_000_000,
    },
}
```

These are explicit example caller budgets, with no implicit defaults. The
metadata budget covers retained argument names and envelope TypeRef/status/
provenance metadata, independently of hash bytes. Intent scalar identities retain
the Oracle's required-text/hash admission rules. Metadata units are:

* the metadata map is depth 0, one node; an envelope is depth 1;
* TypeRef, three scalar statuses and provenance-list are five nodes at depth 2;
* every nested TypeRef is one node; record field names contribute UTF-8 text
  bytes; enum strings and provenance strings are additional leaf nodes;
* text bytes aggregate argument/type/record/field/enum/currency names and
  provenance strings. Fixed field labels and scalar enum labels add no text cost.

The metadata walker is iterative and borrowed. It reserves child counts before
queuing work and checks depth and text before freezing. Internal Optional metadata
is traversed without attempting its forbidden external TypeRef serialization;
value/type conformance remains the separate execution/contract boundary.
`WriteIntent::new(&spec, limits)` borrows the input spec and clones only after
metadata, value and hash checks pass. The builder likewise checks borrowed
metadata and values before its snapshot clone. Rejected unbounded input remains
in caller ownership, and the constructor performs no recursive owned-input drop.

### User-approved metadata admission difference

The Oracle accepts arbitrary envelope metadata while keeping identical hashes.
The original native prototype could clone an 8,192-level TypeRef outside its
canonical-value budget and overflow a 512 KiB worker stack. The user explicitly
approved independent metadata depth/node/text-byte limits before cloning/model
publication. Exceeding them returns a **codeless `WriteIntentErrorKind::Type`**
with a safe path such as:

```text
$.canonical_arguments["detail"].type_ref.item
$.canonical_arguments["detail"].provenance_refs[0]
```

No argument is changed and no model is published. Hash-only functions continue
ignoring metadata exactly like Python. `write-metadata-oracle.tsv` retains ten
original Oracle successes; the configured native snapshot profile admits the
seven small cases and rejects the three excessive cases. Their hash bytes stay
identical. No frozen outcome is rewritten into a rejection.

The regression constructs/owns a deliberate 8,192-level native type on a large
stack and invokes both borrowed admissions on the original 512 KiB worker stack.
Both reject before clone; the owner safely drops the original graph afterwards.
There is no deliberate abort, leaked graph, background work or provider effect.
Error paths are redacted, messages are static, source chains are absent and
Spec/Intent Debug omit payloads. Existing `NEW_APPROVAL_REQUIRED` is distinct from
codeless metadata/type/limit failures.

## Business DateTime encoding and shared Core normalization

The previous checkpoint business-temporal parser moved unchanged into Core for
reuse, with regression runs before and after extraction. Security/evidence UTC
instants retain their separate existing model. Checkpoint decoding continues the
frozen Python 3.12 `fromisoformat` behavior, including normalizing a subsecond
zero-whole-second offset to UTC. WRITE value encoding preserves the original
`datetime.isoformat()` offset instead: for example `+00:00:00.000001` remains
in payload bytes. The separate Core `canonical_datetime` formatter preserves
this distinction. Positive/negative microsecond offsets, near-24-hour offsets,
years 1/9999, offset seconds, precision, scale and zero are covered by Oracle
vectors. Naive times never become implicitly UTC.

## Verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R8-S01-VALUES-ORACLE` | 36 typed value/collection observations: exact canonical bytes, payload/intent SHA-256, metadata/order independence, Decimal/Money scale/negative zero, Unicode/control escaping, nested records/lists, enums, Date/aware DateTime and microsecond/min/max offset edges. |
| `R8-S01-MODEL-ORACLE` | 19 builder observations: exact retained identity/envelope projection and hashes; business/tenant/execution/approval/tool-ref invariants; node/tool/semantic/payload/contract drift; missing business, unknown/duplicate/non-WRITE tool and argument-name/cardinality rejection. |
| `R8-S01-GOLDENS` | All **6 recomputable original compatibility vectors**: four idempotency intent inputs plus mock Platform UPDATE and ADD_MANY provider-request arguments. Original payload/intent hashes are asserted unchanged, with no provider invocation. |
| `R8-S01-NEGATIVE-ORACLE` | 25 standalone value/hash admission observations, including valid nonempty node/tool strings, malformed/credential identities, finite-value/unsupported-model admission, naive DateTime and 4,300/4,301 integer digits. Native Core-unrepresentable values are rejected at construction rather than coerced into strings. |
| `R8-S01-ADMISSION-ORACLE` | 33 constructor/approval comparisons: empty/credential identities, malformed/forged hashes, stale semantic identity, matching approval hashes and payload/intent/both drift with `NEW_APPROVAL_REQUIRED`. Native typed kind/path is checked separately from Python exception text. |
| `R8-S01-SNAPSHOT-METADATA` | Ten original Oracle successes; independent exact metadata depth/node/UTF-8 budgets at 0/1/MAX-1/MAX/MAX+1, primitive/domain/list/record/enum/money/Optional shapes, wide metadata/provenance, safe paths, and 8,192-level small-stack regression. |
| `R8-S01-BOUNDARIES` | Exact canonical bytes/depth/nodes and Decimal budgets, empty/single/multiple arguments, large/wide/deep values, expansion preflight, invalid calendars/zones, snapshot immutability, same-count wrong names, duplicate IR names and borrowed rejection state. |
| `R8-S01-DETERMINISM` | Six files generated twice from independent archives of the fixed commit; exact bytes and SHA-256 match. LF attributes preserve bytes on Linux/Windows. |

| Rust SRS | R8-S01 status and scope |
| --- | --- |
| `NSL-RUST-WRITE-001/002` | IMPLEMENTED for canonical intent construction and admitted identity/declared-contract binding. Actual execution/current contract/policy enforcement remains the later guards. |
| `NSL-RUST-WRITE-003/004`, `NSL-RUST-DIFF-007` | IMPLEMENTED/VERIFIED for canonical payload and domain-separated intent hash parity, original golden vectors and immutable snapshot consistency. |
| `NSL-RUST-GATE-005` | Hash drift signals a new approval requirement; full approval validation stays R7 and full live WRITE permission stays R8-S03/R9. |
| `NSL-RUST-WRITE-005/006/007/008/009/010/011` | No new implementation claim for current policy/preconditions, idempotency, durability, provider atomicity or mutation. |

Generate the six original observation files with Python 3.12 and a pin archive:

```bash
.venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_intents.py "$oracle_dir" values
.venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_intents.py "$oracle_dir" models
.venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_intents.py "$oracle_dir" goldens
.venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_intents.py "$oracle_dir" negative
.venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_intents.py "$oracle_dir" admission
.venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_intents.py "$oracle_dir" metadata
```

| Fixture | SHA-256 |
| --- | --- |
| `write-values-oracle.tsv` | `8527b8e2311dd60d27a42cc1c5cf3813c5e9ad947e7a646e1cd1de9222d8a097` |
| `write-models-oracle.tsv` | `1d337dea2461491a25bde30d11940017e695ffd1d5be95c7d59bba28490b4351` |
| `write-goldens-oracle.tsv` | `91d490ad7fb907ccacbfe1e9905a64da08fcff487f7309298d4964504277cb1a` |
| `write-negative-oracle.tsv` | `ca2abc7ded3f97b8c4fb32c89f5b5d58d04f8960dfba67575c748ca9ad4173b7` |
| `write-admission-oracle.tsv` | `3d538093747ca3a649bf8b66747251083fb7b1ab46e3e0055549ee2b281b373d` |
| `write-metadata-oracle.tsv` | `75183d7135cf4deaf8d267efe8c0ba16672bab0be1acfa8a0576681625ef7383` |

Stable **Rust 1.99.1** passes fmt, locked workspace check, all-target/all-feature
Clippy with warnings denied, the dependency guard and **8 guard tests**, **244
Rust tests** (230 before R8-S01, with 14 new WRITE/metadata tests), and frozen
workspace build. Existing Rust regressions also ran before and after the
business-temporal extraction. The mandatory Python quality command ran **once**
locally for this Slice and its approved continuation: **2,391 passed**, statement
**99.28%**, branch **98.11%**, with traceability/readiness, architecture,
performance and certification checks. Existing Python v0.1 CONDITIONAL findings
remain unchanged.

Pinned `nightly-2026-11-15` LLVM instrumentation uses the unchanged workspace
`crates/**/src/` executable-line aggregation (the Rust statement figure):
**99.10%** (12,884/13,001); branch **98.45%** (1,843/1,872). The new metadata
walker covers **71/71 executable lines and 8/8 instrumented branches**; WRITE
sources together cover all **66/66 instrumented branches**. No source or branch
exclusions were added, and embedded source-unit-test aggregation is unchanged.
Rust and Python are independent measurements. Instrumented build artifacts now
use the ignored workspace target cache, keeping large temporary builds off the
system `/tmp` filesystem; measurement tools/flags and aggregation are unchanged.
The same pinned stable and LLVM toolchains were restored from official
checksum-verified distributions after the earlier temporary installation vanished.

Oracle commit/wheel lock, Python implementation, all prior compatibility
fixtures, and frozen requirement fingerprints/statuses remain unchanged. There
are no new external packages or version updates (17 packages). Cargo.lock changes
only the WRITE crate's three declared workspace/existing SHA-256 edges; its
reviewed SHA-256 is
`d45307992ead2ecdf9f4e770060912e951d0bb75bf885c53320341e81bea6e28`.


## R8-S02 — Durable Idempotency Contract

`IdempotencyKeyFactory`, immutable identity/provider-evidence/record/reservation
models, `IdempotencyRetentionValidator`, `IdempotencyEvidencePort` and
`IdempotencyCoordinator` implement the frozen Oracle's idempotency boundary.
The Oracle remains **28a949ec96581cbdc52c8133527b23bb5b72ee0a**; its code,
wheel lock, existing compatibility observations and Python SRS fingerprints are
unchanged. R8-S02 adds one permitted workspace dependency on `nsl-contract` for
current contract identity and retention semantics. No Runtime dependency or new
external package is introduced.

### Stable key and validated models

The key is `idem:sha256:` plus SHA-256 of `NSL-IDEMPOTENCY-KEY-V1\0` followed
by canonical UTF-8 JSON containing exactly `tenant_id`, `business_operation_id`
and `write_node_id`. Execution ID, payload, tool, approval and contract do not
enter this key. Payload drift therefore keeps the key and conflicts separately;
a new business operation, tenant or WRITE node gets its own identity. Transport
retry must retain the business operation ID; a genuinely new business operation
must receive a new ID. This module performs no automatic business retry.

As previously approved in R2-S01, supplied IdempotencyKey models check the exact
prefix and **76 Unicode characters**, including a 64-character non-hex/Unicode
suffix. The key factory itself always emits lowercase SHA-256 hex. PayloadHash
and contract hashes retain strict lowercase 64-hex validation. Required strings
use Python-compatible blank/credential screening without trimming or rewriting
accepted identities. Native UTC instants and Bool/enum types reject unsupported
representations before constructing a model. Evidence requires
`verified_at < guaranteed_until`; a standalone historical evidence record can
have past retention, matching Python. A CONFLICT reservation cannot carry a
business result reference. CREATED with a reference and REUSED without one are
both representable, as in the Oracle. Models expose borrowed metadata and typed
identifiers; their Debug output omits identity/evidence contents.

### Retention and reservation order

Validation requires `now < required_until`, exact intent/contract/evidence tool
binding, current canonical Tool Contract hash, exactly WRITE capability, required
idempotency with `TOOL_AND_TENANT`, `RETURN_ORIGINAL_RESULT` and
`IDEMPOTENCY_CONFLICT`, matching opaque retention-policy strings, and an explicit
durable duplicate guarantee. It accepts `verified_at == now` and
`guaranteed_until == required_until`, rejects future verification, and rejects
`now == guaranteed_until`. All comparisons use caller-supplied UTC instants at
microsecond precision; no system clock is consulted. Internal Optional Tool
Contract encoding failure is a codeless typed error, with no reservation effect,
retaining R5-S02's existing canonical encoding boundary.

The coordinator takes a borrowed `IdempotencyContext` projection of tenant,
optional business ID and the existing raw checkpoint key/payload maps. It checks:

1. Context tenant and business identity, then retention evidence.
2. Any cached key against semantic identity, then any cached payload.
3. The injected durable reservation acknowledgement against the **entire incoming
   record**, then the CONFLICT disposition.
4. Only after a matching CREATED/REUSED acknowledgement, both checkpoint maps.

Rejections preserve the original maps, including partial/foreign-node data. A
matching partial key-only or payload-only checkpoint is filled after successful
acknowledgement. Foreign acknowledgement fields are rejected with
`IDEMPOTENCY_STATE_INVALID` before interpreting its CONFLICT disposition. Cached
or durable payload drift produces `IDEMPOTENCY_CONFLICT`. Retention failures use
`IDEMPOTENCY_RETENTION_UNAVAILABLE`. Native errors carry static safe messages and
redacted paths; port failures are opaque codeless errors with no protected source.
No local cache entry is treated as proof that durable reservation succeeded.

### Host durability contract and pending outcomes

The host port must atomically scope reservations by **tenant/tool/key**, persist
before CREATED acknowledgement, retain duplicate detection through the incoming
record's `retained_until` across restarts, preserve the original stored payload
on conflict, and return the original business result on reuse when available.
Same semantic key across two tools remains isolated by that host scope.
The incoming record retains the full provider guarantee expiry, which can exceed
the requested minimum recovery horizon.

The Oracle compares a returned record to the full incoming record. A retry after
a provider guarantee reference or expiry change must therefore acknowledge the
new incoming record after checking durable state; returning only the old stored
record is rejected. This echo contract is distinct from preserving the original
business result reference. Both behaviors are recorded without changing Oracle
outcomes. `REUSED` with no business result is a pending/unknown reservation, never
a successful business WRITE or permission to invoke the provider again.

The reference test adapter uses one mutex-protected durable-state abstraction;
its retained storage survives coordinator/context replacement. It verifies atomic
reservation and result reuse, not power-loss durability of a real database.
Production storage remains host supplied through the explicit port contract.
Error/drop can occur before or after adapter acceptance: the maps stay unchanged
while durable evidence may already exist. Dropping a future does not imply
rollback, commit outcome or safe business retry. Later retry observes CREATED or
REUSED according to durable state. Runtime WriteGuard, WRITE_REQUESTED persistence
and actual provider mutation remain R8-S03/R8-S04/R9 respectively.

### R8-S02 verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R8-S02-IDENTITY-ORACLE` | 26 factory/intent observations: original stable identity, payload drift, tenant/business/node separation, Unicode/control escaping, blank and credential rejection. |
| `R8-S02-MODELS-ORACLE` | 99 original model admissions: all required bindings, hashes, supplied key lengths 0/1/63/64/65 with ASCII/non-ASCII suffixes, result references/dispositions, one-microsecond TTL constructor boundaries and minimum/maximum UTC instants. |
| `R8-S02-RETENTION-ORACLE` | 26 observations: now/horizon/verification/expiry at -1/0/+1 microsecond, exact horizon acceptance, wrong tool/hash/policy/durability/scope/duplicate/conflict behavior and READ/TRANSACTION contract rejection. |
| `R8-S02-RESERVATION-ORACLE` | 35 multi-step scenarios: CREATED, REUSED pending/original result, cached/durable payload conflict, context/partial-cache admission, all nine acknowledgement bindings, failure before/after persistence, changed guarantee/TTL echo vs stale acknowledgement, tenant/tool/business/node isolation. Success values, error class meaning/detail code, maps, calls and stored-record counts match the original observations. Python messages are retained verbatim in fixture bytes; Rust safe path/kind is checked separately. |
| `R8-S02-ATOMICITY` | Real thread races with 0/1/9/10/11/32 reservations, same and competing payloads: exactly one CREATED, same payload reused, competing payload rejected, one unchanged stored original and no map mutation on rejection. |
| `R8-S02-FAILURE` | Unpolled/dropped pending futures before/after acceptance, same-port fresh-context retry, failure-order paths, context/cache preservation, codeless contract encoding failure before the port, immutable snapshots and safe errors/Debug. |
| `R8-S02-GOLDENS` | Original duplicate/conflict compatibility observations keep the exact stable key, payload, current contract hash, retained evidence and original business result reference. |
| `R8-S02-DETERMINISM` | Four files regenerated from two independent `git archive` exports of the frozen commit: exact bytes and SHA-256 match. Explicit LF attributes preserve bytes on both CI operating systems. |

| Rust SRS | R8-S02 status and scope |
| --- | --- |
| `NSL-RUST-IDEM-001/002` | IMPLEMENTED/VERIFIED for stable identity and exact Python canonical key generation; `R8-S02-IDENTITY-ORACLE`, `R8-S02-GOLDENS`. |
| `NSL-RUST-IDEM-003` | IMPLEMENTED for the durable atomic port contract and coordinator; integration with the concurrent injected reference adapter verified by `R8-S02-RESERVATION-ORACLE` / `R8-S02-ATOMICITY`. Host database durability remains an adapter obligation. |
| `NSL-RUST-IDEM-004` | PARTIAL at the reservation boundary: same-payload reuse returns original result/pending state without creating another reservation. Business mutation E2E evidence remains the later provider execution slice. |
| `NSL-RUST-IDEM-005` | IMPLEMENTED/VERIFIED for checkpoint and durable reservation payload conflict; `R8-S02-RESERVATION-ORACLE` / `R8-S02-ATOMICITY`. |
| `NSL-RUST-IDEM-006/007` | IMPLEMENTED/VERIFIED for identity/current-contract/evidence and retention admission before reserving; live WRITE enforcement remains later guards. `R8-S02-RETENTION-ORACLE`, `R8-S02-FAILURE`. |
| `NSL-RUST-IDEM-008` | IMPLEMENTED at this API boundary: business identity is stable across transport retries, new business identity is separate, no automatic mutation retry or commit inference; `R8-S02-FAILURE`. |

Generate all four files using Python 3.12 and an unmodified frozen archive:

```bash
for mode in keys models retention reservations; do
  .venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_idempotency.py "$oracle_dir" "$mode"
done
```

| Fixture | SHA-256 |
| --- | --- |
| `idempotency-keys-oracle.tsv` | `7c0b230f2b60ec108bb9ee870194616fdec1de0bcd57cfd73ce2c0abdc45d31f` |
| `idempotency-models-oracle.tsv` | `45cda9900d785ddc1e70907e18c4a1abf76cba401e629705bf5dda5f7fe15662` |
| `idempotency-retention-oracle.tsv` | `dcbde22fe82d009838a102a9d76f2ef980e84839752fc9c454721c05c7d65078` |
| `idempotency-reservations-oracle.tsv` | `19e061de604b47c3fd3728055ff143fde710c21484a00871eb9571ab996ff6cd` |

R8-S02 local verification on pinned **Rust 1.99.1** passes formatting, locked
workspace check, architecture guard and **8 guard tests**, all-target/all-feature
Clippy with warnings denied, **253 Rust tests** (244 baseline + 9 idempotency
tests), and frozen workspace build. The mandatory Python quality command ran
**exactly once locally**: **2,391 passed**, statement **99.28%**, branch
**98.11%**, with existing traceability/readiness, architecture, performance and
certification checks. Existing Python v0.1 CONDITIONAL findings are unchanged.

Pinned **nightly-2026-11-15** LLVM instrumentation, using the unchanged workspace
`crates/**/src/` executable-line aggregation for the Rust statement figure,
measures **99.12%** (13,337/13,456); branch **98.44%** (1,888/1,918). The new
idempotency modules cover 453/455 executable lines and 45/46 instrumented branches.
No exclusions were added; embedded source-unit-test aggregation is unchanged.
These Rust and Python figures are independent measurements. Host durability and
business mutation guarantees retain the explicitly scoped later verification
obligations above.


## R8-S03 — Current-policy WriteGuard

`WritePolicyRequest`, `PolicyDecision`, `BusinessPreconditionEvidence`,
`WriteContractEvidence`, `RiskAcceptancePolicy`, `WriteGuardDecision` and
`WriteGuard` now implement the fixed Oracle's policy/precondition boundary,
with the explicitly user-approved current-principal/contract/scope admission
checks below. The frozen Oracle remains
**28a949ec96581cbdc52c8133527b23bb5b72ee0a**. Python implementation, Oracle wheel
lock, earlier compatibility observations and frozen SRS fingerprints/statuses
are unchanged.

### Ownership and authority boundary

`nsl-write` retains its Core/IR/Contract dependencies, with no Runtime/Compiler/
Source edge or additional package. `WriteGuardContext` borrows a `WritePrincipal`
view and the optional business ID. The view borrows the current principal's
tenant, subject, actor, role/scope sets, authentication-context reference,
verification metadata and optional on-behalf-of identity. It applies Runtime's
existing principal rules with verification required, including nonempty and
credential-free identities/roles/scopes, USER/SERVICE actor types, and a valid
optional delegation reference. Empty roles are allowed; they do not grant a
missing scope. Host authentication establishes verification metadata; this view
validates the admitted model and does not implement an authentication protocol.

`WriteExecutionEvidence` is the small typed projection of the existing Runtime
`PreWriteEvidence`: validation status, approval status and its ten control
booleans. Host code constructs the projection from the current admitted Runtime
snapshot; no Runtime state model is moved, cloned or given a reverse dependency.
These projections and public model constructors are not independently verified
authorization, approval or durability proofs. The host still supplies those
trusted boundaries. Validated IR/semantic admission and actual provider execution
remain the existing compiler/Runtime boundary and later controlled integration.

Models retain immutable owned snapshots, typed Core risk/capability/UTC instants,
strict lowercase SHA-256 evidence and credential-screened required text.
Policy actions are exactly WRITE. Policy expiry must be strictly after evaluation;
precondition expiry must be strictly after observation. The six precondition
mechanisms retain VERSION, REVISION, ETAG, PRECONDITION_TOKEN,
TRANSACTIONAL_REVALIDATION and EQUIVALENT_PROVIDER_MECHANISM. Risk acceptance is
R3-only by default, independently configurable as a nonempty set of Core risk
levels; repeated inputs retain at most six unique levels. Evidence/Spec Debug
omits identity/evidence contents. Model errors are codeless, contain safe paths
and static messages, and expose no protected error source.

### Approved native admission difference

The original Oracle's WriteGuard has no current Tool Contract/scope parameter and
does not validate the current principal. With otherwise matching ALLOW policy and
precondition snapshots, its unit boundary returns ALLOWED even when the required
WRITE scope is absent/empty or the principal is unverified. The user approved
preserving that Oracle while making the Rust boundary reject these cases.
`write-guard-exceptions-oracle.tsv` retains **16 original ALLOWED observations**,
with separate expected native rejections; no frozen success is rewritten.

Before evaluating policy evidence or calling the policy port, Rust checks:

1. The current principal model with verification required, then context tenant
   and business ID against the admitted WriteIntent.
2. Evidence tool/capability/precondition support and the current Tool Contract's
   tool, exactly WRITE capability, risk and canonical contract hash. The validated
   WRITE Tool Contract already guarantees atomicity and precondition support.
3. A nonempty, credential-free actual `current_contract.required_scope` and
   membership in the **current principal's** scopes.
4. The configured accepted risk set.

Invalid principals produce a codeless `WriteGuardErrorKind::Principal` with a
path such as `$.context.principal.verification`. Missing/invalid required scope
or missing current scope yields BLOCKED / `WRITE_SCOPE_DENIED`. Current contract
or contract-evidence mismatch yields BLOCKED / `WRITE_CONTRACT_INVALID`.
Internal Optional contract encoding failure retains R5-S02's codeless boundary
at `$.current_contract`. These outcomes occur with **zero policy-port calls** and
no provider, reservation, persistence or Runtime-state effect. Evidence can be
self-consistent yet stale relative to the actual current contract; it is still
rejected. Updating all evidence to a new scope still requires that new scope.

### Oracle policy, precondition and PreWrite order

After admission, policy must be present, match all eight request identity fields
(tenant, subject, action, tool, risk, business operation, node, intent hash), be
current (`evaluated_at <= now < expires_at`) and explicitly ALLOW. Missing,
foreign, future, expired or DENY policy fails closed before preconditions.
Business precondition evidence must then be present and match tenant, business
operation, node, tool, current contract hash, payload hash and intent hash. It
must be current (`observed_at <= now < expires_at`) and explicitly assert provider
atomic enforcement. The runtime decision does not claim the provider's business
condition already passed; the provider must enforce the bound mechanism during
its later atomic invocation.

Only those successful checks replace the three Oracle control flags
`current_policy_allowed`, `tool_preconditions_passed` and
`business_preconditions_passed` on a local value copy. CHECK FAIL/UNKNOWN precedence,
approval rejection/evidence validity, runtime preflight, trust, intent binding,
idempotency, durability and replay permission preserve the Oracle's original
PreWriteEvaluator ordering. `NO_CHECK` remains admissible when every other axis
allows it. Pending/not-requested/expired approval yields REQUIRES_APPROVAL after
other controls succeed. Evaluation never mutates the caller's evidence/context.
`require_write_allowed` accepts only ALLOWED; an optional reason is not permission.

`WriteGuard::evaluate(request, now)` evaluates an explicitly host-supplied trusted
policy snapshot, including its identity/TTL. `WriteGuard::authorize(request,
port, now)` is the current-authority path: after admission it calls the injected
synchronous `RuntimePolicyPort` **exactly once**, ignores any cached decision on
the request, and checks the newly returned decision. An old ALLOW cannot override
a current DENY; an old DENY cannot veto a new matching ALLOW. The caller supplies
the authoritative UTC check instant; the guard does not read a system clock or
cache authority. The host port must resolve current authority for the request.
Port failures become opaque codeless errors at `$.policy_decision`, without an
ALLOW verdict. No automatic policy/provider retry is introduced.

Like Python, standalone `WriteGuardDecision` construction checks that an optional
reason is nonblank, without interpreting or rewriting an opaque reason string.
Guard-generated reasons are fixed safe labels. The decision model is an internal
host value, not an authenticated authorization proof; public error redaction
remains the Runtime/integration output boundary.

### R8-S03 verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R8-S03-MODELS-ORACLE` | 161 original model observations: exact canonical data bytes, all required text/hash bindings, blank/control/credential rejection, WRITE-only action, all Core risk values, min/max UTC instants, -1/0/+1 microsecond constructor expiry, and decision reason admission. |
| `R8-S03-DECISIONS-ORACLE` | 70 observations: context tenant/business binding; policy missing/DENY, all mismatched identities and exact TTL edges; precondition missing/all bindings/TTL/atomicity; all six mechanisms and default/configurable risk levels; execution controls, all approval states and failure precedence. |
| `R8-S03-PREWRITE-ORACLE` | 26 observations: all five CHECK summary statuses, approval/evidence axes, all nine blocking controls and original NO_CHECK semantics. Direct evaluation and Guard preserve the existing Runtime evidence. |
| `R8-S03-ADMISSION-EXCEPTIONS` | 16 original Oracle ALLOWED observations retained separately from user-approved native rejection: missing/empty WRITE scopes, unverified/malformed current principal, stale actual contract tool/risk/scope/version/hash and blank required scope. Zero policy-port calls for every native rejection. |
| `R8-S03-CURRENT-AUTHORITY` | Fresh injected current policy once per call; request projection equality, missing snapshot, current DENY/foreign/stale/future policy and opaque port failure, cached ALLOW/DENY isolation, and repeated principal/scope/contract checks. |
| `R8-S03-BOUNDARIES` | Empty/one/5/6/7/64/65 risk inputs (set semantics, at most six retained); 0/1/63/64/65 unrelated scopes cannot grant WRITE; USER/SERVICE/delegation, required/credential identities, internal Optional contract failure, immutable evidence, safe errors and Debug, and current-scope drift after an earlier ALLOW. |
| `R8-S03-DETERMINISM` | Four files generated twice from independent fixed-commit `git archive` exports: bytes and SHA-256 match. LF attributes preserve fixture bytes across Linux/Windows. |

| Rust SRS | R8-S03 status and scope |
| --- | --- |
| `NSL-RUST-WRITE-005` | IMPLEMENTED/VERIFIED at the current-policy Guard boundary; `R8-S03-CURRENT-AUTHORITY`, `R8-S03-ADMISSION-EXCEPTIONS`. Full provider-stage orchestration remains later controlled integration. |
| `NSL-RUST-WRITE-006` | IMPLEMENTED/VERIFIED for exact business precondition binding; `R8-S03-DECISIONS-ORACLE`. |
| `NSL-RUST-WRITE-007` | VERIFIED for validated atomic Tool Contract and explicit provider-atomic-enforcement evidence admission. Actual provider atomic execution remains the host/provider integration obligation. |
| `NSL-RUST-WRITE-008` | IMPLEMENTED/VERIFIED for fail-closed verdicts, missing/current policy/precondition axes and typed admission failures; `R8-S03-DECISIONS-ORACLE`, `R8-S03-PREWRITE-ORACLE`, `R8-S03-ADMISSION-EXCEPTIONS`. |
| `NSL-RUST-SEC-005/006` | IMPLEMENTED/VERIFIED within Guard for current authority and cross-tenant principal/policy/precondition binding. Trusted issuer authentication and the full controlled pipeline remain explicit external/later boundaries. |
| `NSL-RUST-SEC-010` | Existing replay permission is preserved as a blocking PreWrite axis; complete replay/provider E2E verification remains later integration. |
| `NSL-RUST-DIFF-010` | All 257 ordinary observations match; 16 admission differences are explicitly user approved, separately recorded and tested. |
| `NSL-RUST-WRITE-009/010/011` | No new completed claim: durable WRITE_REQUESTED, full provider request and controlled mutation pipeline remain R8-S04/R8-S05/R9. |

Generate the original observations using Python 3.12 and an unmodified archive:

```bash
for mode in models decisions prewrite exceptions; do
  .venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_guard.py "$oracle_dir" "$mode"
done
```

| Fixture | SHA-256 |
| --- | --- |
| `write-guard-models-oracle.tsv` | `deb8ad15519f05d50968a1af912121b6bbb77e6e79f66b7b6e832c852eaa8116` |
| `write-guard-decisions-oracle.tsv` | `4ba64823f788d5f298f39f6ef05285a77c403e54114151b55ea4dbe1c5aa46d2` |
| `write-guard-prewrite-oracle.tsv` | `b23261d989660bad70906a9292a09ce039e46cd0186b524862fafa2042bb0c07` |
| `write-guard-exceptions-oracle.tsv` | `12de0e435059a1d42dd6475d66e94e94a2517bce583917e2282173e043092a65` |


Pinned **Rust 1.99.1** passes fmt, locked workspace check, architecture guard and
**8 guard tests**, all-target/all-feature Clippy with warnings denied, **263 Rust
tests** (253 baseline + 10 Guard tests), and frozen workspace build. The complete
Rust regression also passed before and after simplifying the new invariant checks
and bounding the risk set's retained storage. The mandatory Python quality gate
ran **exactly once locally for R8-S03**: **2,391 passed**, statement **99.28%**,
branch **98.11%**, with unchanged traceability/readiness, architecture, performance
and certification gates. Existing Python v0.1 CONDITIONAL findings remain.

Pinned **nightly-2026-11-15** LLVM instrumentation uses the unchanged workspace
`crates/**/src/` executable-line aggregation for the Rust statement figure:
**99.12%** (13,975/14,099); branch **98.42%** (1,994/2,026). New Guard modules
cover 638/643 executable lines and 106/108 instrumented branches; the Guard
admission/decision body, principal validation and PreWrite axes each cover all of
their instrumented branches. No exclusions were added; embedded source-unit-test
aggregation is unchanged. Rust and Python are independent measurements.


## R8-S04 — Durable WRITE_REQUESTED evidence

`WriteCallRequest`, `WriteCallRequestBuilder`, `WriteAttemptRecord`,
`WriteRequestedReceipt`, `WriteEvidencePort` and `WriteRequestedCoordinator`
implement the fixed Oracle's request assembly and durable evidence boundary.
The Oracle remains **28a949ec96581cbdc52c8133527b23bb5b72ee0a**, including its
original request/hash/receipt semantics. The only new workflow differences are
the explicitly user-approved reservation disposition handling described below.
Existing Python implementation, Oracle wheel lock, compatibility observations,
SRS fingerprints and Cargo.lock are unchanged.

### Ownership, snapshot admission and request bindings

WRITE still depends only on Core/IR/Contract and existing SHA-256 packages, with
no Runtime/Compiler/Source edge. `WriteCallContext` borrows tenant/execution/
business identity; `WriteEvidenceContext` additionally borrows the existing
checkpoint durable flag. No Runtime model is moved or cloned. There is no direct
filesystem/SQL/HTTP access and no database/provider implementation in Core.

A validated call snapshot owns tenant, execution, business operation, invocation,
node, tool reference/ID/current contract hash, evaluated argument envelopes,
idempotency key, payload hash, approval/request IDs, intent hash, full typed
business precondition evidence and ordered trace references. Scalar identities
use the Oracle's nonblank/credential rules. The supplied idempotency key retains
the R2-S01 prefix plus 64 Unicode-character suffix rule, including nonhex suffixes;
payload/contract/intent hashes remain strict lowercase SHA-256. Argument names
must be nonblank, matching `_freeze_arguments`; name validation does not alter
accepted names or treat metadata as payload. A standalone call constructor checks
argument payload bytes against the supplied hash, while identity binding between
separate components belongs to the builder/Guard boundaries.

`WriteCallRequest::new(&spec, WriteSnapshotLimits)` borrows the input and checks
argument values and TypeRef/provenance metadata under the existing explicit
R8-S01 caller budgets before its immutable snapshot clone. The builder likewise
preflights the borrowed intent under the new caller profile before constructing
an owned Spec. Flat ordered trace strings are screened before cloning; their
Oracle admission has no new hidden truncation/count quota. Caller-facing decoders
retain their separate overall input-size limits. No value/type conformance rule
is added to pure canonical argument hashing.

The builder checks context tenant/business identity, exactly WRITE evidence and
tool identity, all six reservation evidence bindings (tenant/business/node/tool/
contract/payload), and all seven precondition identity/hash bindings plus the
explicit provider-atomic-enforcement flag. Execution/invocation/approval-request
IDs and trace references are admitted before publishing a preparation. The
existing R8-S03 Guard remains responsible for verified current principal, actual
current contract/scope, current policy/precondition TTL, approval and other axes.
Host code must use those current boundaries; public model constructors and an
ALLOWED snapshot are not authenticated authority by themselves.

### User-approved reservation disposition handling

The frozen Python builder ignores `reservation.disposition` and its existing
business result reference. Its lower coordinator accepts a supplied ALLOWED
snapshot and can persist/invoke for CREATED, CONFLICT and both REUSED forms.
Normal IdempotencyCoordinator already rejects conflicts upstream, but the lower
builder itself lacks that check. The user explicitly approved closing this
boundary in Rust while preserving the original Oracle observations.

`WriteCallRequestBuilder::build` returns an explicit `WriteCallPreparation`:

| Reservation | Native preparation | Persistence / new provider path |
| --- | --- | --- |
| CREATED, with or without result ref | `Created(PreparedWriteCall)` | May enter the durable evidence stage after ALLOWED/context checks. |
| CONFLICT | `WriteExecutionErrorKind::Conflict`, `IDEMPOTENCY_CONFLICT`, path `$.reservation.disposition` | Zero storage/provider calls. |
| REUSED with an existing result ref | `Reused(IdempotencyReservation)` | Return the existing bound reference; zero new storage/provider calls. |
| REUSED without a result ref | `Pending(IdempotencyReservation)` | Explicit pending state; zero new storage/provider calls. |

All identity/precondition and auxiliary ID/trace admission checks still precede
the disposition handoff, so reuse cannot return foreign evidence. The original
five disposition observations remain verbatim in their own fixture: both CREATED
cases match; the three CONFLICT/REUSED cases carry the separate native outcome.
No Oracle success is rewritten into a failure. A pending reservation does not
prove business completion, and an existing result reference is not interpreted
as a new COMMITTED outcome. R9 remains the explicit provider/outcome boundary.

`PreparedWriteCall` has a private boxed request and is non-Clone; only the builder
mints it on CREATED. This keeps the enum small without weakening Clippy or
unsafe-code policy. Standalone admitted call models, REUSED and PENDING do not
mint that token. The host must obtain a genuine current CREATED reservation from
the trusted reservation flow; this type is a local handoff, not authentication or
a global exactly-once guarantee. Explicitly rebuilding from a caller-supplied old
CREATED snapshot is not automatic retry; the orchestration must not substitute
that snapshot for a fresh reservation/current authority.

### Exact record bytes and SHA-256

`WriteAttemptRecord::from_request` retains exactly these **13** frozen fields:

```text
tenant_id, execution_id, business_operation_id, invocation_id, node_id,
tool_id, contract_hash, approval_request_id, idempotency_key, payload_hash,
write_intent_hash, precondition_evidence_id, requested_at
```

Record bytes are compact sorted canonical UTF-8 JSON, with `requested_at` encoded
as UTC `datetime.isoformat()` at microsecond precision, including six fractional
digits when nonzero. Its identity is `sha256:` plus SHA-256 of
`NSL-WRITE-REQUESTED-V1\0` followed by those canonical bytes. The immutable model
caches the bytes/hash; neither generic serializer defaults nor Debug enter the
hash. Every bound field, Unicode/control escaping, a one-microsecond timestamp
change and year 1/9999 boundaries are compared with the original Oracle.

Tool reference, approval ID, trace references, full arguments and full precondition
body are retained in the call but are outside this original 13-field record
projection. Their omission is characterized, not silently changed. Payload and
intent hash/precondition evidence ID remain the existing record bindings.

### Durable acknowledgement and private handoff

The host-owned async `WriteEvidencePort::record_requested` must durably persist
the entire record before acknowledging its hash. Actual database/power-loss
durability is an adapter obligation, as with the existing idempotency port.
The injected reference adapter and test-only provider spy verify ordering and
component failure behavior; they do not claim production database durability.

`WriteRequestedCoordinator::persist` consumes only a CREATED preparation, then:

1. Requires ALLOWED, then matches execution/tenant/business identity to the
   current borrowed context before touching storage.
2. Builds the immutable requested-at record and awaits the injected port.
3. Requires the receipt's exact record hash, `durable == true` and
   `persisted_at >= requested_at`. Equality is accepted; one microsecond earlier
   is rejected. UTC timestamps are explicit caller values; no system clock or
   upper freshness bound absent from Python is invented.
4. Only after all checks pass, sets `write_request_durable = true` and publishes
   a private, non-Clone `DurableWriteRequest` containing the request, matching
   record and receipt for the later R9 consumer.

Receipt mismatch/non-durable/predating evidence returns
`WRITE_REQUEST_EVIDENCE_INVALID`. Storage-port failures are opaque codeless errors
with static safe messages/paths and no protected source chain. Invalid receipt
objects cannot cross the typed Rust port; model construction rejects malformed
hash/reference/UTC/Bool representations instead. Metadata/value limit failures
retain the R8-S01 codeless typed snapshot error boundary.

On failure, no new handoff is minted and the checkpoint flag is preserved,
including a previously true flag. That old flag never bypasses current receipt
validation. Dropping a future before polling, before acceptance or after storage
acceptance but before acknowledgement cannot produce a handoff or provider call.
Storage may nevertheless already contain the record; error/drop never implies
rollback, commit state or permission to repeat a business WRITE. No automatic
retry, timeout-to-commit inference or new provider/commit model is introduced.

Production provider orchestration remains R9-S01. This Slice implements the
required durable handoff; its test-only spy **consumes that handoff** and records
`WRITE_REQUESTED → PROVIDER_WRITE` only after validated storage and flag update.
The full combined pre-invocation certification remains R8-S05. Current authority,
trust, approval and timing still belong to their own live orchestration checks;
the durable handoff proves the completed persistence stage, not those proofs.

### R8-S04 verification and traceability

| Verification ID | Evidence |
| --- | --- |
| `R8-S04-MODELS-ORACLE` | 173 observations: exact call/record/receipt model projection, all scalar/hash/key admissions, nonhex/Unicode key suffixes, blank/control/credential rejection, correctly formatted forged payload hash rejection, argument-name admission, and ordered trace lists at 0/1/63/64/65. |
| `R8-S04-BUILD-ORACLE` | 29 CREATED assembly observations: exact identities/envelopes/precondition/trace output; context, tool/capability, every reservation/precondition binding and atomicity rejection; invalid auxiliary IDs/traces. |
| `R8-S04-RECORD-ORACLE` | 24 exact canonical-byte/SHA-256 observations: all 13 changed fields, UTC min/max/-1/0/+1 microsecond, Unicode/control escaping, deterministic repeats and omitted-field invariance. |
| `R8-S04-DURABILITY-ORACLE` | 17 original execution observations: storage before mock provider, receipt timestamp -1/0/+1 microsecond, wrong hash/non-durable/storage failure, both initial durable-flag states, blocked/requires-approval and foreign execution/tenant/business contexts. |
| `R8-S04-DISPOSITION-EXCEPTIONS` | Five original Oracle request/invocation observations preserved: CREATED cases match; user-approved CONFLICT/REUSED results have zero new storage/provider calls and no durable handoff. |
| `R8-S04-FAULT-BOUNDARIES` | Unpolled/pending/dropped futures before/after adapter acceptance, storage errors before/after acceptance, no rollback/commit inference, old checkpoint flag isolation, immutable snapshots and safe Debug/errors. |
| `R8-S04-SNAPSHOT-BOUNDARIES` | Existing canonical bytes/depth/nodes and snapshot metadata depth/nodes/text at 0/1/MAX-1/MAX/MAX+1; borrowed constructor/builder rejection of 8,192-level TypeRef metadata on a 512 KiB worker stack, with safe owner lifetime/drop on a larger stack. |
| `R8-S04-DETERMINISM` | Five files regenerated from independent frozen `git archive` exports: exact bytes/SHA-256 match; explicit LF attributes preserve cross-platform fixture bytes. |

| Rust SRS | R8-S04 status and scope |
| --- | --- |
| `NSL-RUST-WRITE-009` | IMPLEMENTED/VERIFIED at the durable evidence/handoff boundary; `R8-S04-DURABILITY-ORACLE`, `R8-S04-FAULT-BOUNDARIES`. Production storage durability is a host-adapter obligation. |
| `NSL-RUST-WRITE-010` | IMPLEMENTED for the immutable request's idempotency/payload/approval/intent/precondition bindings; actual provider port integration remains R9-S01. `R8-S04-MODELS-ORACLE`, `R8-S04-BUILD-ORACLE`. |
| `NSL-RUST-WRITE-002/003/008` | Preserved component bindings, payload verification and fail-closed eligibility/evidence checks. Guard policy/authority/TTL semantics remain R8-S03; combined certification remains R8-S05. |
| `NSL-RUST-HASH-006/007/008` | IMPLEMENTED/VERIFIED for the externally relevant original requested-record bytes, SHA-256/domain/field projection; `R8-S04-RECORD-ORACLE`. |
| `NSL-RUST-IDEM-005`, `NSL-RUST-DIFF-010` | Approved CONFLICT rejection and explicit REUSED/PENDING routing supplement existing idempotency. Ordinary observations remain unchanged; new workflow differences are separately retained and verified. |
| `NSL-RUST-WRITE-011`, `NSL-RUST-COMMIT-*` | No completed full-pipeline/commit-state claim: provider invocation and commit/uncertainty semantics remain R9; full R8 pre-invocation gate remains R8-S05. |

Generate original observations with Python 3.12 and an unmodified fixed archive:

```bash
for mode in models build records durability dispositions; do
  .venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_requested.py "$oracle_dir" "$mode"
done
```

| Fixture | SHA-256 |
| --- | --- |
| `write-requested-models-oracle.tsv` | `1b458d2cb2d3e5a461b1be9e9a3bc93aaf10166d25657b4da601215f88a7d076` |
| `write-requested-build-oracle.tsv` | `455b2ac88e29f265d5c8185eac61a8e86c304fc9c59aab060633d973c6097934` |
| `write-requested-records-oracle.tsv` | `8c71c151d51f62fd1df4c1b1241733c690b173cbf051104b22c382440300403d` |
| `write-requested-durability-oracle.tsv` | `9c26d66417bdbb954fe7e51efff55793b0288dacf677416c303cffa303d1d787` |
| `write-requested-dispositions-oracle.tsv` | `e7ae9696baf3a6dd9225da641137bb36ca070a57720f915c84eca6b721e3030a` |


Pinned **Rust 1.99.1** passes fmt, locked workspace check, architecture guard and
**8 guard tests**, all-target/all-feature Clippy with warnings denied, **272 Rust
tests** (263 baseline + 9 new requested-evidence tests), and frozen workspace
build. Final forged-payload observations also pass the complete Rust regression.
The mandatory Python quality command ran **exactly once locally for R8-S04**:
**2,391 passed**, statement **99.28%**, branch **98.11%**, with unchanged
traceability/readiness, architecture, performance and certification checks.
Existing Python v0.1 CONDITIONAL findings remain unchanged.

Pinned **nightly-2026-11-15** LLVM instrumentation uses the unchanged workspace
`crates/**/src/` executable-line aggregation for the Rust statement figure:
**99.14%** (14,376/14,500); branch **98.46%** (2,046/2,078). New requested-evidence
modules cover all **401/401 executable lines and 52/52 instrumented branches**.
No source/branch exclusions were added; embedded source-unit-test aggregation is
unchanged. Rust and Python figures are independent measurements.

## R8-S05 — Admitted Skill / declared contract binding

`WriteGuardRequest` now requires `skill: &SkillObject`, borrowed from the current
schema/integrity-admitted Runtime context. Both Guard entry points compare its
semantic hash with the intent, require a unique matching `tool_ref` declaration,
and compare declared tool/version/hash/capability/risk/scope against the current
Tool Contract before contacting policy or opening later boundaries. New rejection
reasons are `WRITE_SKILL_BINDING_MISMATCH` and
`WRITE_DECLARED_CONTRACT_MISMATCH`. No optional/unbound Guard path is retained.

The user approved this extra native check after the combined gate showed that
both Oracle and the earlier Guard accepted self-consistent new contract evidence
while retaining an old approved Skill/intent. A timeout change alone changes
contract identity. Recompile/new-intent plus old approval is rejected; matching
new approval is required. Original Oracle observations and all prior fixture
bytes remain unchanged. Existing S03 isolated-predicate test helpers supply an
explicit abstract unit declaration projection; the new full component gate uses
strictly decoded genuine Oracle NSO and real `WriteIntentBuilder` output.

Caller admission remains explicit: a raw caller-created Skill/cached semantic
hash is not an authenticated artifact. The Runtime/NSO boundary verifies the model;
Guard consumes that immutable admitted model and binds its declaration. Current
principal/scope checks, proof/TTL, host port authority and durable storage contracts
retain their existing responsibilities. No Runtime reverse dependency is added.

The R8 exit gate is in `nsl-integration` with development-only workspace edges.
It composes Runtime CHECK/approval components with all R8 stages and a test-only
consumer of the private durable handoff. Its **54 ordinary scenarios** compare
exact CHECK/validation/approval/decision, record hash, flags, counts and order;
**5 original Oracle exception scenarios** explicitly retain approved native
rejections/reuse routing. The inventory binds **19 previous R8 files**, four new
artifacts and the R7 manifest. See [gate scope and traceability](../nsl-integration/README.md).

## R9-S01 — Commit-aware provider invocation

`WriteExecutionPort` is an injected `Send + Sync` host adapter returning a boxed
`Send` future. Its request is the immutable R8 `WriteCallRequest`; its response is
`WriteExecutionOutcome::Committed`, `NotCommitted`, or `Unknown`. There is no
Tokio type, timer, network/database implementation, background task or retry in
this semantic API. The production dependency graph and lock are unchanged.

`WriteInvocationCoordinator::invoke` **consumes `DurableWriteRequest`**. Only R8's
matching current durable acknowledgement can construct that non-Clone handoff.
Polling invocation makes one awaited provider call. Raw call requests, receipt
objects and checkpoint booleans cannot be passed in its place. Guard/context or
storage/receipt failures cannot reach the port, even with an old durable flag.
The positive route verifies the same pipeline is connected. Provider requests
preserve every argument envelope and all idempotency/payload/approval/intent/
precondition/trace bindings from the Oracle.

`WriteInvocation` retains the request, requested record/receipt, and observed
outcome. It is not Clone and exposes no method to recover the consumed handoff.
Its outcome is a **provider observation**, not a persisted final business result.
As in the Oracle's split invocation/outcome boundaries, a well-formed foreign
outcome identity remains observable here; exact request/outcome identity admission
and durable outcome recording belong to **R9-S03**. COMMITTED must still pass
later outcome persistence and post-WRITE verification (**R9-S04/S05**). This Slice
does not mark those requirements complete or expose a final RuntimeResult.

The result models preserve the Oracle's nine outcome identity fields, full result
ValueEnvelope, business reference, optional transaction ID, UTC commit timestamp,
reason/message and evidence references. The enum's COMMITTED case directly owns
the Oracle `WriteCommitted.result` envelope. Constructors validate nonempty,
credential-free identity/scalar/evidence text, SHA-256 hashes and the existing
IdempotencyKey prefix/length rule. The owned result envelope moves without cloning
or encoding its recursive value/type graph; inbound decoding/resource budgets
remain the injected host adapter's responsibility. It adds no implicit result
type coercion or classification change. Public Debug output omits business data.

Trusted adapter errors map to UNKNOWN_COMMIT_STATE with the request's identity
and trace references, no transaction ID, and the exact frozen generic message:
`provider outcome is uncertain; reconciliation is required`.

| Adapter termination | Outcome reason |
| --- | --- |
| Cancelled | `PROVIDER_CANCELLED` |
| Timeout | `PROVIDER_TIMEOUT` |
| ConnectionLost | `PROVIDER_CONNECTION_LOST` |
| Exception | `PROVIDER_EXCEPTION` |
| MalformedAcknowledgement | `PROVIDER_ACKNOWLEDGEMENT_MALFORMED` |

An explicit provider NOT_COMMITTED result remains distinct from uncertainty.
Protected transport exception details stay in the host adapter. Rust makes an
arbitrary Python object acknowledgement unrepresentable as a successful response;
the decoder reports MalformedAcknowledgement instead. No timeout is interpreted
as rollback. Dropping an unpolled invocation calls no provider; polling a pending
invocation twice calls it once. Dropping after polling retains no rollback claim,
reusable permit or automatic retry. Full asynchronous after-mutation cancellation,
timeout/connection fault injection and caller reconciliation semantics are the
next **R9-S02** Slice, not a completion claim of these adapter-classifier tests.

### R9-S01 evidence and traceability

Frozen Oracle **28a949ec96581cbdc52c8133527b23bb5b72ee0a** generates **155 model
boundary observations** and **9 invocation observations**. Models cover blank,
Python control whitespace, credential-bearing text, Unicode, 0/1/63/64/65 hash/key
lengths, optional metadata and 0/1/63/64/65 evidence collections. Evidence refs have
no invented 64-item limit. Native typed fields exclude wrong Python runtime
classes and naive/non-UTC datetime values at construction. Invocation compares
exact outcome fields/state, the complete request, durable flag, requested record
hash, call count and persistence-before-provider order. It includes all three
explicit outcomes, foreign-identity observation and all five uncertainty reasons.

Two independent unmodified `git archive` exports reproduced both files byte for
byte and with identical SHA-256. Original Python/Oracle lock and all earlier R8
fixtures remain unchanged; LF attributes preserve the new TSV bytes on Windows.

```bash
.venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_invocation.py "$oracle_dir" models
.venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_invocation.py "$oracle_dir" invocation
```

| Artifact | SHA-256 |
| --- | --- |
| `write-invocation-models-oracle.tsv` | `0700804d0a5608c2e4d147f53e2d447a642bf95f4f8f6ff78a00dcb060b58ea6` |
| `write-invocation-invocation-oracle.tsv` | `718d23c5d2798a202a9777938e2c2ebb34c73da1d157d5ba15ebb22d1ecb946f` |

| Requirement | R9-S01 status / verification |
| --- | --- |
| `NSL-RUST-COMMIT-001/005` | IMPLEMENTED/VERIFIED for explicit provider outcome models and state distinctions. `R9-S01-MODELS`, `R9-S01-INVOCATION`. |
| `NSL-RUST-WRITE-009/010` | VERIFIED durable handoff and unchanged complete provider request at invocation. `R9-S01-INVOCATION`, `R9-S01-FAIL-CLOSED`. |
| `NSL-RUST-WRITE-011` | VERIFIED component boundary: provider coordinator requires the private R8 handoff. Complete controlled execution remains R9-S05. |
| `NSL-RUST-TOOL-005/006`, `NSL-RUST-WRITE-012`, `NSL-RUST-ASYNC-002/004/005` | IMPLEMENTED/VERIFIED injected async port without infrastructure or reverse Runtime dependency. Architecture guard plus source inspection. |
| `NSL-RUST-TOOL-009`, `NSL-RUST-COMMIT-002/003/004`, `NSL-RUST-ASYNC-003` | Classifier/no-retry component evidence only. Full asynchronous fault/reconciliation gate remains R9-S02. `R9-S01-INVOCATION`, `R9-S01-DROP`. |
| `NSL-RUST-COMMIT-006`, durable outcome/verification and final business result | Remain later R9 Slices; COMMITTED observation cannot certify their completion. |

Five new Rust tests certify this Slice. Rust **1.99.1** passes fmt, locked metadata
and workspace check, architecture guard plus **8 tests**, all-target/all-feature
Clippy with warnings denied, **282 Rust tests**, and frozen workspace build.
Cargo.lock SHA-256 remains
`5dfa85682c9050eb3eda9fb72946ab36134367e4af675b9575f36947e24e2314`.
The mandatory Python quality command ran **exactly once locally for R9-S01**:
**2,391 passed**, statement **99.28%**, branch **98.11%**, including existing
traceability/readiness, architecture, performance and certification checks.
Existing v0.1 CONDITIONAL findings are unchanged.

Pinned **nightly-2026-11-15** LLVM instrumentation retains the existing workspace
`crates/**/src/` executable-line aggregation for the Rust statement figure:
**99.15%** (14,538/14,662); branch **98.47%** (2,066/2,098). New execution modules
cover **142/142 executable lines and 2/2 instrumented branches**. No exclusions
were added; embedded source-unit-test aggregation is unchanged. Rust and Python
are independent measurements.

## R9-S02 — Cooperative cancellation and unknown-commit faults

The user approved extending the R9-S01 WRITE port after a concrete probe showed
that dropping a pending Rust invocation after a possible mutation produced no
async cleanup, outcome or caller recovery advice. The frozen Python Oracle
instead delivers cancellation, awaits cleanup, and can observe either a final
result or a cleanup exception. Oracle **28a949e** remains unchanged.

`WriteExecutionPort::execute` and `WriteInvocationCoordinator::invoke` now both
require an invocation-local `WriteCancellationSignal`. Each call gets a fresh
signal; the host retains a clone to request cancellation and continues awaiting
invocation. The signal is sticky and wakes both current and later subscribers.
Repeated notification never performs a second provider call or aborts cleanup.
Concurrent invocations with separate signals keep their cancellation, mutation,
cleanup and call-count state isolated. Existing S01 port implementations/tests
were migrated to the required parameter; their original Oracle bytes are intact.

`invoke_with_timeout(durable, timeout_ms, cancellation)` uses the host's time
driver and the same private durable handoff. A positive deadline polls completion
before expiry; on expiry it requests cancellation and **awaits the same provider
future without a second cleanup deadline**. It never drops that future to infer
a commit state. Consistent with `asyncio.wait_for`, nonpositive budgets return
the conservative timeout observation without invoking the provider. The corpus
also checks 1 ms, `i64::MAX-1` and `i64::MAX` immediate-completion controls.
`invoke` itself needs no time driver. Neither method spawns or retries a WRITE.

| Observed completion | Native outcome / recovery |
| --- | --- |
| Explicit COMMITTED, including cancellation suppression | Preserve the complete result envelope and metadata; no reconciliation directive. |
| Explicit NOT_COMMITTED, including cancellation suppression | Preserve the provider's definite rejection; no reconciliation directive. |
| Explicit UNKNOWN | Preserve the provider's unknown outcome; reconciliation required. |
| Cancelled without a definite result | UNKNOWN / `PROVIDER_CANCELLED`; reconciliation required. |
| Deadline expires, then provider terminates Cancelled | UNKNOWN / `PROVIDER_TIMEOUT`; reconciliation required. |
| Connection loss or provider exception after possible mutation | UNKNOWN / `PROVIDER_CONNECTION_LOST` or `PROVIDER_EXCEPTION`; reconciliation required. |
| Exception raised during cancellation cleanup | UNKNOWN / `PROVIDER_EXCEPTION`; retain this error instead of overwriting it with timeout/cancellation. |
| Malformed acknowledgement | UNKNOWN / `PROVIDER_ACKNOWLEDGEMENT_MALFORMED`; reconciliation required. |

`WriteInvocation::provider_error()` retains the typed termination; for deadline
cancellation this is the actual provider's Cancelled error while the outcome
reason is TIMEOUT. `deadline_elapsed()` records the separate deadline observation.
Successful suppression keeps all returned values, transaction/reference data and
evidence refs. Public Debug output remains data-safe, and protected raw transport
exception details stay in the trusted host adapter.

Caller advice is invariant: `automatic_retry_allowed()` is **false** for every
observation. Only an unknown outcome has `reconciliation_required() == true` and
`recovery_status() == Some(RECONCILIATION_REQUIRED)`. Definite observations return
no provisional terminal status: exact outcome identity admission, durable outcome
evidence and post-WRITE verification still belong to **R9-S03/S04/S05**. This
Slice does not promote a COMMITTED observation to final Runtime success.

This is a cooperative in-process boundary. The caller must request cancellation
and await completion. Unstructured task abort/future drop, process crash or a
noncooperative provider cannot synchronously manufacture an observed result;
the durable requested record remains the host's recovery evidence. The old S01
drop regression remains as evidence of that limit. A timer expiring alone never
proves business rollback or authorizes retry.

### R9-S02 differential and fault evidence

`write-faults-oracle.tsv` contains **81 original Oracle observations** covering
timeout, connection loss, cancellation, malformed acknowledgement, provider
exception, suppression to each explicit outcome, and cleanup exceptions. Faults
occur both before and after a synthetic possible mutation; cleanup suspends for
0/1/3 scheduler turns. Two independent unmodified frozen archives reproduce the
fixture byte-for-byte, with SHA-256:

```text
ebc08434e57f1cc37cc03d0b68e1732127d16392638a12d3dbd05cea30215817
```

The generator drives the genuine Oracle `CommitAwareWriteExecutor`, including its
in-memory outcome evidence port. It records original final statuses and outcome
record counts separately. The native comparison selects the invocation/recovery
projection: full outcome fields, durable flag, possible mutation and cleanup
counts, persistence/provider/cleanup order, recovery status and retry permission.
It does not claim that native R9-S03 outcome persistence exists already. Every
entered provider is called once; automatic business retries are **zero**. The two
nonpositive timeout cases enter no provider and also have zero retries.

Native fault tests run with a paused Tokio clock, without real business I/O.
An additional barrier holds cleanup for **60 virtual seconds** after cancellation
or deadline expiry. Invocation remains pending until that barrier opens, then
preserves the committed result or cleanup exception. Separate tests exercise
0/1/3 existing signal waiters, late subscribers, repeated notification and two
concurrent invocations. S01 test helper extraction ran its five regressions before
and after the behavior-preserving move; no baseline expectation changed.

```bash
.venv/bin/python -I -B rust/crates/nsl-write/tests/fixtures/generate_write_faults.py "$oracle_dir"
```

| Requirement | R9-S02 status / verification |
| --- | --- |
| `NSL-RUST-COMMIT-002/003/004/005` | IMPLEMENTED/VERIFIED at the invocation/recovery boundary: unknown remains distinct, automatic business retry zero, reconciliation required. `R9-S02-ORACLE`, `R9-S02-CLEANUP`. |
| `NSL-RUST-TOOL-009`, `NSL-RUST-ASYNC-003`, `NSL-RUST-SEC-009`, `NSL-RUST-PERF-008` | VERIFIED asynchronous after-mutation fault classification and preserved suppression/cleanup outcomes. `R9-S02-ORACLE`, `R9-S02-CLEANUP`. |
| `NSL-RUST-WRITE-009/010/011`, `NSL-RUST-ASYNC-002/004/005` | VERIFIED existing durable handoff plus injected, executor-neutral public port/signals; architecture guard and migrated S01 regressions. |
| `NSL-RUST-DIFF-008/010` | VERIFIED scoped outcome and recovery projection over 81 untouched Oracle observations. `R9-S02-ORACLE`. |
| `R9-S02-ISOLATION`, `R9-S02-SIGNAL` | VERIFIED invocation-local control, sticky multiple/late subscribers and one call per entered provider. |
| Durable outcome identity/evidence, verification, full controlled RuntimeResult | Remain R9-S03/S04/S05; no new completion claim. |

The production dependency update reuses reviewed Tokio **=1.54.1** with existing
features; all 17 external package/version/checksum records are unchanged. See
[dependency evidence](../../DEPENDENCIES.md). Frozen Python implementation,
Oracle lock, requirement fingerprints and earlier fixture bytes remain unchanged.

Rust **1.99.1** passes fmt, locked metadata/workspace check, architecture guard
and **8 guard tests**, all-target/all-feature Clippy with warnings denied,
**286 Rust tests** (282 baseline plus four R9-S02 tests), and frozen workspace
build with a stable lock digest. The mandatory Python quality command ran
**exactly once locally for R9-S02**: **2,391 passed**, statement **99.28%**, branch
**98.11%**, including existing traceability/readiness, architecture, performance
and certification checks. Existing v0.1 CONDITIONAL findings are unchanged.

Pinned **nightly-2026-11-15** LLVM instrumentation retains the existing workspace
`crates/**/src/` executable-line aggregation for the Rust statement figure:
**99.16%** (14,617/14,741); branch **98.48%** (2,076/2,108). The execution modules
cover **221/221 executable lines and 12/12 instrumented branches**. No source or
branch exclusions were added; embedded source-unit-test aggregation is unchanged.
Rust and Python are independent measurements.
