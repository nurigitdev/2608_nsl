# NeX Skill Language v0.2 Cryptography 46 Compatibility

**Status:** IMPLEMENTED

**Release Slice:** 0071

**Integration baseline:** `cryptography 46.0.7`

## 1. Purpose

Slice 0071 aligns the NSL v0.2 wheel with the cryptography version already
validated by the NeX Platform NSL v0.1 integration. The previously built v0.2
wheel was not distributed, so the package version remains `0.2.0` and the
replacement artifact supersedes the local Slice 0070 build.

## 2. Structural Review

Cryptographic provider access remains isolated to `nsl.adapters.ed25519`.
Core package building, signature policy, trust evaluation, Runtime, Compiler,
IR, Audit, and Replay modules do not import `cryptography`. The adapter uses
the stable Ed25519 raw-key, signing, verification, and serialization APIs that
are present in cryptography 46.0.7. No production source refactor or semantic
change is required.

## 3. Compatibility Contract

| Item | Contract |
|---|---|
| NeX Platform baseline | `cryptography==46.0.7` |
| NSL wheel requirement | `cryptography>=46.0.7,<47.0.0` |
| Python | `>=3.12` |
| NSL package version | `0.2.0` |
| Provider boundary | `nsl.adapters.ed25519` only |

The exact development and CI version prevents unrecorded provider drift. The
wheel range permits compatible 46.x patch updates while rejecting versions
below the tested baseline and all 47.x or later releases.

## 4. Verification

The Slice 0071 verification set covers:

- exact installed dependency and project metadata consistency;
- Ed25519 sign/verify and tamper rejection on cryptography 46.0.7;
- invalid signature-size boundary and robustness cases;
- wheel dependency lower and upper boundary rejection;
- the complete regression, statement, and branch coverage gate;
- deterministic wheel rebuild and clean installation smoke testing.

Primary evidence:

- `tests/test_cryptography_compatibility.py`
- `tests/test_package_verification_and_signature.py`
- `tests/test_architecture.py`
- `tests/test_wheel_release.py`
- `tools/wheel_release.py`
- `.github/workflows/nsl-quality.yml`

## 5. Traceability Assessment

This Slice changes deployment compatibility metadata, not NSL Source, IR,
Runtime, Approval, Write, Trust, Audit, or Replay semantics. Existing SRS
Requirement statuses and Slice ownership therefore remain unchanged. The new
tests provide supplemental compatibility evidence for the existing Ed25519
adapter and signed-package trust requirements.

## 6. Verification Result

| Verification | Result |
|---|---|
| Focused compatibility regression | `153 passed` |
| Full regression | `2,307 passed`, `0 failed` |
| Statement coverage | `99.30%` |
| Branch coverage | `98.16%` |
| Architecture certification | `CERTIFIED`, `0` violations |
| v0.2 SRS certification | `123/123 IMPLEMENTED`, `CERTIFIED` |
| Clean wheel environment | `pip check` reports no broken requirements |
| Installed dependency | `cryptography 46.0.7` |
| Installed Ed25519 smoke test | Passed |
| Reproducible wheel builds | Byte-identical SHA-256 |

Replacement artifact:

- Wheel: `nex_skill_language-0.2.0-py3-none-any.whl`
- Size: `194346` bytes
- SHA-256: `d23b6735a3c27c00afd189d1e3a20cd00758dc2d732caf9285653bfeb44d218a`
- Runtime dependency: `cryptography<47.0.0,>=46.0.7`
