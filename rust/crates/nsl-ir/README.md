# NSO IR model

`nsl-ir` owns the typed in-memory NSO model. `SkillObject` contains the version, skill metadata, ordered body and specs, declared resource limits, static analysis, hashes, and source manifest. Its expressions and statements are closed enums. `nsl-core` provides types, exact literals, classifications, tool capability, risk levels, and SHA-256 digest text.

`IrVersion` parses `1.0` and `1.1`. `RequiredToolKind::ReadV1_0` represents a READ tool with no risk field. `RequiredToolKind::V1_1` carries the tool capability and mandatory risk level. The 1.1 approval and WRITE nodes preserve result types, ordered arguments, and the static approval reference. Approval is required by definition and its TTL is optional. Exact Core integers preserve Oracle values before the execution boundary applies host-specific limits. Open text fields such as the CHECK severity and binary operator retain the strings accepted by the Oracle IR schema.

`R3-S02` owns document admission, including version-specific node combinations, credential screening and WRITE/approval linkage validation for incoming objects. `R3-S03` owns the production codec and `R3-S04` owns hash calculation. In this slice the model carries the supplied digests, and only the test harness projects fixtures into comparable data.

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
