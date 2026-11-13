# NSL core status and identity contracts

R2-S01 introduces the status enums and validated identity newtypes for `NSL-RUST-CORE-001` through `NSL-RUST-CORE-007`. Statuses parse and encode as the exact case-sensitive strings from Python Oracle [`28a949e`](../../../rust-transition/baseline/python-oracle-lock.json). The tests compare nine enums with the [frozen status corpus](../../../compatibility/v0_2_1/values/core-status-enums/enum-values.json). `ApprovalDecision`, `PreWriteEligibility`, and `ToolCapability` use the values in the same Oracle's [`nsl/core.py`](../../../nsl/core.py).

## Identity parsing

| Type | Accepted input |
| --- | --- |
| `BusinessOperationId`, `ApprovalRequestId` | A string with at least one non-whitespace character. Parsing preserves the original text. |
| `IdempotencyKey` | The exact prefix `idem:sha256:` and a suffix of 64 Unicode scalar values, matching Python `str` length for UTF-8 input. Uppercase and non-hexadecimal suffix characters are accepted. |
| `PayloadHash` | The exact prefix `sha256:` and 64 lowercase ASCII hexadecimal digits. |

The [`IdempotencyKeyFactory`](../../../nsl/idempotency.py) in the Oracle emits lowercase hexadecimal. The Oracle's idempotency identity and WRITE request boundaries check prefix and length, while its payload hash boundary also checks the hexadecimal alphabet. Rust preserves that distinction at construction. The tests cover wrong prefixes, short and long suffixes, uppercase and non-hexadecimal characters, and multibyte input.

These newtypes validate text shape. Runtime boundaries also own credential screening, authority checks, and comparisons against recomputed hashes and binding evidence.
