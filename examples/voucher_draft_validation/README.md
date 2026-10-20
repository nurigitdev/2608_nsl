# Voucher Draft READ Validation

This canonical NSL 0.2 example implements the read-only validation prefix of
`AC-W01`. It validates an active account, active project, open accounting
period, and minimum voucher amount through injected READ Tool contracts.

The executable prefix registers only `ACCOUNT.GET_ACCOUNT`,
`PROJECT.GET_PROJECT`, and `FINANCE.GET_ACCOUNTING_PERIOD`. The canonical
`FINANCE.CREATE_VOUCHER_DRAFT` and `FINANCE.GET_VOUCHER` contracts are reserved
for Slice 0063, so this Slice cannot invoke a WRITE provider.

`approval.json` defines the Slice 0062 approval boundary. A complete passing
validation is sealed as a checkpoint before this request is published. The
asset carries only display-safe metadata and a protected payload reference;
provider WRITE execution remains outside this Slice.

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
