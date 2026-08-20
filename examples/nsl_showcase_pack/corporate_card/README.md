# Corporate Card Control Showcase

## Skills

`FINANCE.CORPORATE_CARD_MONTHLY_SUMMARY` emits monthly totals and transaction
counts for every active team. It has no CHECK statement.

`FINANCE.CORPORATE_CARD_MONTHLY_POLICY_CHECK` emits the same summary and six
independent policy statuses:

| Check | PASS condition | Severity |
|---|---|---|
| `MONTHLY_AMOUNT_LIMIT` | total amount <= KRW 1,000,000 | ERROR |
| `CARD_USAGE_PRESENT` | transaction count > 0 | WARNING |
| `MONTHLY_COUNT_LIMIT` | transaction count < 10 | WARNING |
| `MISSING_RECEIPT` | missing receipt count == 0 | ERROR |
| `SINGLE_TRANSACTION_LIMIT` | maximum transaction <= KRW 500,000 | WARNING |
| `RESTRICTED_MERCHANT` | restricted merchant count == 0 | ERROR |

The limits are runtime context values, not hard-coded policy constants. The
included `context.json` supplies the showcase values.

## Deterministic Period

The caller supplies `year` and `month`. Neither Skill reads a wall clock, so the
same input, context, Tool evidence, and Skill identity can be replayed.

An active team roster is read separately from card transactions. This is
required to emit and check teams with zero card usage.

## Boundary Matrix

| Team | Total | Count | Expected highlights |
|---|---:|---:|---|
| Finance | KRW 900,000 | 9 | all PASS |
| Development | KRW 1,000,000 | 10 | amount PASS, count FAIL, single amount at limit PASS |
| Human Resources | KRW 0 | 0 | usage presence FAIL, empty Money sum is typed zero |
| Sales | KRW 1,000,001 | 2 | amount, receipt, single amount, merchant FAIL |

Policy CHECK failure uses `on_fail REPORT`, so a completed control run can
contain FAIL statuses without becoming a Runtime failure.

## Run

From the repository root:

```powershell
python -m nsl check --profile examples\nsl_showcase_pack\corporate_card\summary.profile.json
python -m nsl run --profile examples\nsl_showcase_pack\corporate_card\summary.profile.json
python -m nsl test --suite examples\nsl_showcase_pack\corporate_card\summary.scenarios.json

python -m nsl check --profile examples\nsl_showcase_pack\corporate_card\policy.profile.json
python -m nsl run --profile examples\nsl_showcase_pack\corporate_card\policy.profile.json
python -m nsl test --suite examples\nsl_showcase_pack\corporate_card\policy.scenarios.json
```

Each scenario suite verifies the boundary matrix, missing Tool fixture, denied
card Scope, collection limit, and deterministic repeat.

## Data and Security Contract

- Team identity and name are INTERNAL.
- Card amounts and transaction-derived counts are CONFIDENTIAL; status-only CHECK results are INTERNAL.
- The Tool Contract returns facts and candidates, never a final PASS or FAIL.
- `maximum_transaction_amount` is defined as KRW zero when a team has no usage.
- The showcase is bounded to 10 teams, 11 Tool calls, 10 loop iterations, and 10 emitted rows.
- Production adapters must define posted, canceled, refund, timezone, and team-at-transaction-time semantics.
- Credentials and endpoints must remain outside Skill, profile, fixture, audit, and package content.
