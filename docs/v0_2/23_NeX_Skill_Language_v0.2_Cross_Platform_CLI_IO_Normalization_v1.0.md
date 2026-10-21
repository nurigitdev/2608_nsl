# NeX Skill Language v0.2 Cross-Platform CLI I/O Normalization

**Status:** IMPLEMENTED

**Release Slice:** 0072

## 1. Purpose

Slice 0072 removes an operating-system difference in CLI file error output.
Writing a compiled artifact to a directory raises `PermissionError` on Windows
and `IsADirectoryError` on Linux. Both conditions represent an invalid file
operation, but the prior normalization produced different public messages.

## 2. Stable Contract

| Condition | CLI message |
|---|---|
| Missing path | `file not found` |
| Directory used as a file path | `file operation failed` |
| Actual permission denial | `permission denied` |
| Other protected `OSError` | `file operation failed` |

Raw exception text, local paths, and platform-specific error descriptions are
not exposed through the CLI JSON error contract.

## 3. Implementation Boundary

The change is limited to CLI `OSError` normalization. Compiler, Runtime, IR,
Tool, Approval, Write, Audit, Replay, and cryptographic behavior are unchanged.
`NSL-CLI-003` retains Slice 0026 ownership and gains regression evidence for
the compile-output I/O failure path.

## 4. Verification

- Direct `IsADirectoryError` normalization models Linux.
- `PermissionError` carrying a directory filename models Windows.
- A non-directory `PermissionError` remains `permission denied`.
- The real `nsl compile -o <directory.nso>` path must return
  `CLI_IO_ERROR` with `file operation failed`.
- Windows and Ubuntu CI both run the authoritative full quality gate.

## 5. Verification Result

| Verification | Result |
|---|---|
| Focused CLI, traceability, and workflow regression | `102 passed` |
| Windows full regression | `2,307 passed`, `0 failed` |
| Statement coverage | `99.30%` |
| Branch coverage | `98.16%` |
| Architecture certification | `CERTIFIED`, `0` violations |
| Reproducible wheel build | Byte-identical SHA-256 |
| Clean wheel dependency check | No broken requirements |
| Installed CLI directory-output smoke test | `CLI_IO_ERROR`, return code `3` |

The repository-local execution environment is Windows. Native Linux full-gate
verification is owned by the new `ubuntu-latest` CI job and is required on
every push and pull request to `master`.

Replacement artifact:

- Wheel: `nex_skill_language-0.2.0-py3-none-any.whl`
- Size: `194397` bytes
- SHA-256: `92f993d5b4093d2c9b6a2785e390abdae616b9219faeab3e1d62fea44a5ed260`
