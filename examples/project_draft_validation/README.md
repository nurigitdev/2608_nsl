# Project Draft Validation

This example is the `AC-W02` Project Draft contract for NSL v0.2. The
validation profile executes only `PROJECT.GET_PROJECT` and
`PROJECT.CHECK_DUPLICATE`; the controlled source additionally declares
`PROJECT.CREATE_PROJECT_DRAFT`, whose Tool Contract uses
`PROJECT.GET_PROJECT` for read-after-write verification.

Project-specific rules remain in NSL Source, Tool contracts, and Mock
fixtures. The common Runtime contains no Project branch. `start_day` and
`end_day` are monotonic tenant-calendar day indices produced by the typed
Agent or Tool adapter boundary. This keeps calendar conversion out of Runtime
and respects the language rule that ordering is limited to numeric and Money
types.

The deterministic validation profile covers active and inactive parents,
empty through over-limit duplicate results, allowed-period boundaries,
reversed date ranges, team authority, partial and unknown evidence, malformed
or missing data, authorization failure, and missing inputs. None of these
paths invokes a WRITE provider.

```powershell
python -m nsl check --profile examples/project_draft_validation/profile.json
python -m nsl run --profile examples/project_draft_validation/profile.json
python -m nsl test --suite examples/project_draft_validation/scenarios.json
```

`project_draft_write.ns` is the canonical controlled WRITE source used by the
Runtime API. CLI approval and resume orchestration remains assigned to Slices
0066 and 0067; no example profile auto-approves a Production WRITE.
