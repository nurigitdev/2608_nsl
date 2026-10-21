# NeX Skill Language v0.2 Wheel Release

**Status:** IMPLEMENTED

**Release Slice:** 0070

**Distribution:** `nex-skill-language`

**Version:** `0.2.0`

## 1. Purpose

Slice 0070 packages the certified NSL v0.2 compiler, runtime, controlled-write
contracts, and framework-neutral NeX integration API as an installable Python
wheel. It does not change NSL Source, IR, Runtime, Approval, Write, Audit, or
Replay semantics and therefore does not rebaseline an SRS Requirement ID.

## 2. Release Contract

| Item | Contract |
|---|---|
| Wheel | `nex_skill_language-0.2.0-py3-none-any.whl` |
| Python | `>=3.12` |
| Runtime dependency | `cryptography>=49.0.0,<50.0.0` |
| Console entry point | `nsl = nsl.cli:main` |
| NeX-Platform API | `nsl.api` |
| Version API | `nsl.__version__`, `nsl.api.__version__` |
| Integrity | SHA-256 sidecar generated after wheel verification |

Only the `nsl` package and its wheel metadata are permitted in the artifact.
Tests, documentation, examples, requirements, bytecode, credentials, and
customer bindings must not be present.

## 3. Build and Verification

```powershell
.\.venv\Scripts\python.exe tools\run_quality.py
.\.venv\Scripts\python.exe tools\wheel_release.py
```

`wheel_release.py` uses the local declared build toolchain, verifies the wheel
name and metadata, checks the complete `RECORD`, rejects unsafe or unexpected
archive content, verifies the CLI entry point, and writes a SHA-256 sidecar. A
fixed ZIP build epoch makes repeated builds of unchanged Source byte-identical.

The resulting wheel must then be installed into a clean Python 3.12 environment
outside the source tree. The release smoke test verifies package provenance,
the public version, `nsl.api` import, CLI entry point, dependency consistency,
and a v0.2 compile/check path.

## 4. NeX-Platform Consumption

NeX-Platform installs the wheel from the approved internal artifact repository
and integrates through `nsl.api`. Framework, queue, storage, credential, tenant,
and customer endpoint bindings remain owned by NeX-Platform and are not bundled
in the NSL wheel.

```powershell
python -m pip install nex_skill_language-0.2.0-py3-none-any.whl
python -c "import nsl.api; print(nsl.api.__version__)"
```

Production deployment must verify the published SHA-256 digest and pin the
exact `0.2.0` artifact. Public PyPI publication is outside this release scope.
