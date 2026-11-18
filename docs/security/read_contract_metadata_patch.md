# Python READ contract authority security patch

This maintenance patch follows the completed Rust R6-S04. It applies to the
current Python implementation only. The compatibility reference remains
`28a949ec96581cbdc52c8133527b23bb5b72ee0a`; the Oracle wheel, lock and differential
fixtures are unchanged. No R6-S05 work is included.

## Finding and fix

Before this patch, NSO 1.0/1.1 could advertise READ and a weaker scope while
carrying the correct hash of an actual WRITE contract. Runtime preflight checked
the hash but did not bind those authority claims to the resolved contract. The
READ path authorized the advertised scope. The synthetic probe completed and
called the WRITE mock once without the actual WRITE scope or approval. This
demonstrates a Runtime authorization defect; deployment exposure also depends on
artifact admission, catalog configuration and independent provider checks.

`RuntimeEngine` now compares both capability and required scope with the resolved
contract during preflight. Both request validation APIs resolve and check again
at Tool authorization, and authorize the actual contract's scope. Every READ
entry repeats the comparison and additionally requires the actual capability to
be exactly READ. These checks precede argument evaluation, Tool authorization,
tool-call accounting, invocation IDs and provider dispatch. A changed registry
cannot substitute different capability/scope after preflight unnoticed.

Existing public error boundaries are preserved: preflight/request validation
raise `RuntimeContractError` (execution projects a RUNTIME failure); a READ entry
failure raises `ToolExecutionError` with `TOOL_CONTRACT_MISMATCH`. Missing actual
scope retains `AuthorizationError` and the AUTHORIZATION result. Genuine
matching WRITE declarations remain available to the governed controlled
pipeline; READ nodes cannot invoke them, including before approval.

## Verification and traceability

`TEST-SEC-READ-CONTRACT` is a maintenance verification ID. Existing requirement
statuses and original implementation Slice assignments are retained, with new
regression evidence appended in both traceability ledgers. SRS text and baseline
fingerprints are unchanged.

| Requirement | Original Slice | Status | New evidence in `tests/test_read_contract_security.py` |
| --- | --- | --- | --- |
| NSL-REQ-005 | 0011 | IMPLEMENTED | NSO-admitted capability/scope substitution rejection |
| NSL-READ-001 | 0018 | IMPLEMENTED | Direct READ recheck and nested-call isolation |
| NSL-SEC-015 | 0001 | IMPLEMENTED | Actual scope allow/deny and registry-change rejection |
| NSL-TOL-201 | 0034 | IMPLEMENTED | Controlled-prefix READ cannot dispatch a matching WRITE contract |
| NSL-IR-205 | 0036 | IMPLEMENTED | Real contract hash does not authorize forged IR metadata |
| NSL-CTX-210 | 0043 | IMPLEMENTED | Principal scopes retained in both validation APIs and execution |

The regression matrix covers both IR versions; WRITE, TRANSACTION and unknown
actual capabilities; scope-only and combined substitutions; three public entry
points; direct READ/LET; no nested argument effects or LET publication; matching
non-READ metadata; controlled-prefix rejection; actual-scope denial and allowed
execution; and registry changes between preflight and use. Rejection asserts no
provider calls and no charged invocations. Existing controlled WRITE tests verify
the approved path remains functional.

The reusable mock-only probe is
[`probe_python_read_metadata.py`](../../rust/crates/nsl-runtime/tests/fixtures/probe_python_read_metadata.py).
Run it against an archive of the pinned commit and against `.` separately:
the frozen Oracle retains the original COMPLETED/one-call observations, while
the patched current tree returns FAILED/zero calls in both IR versions. No
external business provider is constructed.

Use the single authoritative regression/coverage command for this separate patch:

```bash
COVERAGE_FILE="$PWD/test/.coverage" .venv/bin/python tools/run_quality.py
```

CI uses `.github/workflows/nsl-quality.yml` on this branch through
`workflow_dispatch`: Python 3.12 quality on Linux and Windows, plus Windows
wheel build/install verification. This patch does not replace the frozen wheel
or publish a release. Exact local and remote verification results are reported
with the patch commit after the gates finish.

Local verification on Python 3.12.3 passed **2,391 tests**, including **58 new
security regressions**. Statement coverage is **99.28%** (10,937/11,016) and
branch coverage is **98.11%** (3,634/3,704). Architecture, performance,
traceability and certification gates passed. The pre-existing v0.1 CONDITIONAL
status (two PARTIAL and one PLANNED requirements) is preserved. An initial test
attempt found a missing IR 1.1 `risk_level` in the new test fixture; correcting
that fixture and rerunning the same authoritative command produced this pass.
