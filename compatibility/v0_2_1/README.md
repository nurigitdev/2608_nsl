# NSL v0.2.1 compatibility corpus

The corpus records the same inputs and expected observations for the frozen Python v0.2.1 Behavioral Oracle and the Rust implementation. The [Oracle lock](../../rust-transition/baseline/python-oracle-lock.json) identifies the source commit. The NSL language, IR, tool, and security contracts define the expected behavior.

## Layout

- `manifest.json` indexes all cases. It conforms to [manifest.schema.json](manifest.schema.json), a [JSON Schema Draft 2020-12](https://json-schema.org/draft/2020-12/) document.
- `<category>/<case_id>/case.json` describes one case. Validate it against `#/$defs/case` in the same schema, with references resolved against that schema document.
- Input and expected artifact files live under their own case directory. Their paths are relative to that directory. Manifest entry paths are relative to the corpus root.

Categories are `source`, `contracts`, `compile`, `diagnostics`, `nso`, `values`, `runtime`, `write`, `approval`, `idempotency`, `trust`, `crypto`, `audit`, `replay`, and `integration`. A case has one primary category and may cite contracts from multiple areas.

## Manifest and case fields

`schema_version` identifies the manifest and case format. `corpus_version` identifies the corpus content release and starts at `1.0.0`. `oracle_commit` must equal `commit` in the Oracle lock. A case ID is stable and unique across the entire corpus. Each entry records the case ID, category, path to `case.json`, and SHA-256 of its exact bytes.

| Case field | Meaning |
| --- | --- |
| `case_id`, `category` | Must match the corresponding manifest entry. |
| `contract_refs` | Requirement IDs or normative contract references. |
| `source_refs` | Evidence in the frozen source tree, such as a repository-relative source path or `tests/test_file.py::test_name`. These are provenance strings. |
| `inputs`, `expected_artifacts` | Maps from case-local roles to `{ "path": "…", "sha256": "…" }` file references. Use an empty object when there are no files of that kind. |
| `expected_status` | Exact external status value for the case, or `null` when inapplicable. |
| `expected_error` | Stable external error `code` and, where applicable, `phase`, or `null` when no error is expected. For structured fields such as spans and safe messages, use an expected artifact. |
| `expected_hashes` | Named expected SHA-256 outputs, such as `semantic_hash` or `tool_contract_hash`. Use an empty object when the case asserts no hash output. |
| `classification` | NSL data classification of the case material. Use repository-safe synthetic data, including for cases that exercise higher classification levels. |
| `notes` | Human-readable context. Use an empty string when there are no notes. |

Every case must assert at least one observation. Roles name inputs or observations in the NSL contract, such as `source`, `tool_catalog`, `nso`, or `runtime_result`. Store exact canonical outputs as files so the consumer can compare bytes. Use the relevant wire contract for structured artifacts. Expected hashes assert product behavior, while file-reference hashes identify stored bytes.

## Verification and byte identity

1. Parse manifest and case descriptors as UTF-8 JSON and reject duplicate object keys. Apply schema validation to both document types.
2. Reject repeated case IDs or repeated descriptor paths, including entries whose other fields differ. Require each path to equal `<category>/<case_id>/case.json` and compare the descriptor's ID and category with the entry.
3. Check that referenced paths resolve to regular files within their prescribed root. Reject symlinks and Windows device names, including device names with extensions. The schema limits paths to portable lowercase components separated by `/`.
4. Reject missing or unhashed case descriptors, inputs, and expected artifacts. Compute SHA-256 over the exact file bytes and reject mismatches. Keep those bytes stable across checkouts. When adding a fixture type, ensure its Git attributes preserve the intended bytes.
5. Reject a manifest whose `oracle_commit` differs from the Oracle lock. Verify the corpus before running its cases.

Use UTF-8 without a BOM, LF line endings, and a final LF for the manifest and case descriptors. For deterministic exports, sort the manifest entries by `case_id` and keep JSON key ordering and formatting stable. The SHA-256 of the exact `manifest.json` bytes identifies the corpus content, including its references to case and fixture hashes.

## Reproduce the first corpus

From the repository root, use Python 3.12+ and a Git checkout containing the Oracle commit. The exporter reads the locked commit directly from Git. The verifier uses JSON Schema Draft 2020-12 and checks the recorded hashes and canonical NSO and Tool Contract bytes.

```bash
.venv/bin/python -m pip install -r tools/rust_transition/requirements.txt
.venv/bin/python -m tools.rust_transition.export_compatibility_corpus test/corpus-a
.venv/bin/python -m tools.rust_transition.export_compatibility_corpus test/corpus-b
.venv/bin/python -m tools.rust_transition.verify_compatibility_corpus test/corpus-a
.venv/bin/python -m tools.rust_transition.verify_compatibility_corpus test/corpus-b
.venv/bin/python -m tools.rust_transition.verify_compatibility_corpus
diff -r test/corpus-a test/corpus-b
sha256sum test/corpus-a/manifest.json test/corpus-b/manifest.json compatibility/v0_2_1/manifest.json
```

Choose new destination directories for subsequent exports. The checked-in corpus includes version identity, Core enum values, exact Decimal and Money values, a READ Tool Contract, canonical IR 1.0 and 1.1, a compiler diagnostic, and READ/CHECK/EMIT with `PASS`, `FAIL`, and `UNKNOWN`. The NSO hash fields store lowercase hexadecimal digests. NSL artifacts retain any `sha256:` prefix required by their own wire contracts.
