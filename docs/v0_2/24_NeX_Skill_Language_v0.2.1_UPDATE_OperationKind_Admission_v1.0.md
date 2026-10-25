# NeX Skill Language v0.2.1 UPDATE OperationKind Admission

**Status:** IMPLEMENTED

**Release Slice:** P8-S99A

**Distribution:** `nex-skill-language`

**Version:** `0.2.1`

## 1. Decision

NSL v0.2.1 adds `OperationKind.UPDATE` to the bounded WRITE Tool Contract
model. Existing `CREATE` and `CREATE_DRAFT` values and their serialized forms
remain unchanged.

`UPDATE` is used for governed mutation of an existing Business Object or its
relationships. It does not weaken the controlled WRITE requirements: atomic
execution, idempotency, explicit commit confirmation, approval, preconditions,
post-write verification, and evidence remain mandatory.

## 2. Compatibility

- Package `0.2.0` and its wheel digest remain an immutable historical release.
- Package `0.2.1` is a backward-compatible contract extension.
- Unknown operation kinds, including `DELETE`, remain fail-closed.
- NeX Platform must admit the exact `0.2.1` wheel by source revision, source
  tree, wheel digest, dependency set, and public API before runtime activation.

## 3. Project Operation Mapping

| Canonical operation | NSL OperationKind |
|---|---|
| `PROJECT.PROJECT.CREATE` | `CREATE` |
| `PROJECT.PROJECT.DEACTIVATE` | `UPDATE` |
| `PROJECT.MEMBERSHIP.ADD` | `CREATE` |
| `PROJECT.MEMBERSHIP.REMOVE` | `UPDATE` |

## 4. Acceptance

The release is admissible only when:

1. `OperationKind.UPDATE` round-trips through Tool Contract parsing and
   serialization.
2. Existing CREATE contracts remain byte-for-byte semantically stable.
3. Unknown operation kinds are rejected.
4. The authoritative NSL quality gate passes at the frozen coverage floors.
5. The reproducible `0.2.1` wheel is inspected and receives a SHA-256 sidecar.
6. NeX Platform records a separate `0.2.1` admission lock without modifying
   the historical `0.2.0` lock.
