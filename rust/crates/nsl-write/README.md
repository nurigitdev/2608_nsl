# nsl-write — R8-S01 Canonical Write Intent

`WriteIntent` owns an immutable snapshot of evaluated argument envelopes and the
Oracle's tenant/business/node/tool/approval identities. It validates both supplied
hashes before publishing a model. `WriteIntentBuilder` resolves exactly one
matching declared IR tool, requires exactly WRITE capability, and checks argument
names/cardinality against the WRITE expression. Missing business identity,
unknown/duplicate/read/transaction tool declarations, missing/extra/wrong argument
names and a non-WRITE expression are rejected. The context is a borrowed Runtime
projection (`tenant_id`, optional business ID and admitted `SkillObject`); this
crate depends on Core/IR and the existing SHA-256 package, without a Runtime edge.

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
