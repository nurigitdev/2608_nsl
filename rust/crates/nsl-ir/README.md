# NSO IR model

`nsl-ir` owns the typed in-memory NSO model. `SkillObject` contains the version, skill metadata, ordered body and specs, declared resource limits, static analysis, hashes, and source manifest. Its expressions and statements are closed enums. `nsl-core` provides types, exact literals, classifications, tool capability, risk levels, and SHA-256 digest text.

`IrVersion` parses `1.0` and `1.1`. `RequiredToolKind::ReadV1_0` represents a READ tool with no risk field. `RequiredToolKind::V1_1` carries the tool capability and mandatory risk level. The 1.1 approval and WRITE nodes preserve result types, ordered arguments, and the static approval reference. Approval is required by definition and its TTL is optional. Exact Core integers preserve Oracle values before the execution boundary applies host-specific limits. Open text fields such as the CHECK severity and binary operator retain the strings accepted by the Oracle IR schema.

The model carries the declared source bundle and semantic digests. The test harness projects model fixtures into structured data for comparison.

| Rust SRS | R3-S01 evidence |
| --- | --- |
| `NSL-RUST-IR-001`, `003` | [`IrVersion`](src/model.rs) distinguishes 1.0 and 1.1 and rejects unsupported version text. The [Oracle tests](tests/oracle_model.rs) verify both versioned tool shapes. |
| `NSL-RUST-IR-002`, `016` | [`ExpressionKind` and `StatementKind`](src/nodes.rs) represent all Oracle nodes. The tests compare every field in the read-only and Controlled WRITE corpus objects, including approval linkage. Supplementary cases cover all literal variants, ResultPolicy defaults, and absent and present TTL values. |

Regenerate the model fixtures from the root of the repository with the locked Python Oracle and pinned Rust toolchain.

```bash
oracle_dir=$(mktemp -d)
git archive 28a949ec96581cbdc52c8133527b23bb5b72ee0a nsl | tar -x -C "$oracle_dir"
.venv/bin/python -I -B rust/crates/nsl-ir/tests/fixtures/generate.py "$oracle_dir" compatibility/v0_2_1/nso > "$oracle_dir/oracle_models.rs"
rustfmt +1.99.1 --edition 2024 "$oracle_dir/oracle_models.rs"
cmp "$oracle_dir/oracle_models.rs" rust/crates/nsl-ir/tests/fixtures/oracle_models.rs
```

## R3-S02 structured document admission

`validate_nso_document(&ValueData)` screens the entire document for credential material before validating structure. It checks exact field sets, primitive and tagged literal shapes, closed enums, number minimums, tool metadata, and source manifest structure. Schema errors identify a static field path and array positions. Credential errors omit input text and paths.

IR 1.0 accepts READ tools without risk levels and the read-only node set. IR 1.1 requires tool risk levels, admits approval and WRITE nodes, and checks unique tool and approval references. Every WRITE refers to a declared WRITE tool and a preceding approval that is used exactly once. Under `NSL-0.2-STRICT` there is at most one WRITE. The validator uses the Oracle's statement order and loop-body rules. It also checks the nonblank fields and distinct WRITE argument names required by the Oracle's model constructors. Runtime profile admission remains a later execution check.

The API starts after byte parsing, with a structured `ValueData` tree. The codec checks UTF-8, JSON grammar, and duplicate object keys before invoking it.

| Rust SRS | R3-S02 evidence |
| --- | --- |
| `NSL-RUST-IR-001`–`005` | [`structured_admission_matches_frozen_oracle`](tests/schema/mod.rs) compares 87 cases against `28a949e`, including the two frozen NSOs and version-specific structure and linkage violations. |
| `NSL-RUST-IR-013` | The same fixture verifies credential rejection before schema validation for nested keys, text, and approval messages. `schema_error_does_not_echo_caller_controlled_fields` verifies generic diagnostics for unknown fields. |

Regenerate the admission fixture using the same Oracle export and pinned toolchain as above.

