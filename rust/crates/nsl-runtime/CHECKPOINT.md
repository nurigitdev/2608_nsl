# R7-S02 checkpoint contract

The fixed reference is Python `28a949ec96581cbdc52c8133527b23bb5b72ee0a`.
Checkpoint schema and state schema are integer **1**. A checkpoint consists of
execution/business identity, positive arbitrary-precision checkpoint version,
skill ID/version/semantic hash, IR version, runtime version, exact opaque state
bytes and an integrity digest. Canonical outer JSON uses base64 state bytes.
SHA-256 covers every outer field except `integrity_hash`, including the constant
integer schema version and base64 of the exact state bytes. This digest detects
corruption; it is not a signature or authorization proof.

State contains exact security context, inputs/contexts, paired variable/check
frames, CHECKs, output rows, resource usage, completeness/current-node/invocation/
FOREACH state, approval records, write journal, idempotency maps and checkpoint
progress/approval/payload/intent mappings. Capture increments the version only
after successful encoding/validation. Restore checks integrity, every outer
binding and exact current principal/policy, then validates all state before
publishing a new ExecutionContext. Restored resource usage is installed into the
same guard; the injected monotonic clock starts once for the restored segment,
as in the reference. No elapsed wall time or duration restart policy is invented.

Callers supply `CheckpointLimits`: separate outer-document and decoded-state
byte/depth/node limits, plus the per-token Decimal expansion budget. Both decode
and encode are bounded; base64 length is checked before allocating decoded state.
Failures carry a safe path and typed kind, have no NSL diagnostic code and expose
no rejected payload, credential or underlying source chain. Context/checkpoint
Debug output omits state bytes. No store, human-approval coordinator, resume claim
or business provider invocation is added in this Slice.

User-approved strict differences retain original observations separately:

* schemas/version counters require JSON integers; bool/float aliases are rejected;
* envelopes require recursive Core type conformance, supported finite values and
  credential-free values/metadata; floats/NaN/Infinity are rejected;
* restored FOREACH depth must fit the existing 0..=16 bound;
* caller-specified resource limits reject oversized/deep/excess-node documents.

Native snapshots must pass the same validation before capture. Expected safe
canonical state bytes/outer bytes/digests and successful recapture after restore
must match the fixed Python reference. Typed state schema validation also rejects
wrong container/scalar kinds rather than coercing malformed CHECK/provenance data.
Detailed vectors, hashes, traceability and gate results are recorded in README.md.
