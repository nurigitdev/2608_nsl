# NSO IR model

`nsl-ir` owns the typed in-memory NSO model. `SkillObject` contains the version, skill metadata, ordered body and specs, declared resource limits, static analysis, hashes, and source manifest. Its expressions and statements are closed enums. `nsl-core` provides types, exact literals, classifications, tool capability, risk levels, and SHA-256 digest text.

`IrVersion` parses `1.0` and `1.1`. `RequiredToolKind::ReadV1_0` represents a READ tool with no risk field. `RequiredToolKind::V1_1` carries the tool capability and mandatory risk level. The 1.1 approval and WRITE nodes preserve result types, ordered arguments, and the static approval reference. Approval is required by definition and its TTL is optional. Exact Core integers preserve Oracle values before the execution boundary applies host-specific limits. Open text fields such as the CHECK severity and binary operator retain the strings accepted by the Oracle IR schema.

The model carries supplied digests. `R3-S03` owns the production codec and `R3-S04` owns hash calculation. The test harness projects model fixtures into structured data for comparison.

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

The API starts after byte parsing, with a structured `ValueData` tree. JSON duplicate-key and encoding checks belong to `R3-S03`. Digest recomputation and cross-checks belong to `R3-S04`.

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