```bash
.venv/bin/python -I -B rust/crates/nsl-ir/tests/fixtures/generate_schema.py "$oracle_dir" compatibility/v0_2_1/nso > "$oracle_dir/schema_cases.rs"
rustfmt +1.99.1 --edition 2024 "$oracle_dir/schema_cases.rs"
cmp "$oracle_dir/schema_cases.rs" rust/crates/nsl-ir/tests/fixtures/schema_cases.rs
```

## R3-S03 canonical JSON codec

`NsoCodec::decode(bytes, NsoLimits { max_bytes, max_depth, max_nodes })` checks the caller's inclusive limits while reading the input. The root is depth zero and counts as one node. Every JSON value counts as a node, including containers. Keys are strings within their parent object. Duplicate keys are rejected after escape decoding. The decoder runs credential, schema, and digest checks before returning a typed `SkillObject`.

`NsoCodec::encode(&skill, max_decimal_bytes)` validates the projected object and writes UTF-8 JSON with scalar-ordered keys, no extra whitespace, and the Oracle's escaping. List order is preserved. Decimal and Money use the Core fixed-point encoder and its caller-supplied budget per numeric string. JSON integer tokens keep their exact base-10 digits, with the frozen CPython 3.12 default of 4,300 digits in either direction. Noncanonical whitespace and valid string escapes are accepted on input and normalized by the encoder.

| Rust SRS | R3-S03 evidence |
| --- | --- |
| `NSL-RUST-IR-006`–`009`, `NSL-RUST-DIFF-003` | [`codec` tests](tests/codec/mod.rs) compare both frozen NSOs against independent Rust models, then decode and re-encode those same bytes. The supplementary Oracle fixture verifies Unicode escaping, large integers, Decimal and Money. |
| `NSL-RUST-SEC-011`, `NSL-RUST-IR-013` | The same tests exercise byte, depth, and node boundaries in both IR versions, including one unit below and above each exact limit. Credential screening runs on both codec paths. |

Regenerate the scalar fixture using the same Oracle export as above.

```bash
.venv/bin/python -I -B rust/crates/nsl-ir/tests/fixtures/generate_codec.py "$oracle_dir" compatibility/v0_2_1/nso > "$oracle_dir/canonical-scalars.nso"
cmp "$oracle_dir/canonical-scalars.nso" rust/crates/nsl-ir/tests/fixtures/canonical-scalars.nso
```

## R3-S04 hash identities

| Identity | SHA-256 input |
| --- | --- |
| Source manifest and source bundle | The canonical JSON array of manifest entries in their supplied order. Each entry carries `logical_path`, `sha256`, `size_bytes`, and `is_root`. |
| Semantic | The canonical JSON projection of the typed IR with `build` and `hashes` omitted. |
| Artifact | The complete canonical NSO bytes emitted by `NsoCodec::encode`, including `build` and `hashes`. |

These inputs use the unprefixed Oracle SHA-256 domains. `sha256:` prefixes the returned digest text. `artifact_sha256` hashes the exact byte slice it receives. Pass the complete output of the encoder for canonical artifact identity. Producers compute and assign the source bundle and semantic digests before encoding.

The decoder compares the two declared source bundle digests, recomputes the manifest digest, and recomputes the semantic digest from the typed IR. It uses the decode byte budget as the aggregate limit for expanded Decimal and Money strings during semantic hashing. This bounds expansion even when multiple short exponent tokens occur in the input.

| Rust SRS | R3-S04 evidence |
| --- | --- |
| `NSL-RUST-IR-010`, `011`, `012`, `015` | [`hash` tests](tests/hash/mod.rs) compare all four identities with the frozen Oracle for IR 1.0 and 1.1. They check manifest ordering, UTF-8 paths, build-only changes, and approval TTL. |
| `NSL-RUST-IR-014`, `NSL-RUST-SEC-011` | The tests distinguish conflicting declarations, altered manifests, and semantic mismatches. Exponent expansion is checked against the caller's byte budget. |

Regenerate the digest fixture using the same Oracle export as above.

```bash
.venv/bin/python -I -B rust/crates/nsl-ir/tests/fixtures/generate_hashes.py "$oracle_dir" compatibility/v0_2_1/nso > "$oracle_dir/hash-oracle.tsv"
cmp "$oracle_dir/hash-oracle.tsv" rust/crates/nsl-ir/tests/fixtures/hash-oracle.tsv
```
