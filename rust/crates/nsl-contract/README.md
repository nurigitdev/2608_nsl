# NSL Tool Contract model

`ToolContract::new` validates a `ToolContractSpec` and retains the ordered input schemas, output schema, required scope, classification, risk, timeout, and result policy. The defaults are a 30,000 ms timeout, `READ_ONLY` risk, and `empty_is_valid = true`. Versions use three canonical ASCII numeric components. Each component preserves its original digits regardless of size. Contract construction preserves the exact strings supplied for `tool_id` and `required_scope`, including empty strings.

`ContractCapability` preserves the declared spelling and exposes a Core `ToolCapability` for `READ`, `WRITE`, and `TRANSACTION`. Other nonblank values retain their original text. Exact `WRITE` requires an operation, a risk level, and validated `WriteSemantics`. An operation or WRITE semantics on any other capability is rejected. The operation set contains `CREATE`, `CREATE_DRAFT`, and `UPDATE`.

WRITE semantics require a side effect, `ATOMIC` execution, required idempotency, `EXPLICIT` commit confirmation, approval, and precondition support. A supplied verification block carries at least one hint. The model uses Core `TypeRef` and `DataClassification` for the schema fields and exact booleans for the safety flags. Constructors return typed `ContractError` values.

`ToolContractCatalog` indexes by the exact tool ID and version and rejects duplicate identities. `resolve` distinguishes an unknown tool from a known tool with an incompatible version. `get` reports a missing exact pair.

| Rust SRS | Status and verification in R5-S01 |
| --- | --- |
| `NSL-RUST-TOOL-001` | Implemented. The [model differential test](tests/model_oracle.rs) compares all model fields, defaults, string boundaries, timeouts, and ordered types. |
| `NSL-RUST-TOOL-002` | Implemented. The same test checks WRITE operation, risk and safety fields, idempotency, commit confirmation, and optional verification. |
| `NSL-RUST-TOOL-004` | Implemented for the catalog. The same test checks exact resolution, missing tools and versions, and duplicate identities. |

Regenerate the 108-case constructor and catalog fixture with Python 3.12 from the repository root.

```bash
oracle_dir=$(mktemp -d)
git archive 28a949ec96581cbdc52c8133527b23bb5b72ee0a nsl | tar -x -C "$oracle_dir"
.venv/bin/python -I -B rust/crates/nsl-contract/tests/fixtures/generate_contract_model.py "$oracle_dir" > "$oracle_dir/contract-model-oracle.tsv"
cmp "$oracle_dir/contract-model-oracle.tsv" rust/crates/nsl-contract/tests/fixtures/contract-model-oracle.tsv
```
