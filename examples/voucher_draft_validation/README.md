# Voucher Draft Validation and Controlled WRITE

This canonical NSL 0.2 example implements the read-only validation prefix of
`AC-W01`. It validates an active account, active project, open accounting
period, and minimum voucher amount through injected READ Tool contracts.

The read-only profile registers only `ACCOUNT.GET_ACCOUNT`,
`PROJECT.GET_PROJECT`, and `FINANCE.GET_ACCOUNTING_PERIOD`, so its CLI workflow
cannot invoke a WRITE provider.

`approval.json` defines the Slice 0062 approval boundary. A complete passing
validation is sealed as a checkpoint before this request is published. The
asset carries only display-safe metadata and a protected payload reference;
provider WRITE execution remains outside the read-only CLI profile.

`voucher_draft_write.ns` is the canonical Slice 0063 end-to-end Skill. The
public controlled Runtime pipeline executes its READ/CHECK prefix without
changing Skill identity, resumes from bound approval evidence, re-evaluates
policy and business preconditions, and invokes
`FINANCE.CREATE_VOUCHER_DRAFT` only after durable `WRITE_REQUESTED` evidence.
It then verifies a committed result through `FINANCE.GET_VOUCHER`. The final
`voucher_no` output is emitted only for a committed and verified outcome.

`principal_write.json` supplies the explicit example scopes used by the E2E
acceptance test. Live provider adapters and customer endpoints are deliberately
absent; the test uses injected deterministic ports, protected Replay evidence,
and a Replay session that has no live WRITE provider. CLI support for the
controlled run/resume workflow remains assigned to Slices 0066 and 0067.

Run the deterministic local profile and scenario suite from the repository
root:

```powershell
.\.venv\Scripts\python.exe -m nsl check examples\voucher_draft_validation\voucher_draft_validation.ns --tool-contracts examples\voucher_draft_validation\tools.json
.\.venv\Scripts\python.exe -m nsl run --profile examples\voucher_draft_validation\profile.json
.\.venv\Scripts\python.exe -m nsl test --suite examples\voucher_draft_validation\scenarios.json
```

The fixture set covers valid and invalid business evidence, amount boundary
values `0` and `1`, missing and malformed results, partial and unknown evidence,
authorization failure, and missing dependencies. Tenant mismatch and an
unexpected dependency failure are exercised by the repository acceptance
test because they require an injected Tool Port boundary rather than fixture
business data.
