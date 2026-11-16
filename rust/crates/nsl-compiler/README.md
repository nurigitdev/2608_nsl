# NSL Include composition

`SourceBundleBuilder::new(resolver, include_options, parse_limits).build(root)` parses the root skill and resolves each declaration through an injected `IncludeResolver`. The resolver receives the including source and a validated, canonical relative POSIX path. A filesystem resolver owns its physical root and filesystem policy. The compiler performs lexical path normalization and verifies that each returned logical path equals the requested path.

The traversal checks depth before resolution, then validates the response and enforces the unique-fragment count and total decoded UTF-8 byte budget before parsing new fragments. The byte budget includes the root. The default budgets are 16 levels, 100 fragments, and 10 MiB. A repeated reference counts the source once, preserves every dependency edge, and must match the canonical path, `SourceId`, and entire decoded text. An active matched source is a cycle. Identity conflicts return `IncludeIdentityConflict` with the declaration span and no NSL diagnostic code. Relative logical paths and consistent resolver identity are the Rust boundary rules for R4-S04.

`SourceBundle::compose` merges fragment tool requirements, context fields, and the single required limits block as AST declarations. It retains node spans, root statements, and tool and context ordering. The composed AST has an empty include list. The bundle retains the graph and the root-first depth-first manifest with content SHA-256 and byte sizes. The manifest preserves the root source's original logical path spelling, and the traversal uses its normalized path for identity checks. Compile-time `BuildMetadata` reuses the NSO manifest hash implementation.

| Rust SRS | Status and verification in R4-S04 |
| --- | --- |
| `NSL-RUST-SRC-010` | Implemented. [Oracle differential cases](tests/include_oracle.rs) cover path normalization, invalid paths, graph and manifest order, and depth, count, and UTF-8 byte boundaries. [Boundary tests](tests/include_boundaries.rs) cover relative-only configuration, exact identity, and a 2,048-level traversal. |
| `NSL-RUST-SRC-011` | Implemented. The differential and boundary tests verify root, ancestor, and normalized-alias cycle rejection. |
| `NSL-RUST-SRC-012` | Implemented. Both suites inject in-memory resolvers and check the canonical requests and validation order. |

Regenerate the 94-case fixture from the repository root with Python 3.12.

```bash
oracle_dir=$(mktemp -d)
git archive 28a949ec96581cbdc52c8133527b23bb5b72ee0a nsl | tar -x -C "$oracle_dir"
.venv/bin/python -I -B rust/crates/nsl-compiler/tests/fixtures/generate_includes.py "$oracle_dir" > "$oracle_dir/include-oracle.tsv"
cmp "$oracle_dir/include-oracle.tsv" rust/crates/nsl-compiler/tests/fixtures/include-oracle.tsv
```
