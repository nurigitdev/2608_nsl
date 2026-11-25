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
