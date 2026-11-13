# NSL core contracts

R2-S01 introduces the status enums and validated identity newtypes for `NSL-RUST-CORE-001` through `NSL-RUST-CORE-007`. Statuses parse and encode as the exact case-sensitive strings from Python Oracle [`28a949e`](../../../rust-transition/baseline/python-oracle-lock.json). The tests compare nine enums with the [frozen status corpus](../../../compatibility/v0_2_1/values/core-status-enums/enum-values.json). `ApprovalDecision`, `PreWriteEligibility`, and `ToolCapability` use the values in the same Oracle's [`nsl/core.py`](../../../nsl/core.py).

## Identity parsing

| Type | Accepted input |
| --- | --- |
| `BusinessOperationId`, `ApprovalRequestId` | A string with at least one non-whitespace character. Parsing preserves the original text. |
| `IdempotencyKey` | The exact prefix `idem:sha256:` and a suffix of 64 Unicode scalar values, matching Python `str` length for UTF-8 input. Uppercase and non-hexadecimal suffix characters are accepted. |
| `PayloadHash` | The exact prefix `sha256:` and 64 lowercase ASCII hexadecimal digits. |

The [`IdempotencyKeyFactory`](../../../nsl/idempotency.py) in the Oracle emits lowercase hexadecimal. The Oracle's idempotency identity and WRITE request boundaries check prefix and length, while its payload hash boundary also checks the hexadecimal alphabet. Rust preserves that distinction at construction. The tests cover wrong prefixes, short and long suffixes, uppercase and non-hexadecimal characters, and multibyte input.

These newtypes validate text shape. Runtime boundaries also own credential screening, authority checks, and comparisons against recomputed hashes and binding evidence.

## Type, value, and classification model

R2-S02 covers the typed value and classification kernel for `NSL-RUST-CORE-007`, `009`, `010`, and `013`. `TypeRef` preserves record field order and enum value order. `from_data` checks the Oracle's NSO and Tool Contract type shapes. `to_data` mirrors the Oracle's structured projection, including its omission of empty `fields` or `values` arrays. As in Python, the external schema requires those keys on incoming record and enum documents.

`Optional<T>` is an internal type. It accepts explicit `Null` or a value accepted by `T`. Missing record fields stay missing. External type conversion rejects Optional at any nesting depth, preserving the frozen NSO and Tool Contract type schema.

`ValueData` represents canonical fields, tags, and exact number tokens before JSON serialization. Integer tokens have no machine-width limit. Decimal and Money amounts preserve the Oracle's fixed-point output, including trailing zeros and signed zero. General Decimal input parsing, arithmetic, and comparison belong to R2-S03. Date and DateTime variants carry ISO text from the temporal boundary. JSON byte encoding belongs to R3.

A complete envelope derives `EMPTY` for `Null` and empty lists or records. Zero, false, and empty strings remain `PRESENT`. An envelope can also preserve presence, completeness, classification, and provenance provided by the caller. Type conformance is checked through `TypeRef::accepts`.

The classification order is `PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED`. `maximum.allows(actual)` checks that the actual level fits the maximum. `highest` returns `None` for an empty input, so no default classification is introduced by aggregation.

[`value-model.json`](tests/fixtures/value-model.json) was produced with `TypeRef.to_data`, `encode_value`, `ValueEnvelope.complete`, `classification_allows`, and `highest_classification` from frozen Oracle `28a949e`. It covers nested lists, an integer wider than 128 bits, tagged values, presence boundaries, and all 16 classification pairs.
