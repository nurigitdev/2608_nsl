# NeX Skill Language v0.2.1 Cross-Platform Artifact Hash Stability

## 1. Scope

This Slice 0073 correction applies to the post-v0.2 P8 example artifact sets.
It does not change the NSL language, Compiler, Runtime, IR, package, or
signature semantics certified by the `v0.2` tag.

## 2. Failure Evidence

GitHub Actions run 44 for commit `a5a0f5f` completed both the Windows and
Linux quality gates successfully. Later P8 commits introduced exact-source
artifact manifests. In run 52, Linux passed while Windows failed three
manifest checks:

- Project READ Tool catalog digest
- Project WRITE Tool catalog digest
- Voucher READ Tool catalog digest

The tests intentionally hash `Path.read_bytes()`. The Git index stored LF,
while a Windows checkout with `core.autocrlf=true` produced CRLF working-tree
bytes because the repository had no `.gitattributes` policy. The resulting
SHA-256 values therefore differed even though parsed JSON and compiled NSL
semantics were unchanged.

## 3. Architecture Decision

Artifact manifests bind exact released source bytes. Tests must not hide a
byte change by normalizing content immediately before hashing. Repository
checkout policy owns the physical source representation:

```gitattributes
*.json text eol=lf
*.ns text eol=lf
```

This preserves tamper-sensitive raw-byte evidence while making those bytes
deterministic across Windows and Linux.

## 4. Verification

- The root `.gitattributes` policy is regression tested.
- Every Tool catalog and NSL source referenced by the three P8 manifests is
  non-empty, contains no carriage return byte, and ends with LF.
- Existing manifest SHA-256 checks continue to use raw bytes.
- The authoritative full quality gate runs after the correction.

## 5. Traceability

This correction reinforces `CHG-031`, Artifact and Package Hash Ownership.
It does not change a v0.1 or v0.2 SRS Requirement statement, implementation
status, Slice assignment, or verification claim, so no traceability baseline
row or fingerprint is rebaselined.

Historical failed CI evidence remains immutable. A successful Windows and
Linux run for the corrected commit supersedes it as the current verification
evidence.

## 6. Local Windows Verification Result

| Verification | Result |
|---|---|
| Focused P8 and line-ending regression | `24 passed` |
| Authoritative full regression | `2,333 passed`, `0 failed` |
| Statement coverage | `99.30%` |
| Branch coverage | `98.16%` |
| Architecture certification | `CERTIFIED`, `0` violations |
| v0.2 SRS certification | `CERTIFIED`, `123/123 IMPLEMENTED` |

The corresponding Linux result is produced by the `ubuntu-latest` quality
job after this correction is pushed. It is not inferred from the local
Windows execution.
