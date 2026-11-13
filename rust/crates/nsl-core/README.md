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

`ValueData` represents canonical fields, tags, and exact number tokens before JSON serialization. Integer tokens have no machine-width limit. Date and DateTime variants carry ISO text from the temporal boundary. JSON byte encoding belongs to R3.

A complete envelope derives `EMPTY` for `Null` and empty lists or records. Zero, false, and empty strings remain `PRESENT`. An envelope can also preserve presence, completeness, classification, and provenance provided by the caller. Type conformance is checked through `TypeRef::accepts`.

The classification order is `PUBLIC < INTERNAL < CONFIDENTIAL < RESTRICTED`. `maximum.allows(actual)` checks that the actual level fits the maximum. `highest` returns `None` for an empty input, so no default classification is introduced by aggregation.

[`value-model.json`](tests/fixtures/value-model.json) was produced with `TypeRef.to_data`, `encode_value`, `ValueEnvelope.complete`, `classification_allows`, and `highest_classification` from frozen Oracle `28a949e`. It covers nested lists, an integer wider than 128 bits, tagged values, presence boundaries, and all 16 classification pairs.

## Decimal and Money

`NslDecimal` parses finite base-10 input without rounding and compares by numeric value. `1.0` and `1.00` compare equal, as do `-0.00` and `0`. The fixed-point encoder follows the Oracle's `format(value, "f")` and preserves trailing fractional zeros and signed zero. For example, `1E-3` encodes as `0.001` and `-0.00` encodes as `-0.00`. `to_fixed_point` and `Value::to_data` take a caller-provided byte budget per Decimal token, including Money amounts. Encoding checks that budget before expanding an exponent into zero padding.

Decimal addition, subtraction, multiplication, and division round each result to at most 28 significant digits using `ROUND_HALF_EVEN`, as in the frozen Python Oracle's default context. The rule is fixed in Rust. `1234567890123456789012345678 + 0.5` keeps its even last digit. `1234567890123456789012345679 + 0.5` rounds to `1234567890123456789012345680`. The arithmetic context has `Emax = 999999`, `Emin = -999999`, and a minimum result exponent of `-1000026`. Division by zero and arithmetic overflow return errors.

`Money` pairs a finite `NslDecimal` with three uppercase ASCII letters. Addition and subtraction use the same Decimal context. NSL comparisons use `checked_cmp`, which requires matching currencies just as addition and subtraction do. Rust structural equality compares the amount and currency, matching equality of the Oracle's Python data objects.

R2-S03 verification for the Rust SRS is recorded below. The test fixture contains 80 cases generated from the exact Oracle commit and exercises the core Decimal/Money codec and arithmetic. The Python-to-Rust-to-Python NSO byte round-trip is verified in the later codec slices.

| Rust SRS | R2-S03 status | Evidence |
| --- | --- | --- |
| `NSL-RUST-MONEY-001`, `002`, `009` | Implemented in Core | [`NslDecimal`](src/decimal.rs) keeps `bigdecimal` and its integer operations private. [`Money`](src/money.rs) exposes only Core types. |
| `NSL-RUST-MONEY-003`, `004`, `005`, `006`, `007` | Implemented in Core | [`frozen_decimal_parse_encoding_arithmetic_and_order`, `frozen_money_currency_arithmetic_and_order`, and `exponent_limits_and_fixed_point_allocation_are_checked`](tests/decimal_money.rs) verify Oracle outputs, malformed and non-finite inputs, and currency and exponent boundaries. |
| `NSL-RUST-MONEY-008`, `NSL-RUST-QUAL-007` | Core evidence for R2-S03 | [`decimal-money.tsv`](tests/fixtures/decimal-money.tsv) supplies Oracle outputs for fixed-point round-trips. `addition_and_subtraction_are_exact_for_representable_values` verifies 490 operand pairs and their round-trips. |

Generate the fixture from the repository root with Python 3.12 and a fresh Oracle checkout. The expected SHA-256 is `57d95e17e95c15e673a77e23c6ac7b970ab886da783f2618733e6fdc44da9f39`.

```bash
oracle_dir=$(mktemp -d)
git archive 28a949ec96581cbdc52c8133527b23bb5b72ee0a nsl | tar -x -C "$oracle_dir"
.venv/bin/python -I -B rust/crates/nsl-core/tests/fixtures/generate_decimal_money.py "$oracle_dir" > "$oracle_dir/decimal-money.tsv"
cmp "$oracle_dir/decimal-money.tsv" rust/crates/nsl-core/tests/fixtures/decimal-money.tsv
```
