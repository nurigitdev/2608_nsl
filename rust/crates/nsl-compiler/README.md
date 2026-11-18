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

## R5-S03 Symbol and scope resolution

`resolve_symbols(&skill, sources)` runs on the composed AST. Normal symbol IDs start at `s0001` and follow inputs, composed contexts, and body declarations in that order. The resolver records declaration origins and binds each expression path to the symbol for its first segment. Skill scope is `scope0001`; foreach blocks receive sequential child scope IDs and reserve their iterator IDs upon entry, after resolving the collection.

The resolver checks let initializers and CHECK conditions before declaring their names. INPUT, CONTEXT, VARIABLE, ITERATOR, and CHECK share the active name table. Duplicate declarations and active ancestor shadowing produce `NSL-E2002`. Unavailable names produce `NSL-E2001`. A completed foreach frees its active names for reuse while its bindings remain in the result. Semantic diagnostics preserve the originating `SourceSpan`, including declarations from Include fragments.

NSL 0.2 skill-scope approvals use their own `ApprovalSymbolId` sequence starting at `s0001`. The approval namespace can hold the same names as the ordinary symbols. Duplicate approval IDs produce `NSL-E2002` at the second ID token. R5-S06 owns WRITE linkage, approval scope validation, and approval ordering and usage checks.

| Rust SRS | Status and verification in R5-S03 |
| --- | --- |
| `NSL-RUST-COMP-002` | Implemented. The [symbol differential test](tests/symbol_oracle.rs) checks source declarations, scope and symbol IDs, expression references, and the independent approval namespace. |
| `NSL-RUST-COMP-003` | Implemented. The same test checks duplicate declarations, use before declaration, shadowing, scope escape, and sibling name reuse in both language versions. |
| `NSL-RUST-COMP-019` | Symbol-resolution evidence. The differential test checks codes, positions, messages and snippets, and the [boundary tests](tests/symbol_boundaries.rs) exercise deep scope traversal and error unwind. |

Regenerate the 65-case fixture from the same Oracle checkout with Python 3.12. The generator observes SymbolTable calls made by the frozen compiler and approval analyzer.

```bash
.venv/bin/python -I -B rust/crates/nsl-compiler/tests/fixtures/generate_symbols.py "$oracle_dir" > "$oracle_dir/symbol-oracle.tsv"
cmp "$oracle_dir/symbol-oracle.tsv" rust/crates/nsl-compiler/tests/fixtures/symbol-oracle.tsv
```

## R5-S04 Static type checking

`StaticTypeChecker::new(sources).check(&skill, &resolution, &catalog)` annotates a composed AST after successful `resolve_symbols` on that same AST. Arena IDs and the resolution belong to their originating skill. `TypedSkill` exposes immutable symbol and expression types with data classifications and the ordered Field/Projection decisions for each path. Statement and expression traversal uses explicit work stacks. The stage reports missing AST or resolution entries as `TypeCheckError::InvalidResolution`.

All type comparisons are exact. Int division returns Decimal; other Int arithmetic returns Int. Decimal arithmetic stays Decimal. Money supports same-currency addition/subtraction and ordering. Equality and inequality accept exactly matching types, including Bool, lists, records, enums, and internal Optional types. Bool arithmetic/ordering and non-Bool CHECK conditions are rejected. The closed built-in registry is `count`, `min`, `max`, and `sum`; `sum` accepts only lists of Int, Decimal, or Money. Like the Oracle, min/max accept any list element type. The existing lexer treats `max` as a keyword; the direct signature tests cover it without changing source syntax.

Field access accepts records and one level of list projection. Projection retains nested list element types and does not flatten them. Optional types remain internal model types: they propagate through symbols and foreach elements and can be compared with the same Optional type, but are never implicitly unwrapped or promoted from `T`. Their existing canonical encoding rejection remains in Core and Contract.

The checker resolves required contracts exactly, checks READ capability, validates named argument sets and duplicates before checking values, and checks each argument in source order. Tool result types and classifications come from the contract. EMIT requires the exact declared field set and types, with output classifications that allow each value. Symbol and type diagnostics share `SourceDiagnosticContext` to preserve Oracle codes, messages, source positions, and snippets. Contract field names need not be source identifiers, so their diagnostic spelling uses Python 3.12/Unicode 15.0 repr rules, including quotation and nonprintable-character escapes.

Resource bounds, approval linkage, WRITE safety, and the complete IR lowering/public compile API remain R5-S05 through R5-S08. This stage does not establish that a WRITE is safe to execute. Its input precondition also means mixed name/type error priority across compiler stages will be integrated with the full compiler.

| Rust SRS | Status, verification ID, and evidence in R5-S04 |
| --- | --- |
| `NSL-RUST-COMP-004` | Implemented in the static type stage. `R5-S04-TYPE-ORACLE`: [type differential tests](tests/type_oracle.rs) compare 6,925 rules and 95 source cases with Oracle `28a949e`, including Money, records, lists, projections, internal Optional, and inferred classifications. |
| `NSL-RUST-COMP-005` | Implemented. `R5-S04-BOOL`: the same rules and source cases reject truthiness, Bool arithmetic/ordering, and implicit numeric conversions while permitting Bool equality/inequality. |
| `NSL-RUST-COMP-006` | Implemented in the static type stage. `R5-S04-TOOL-TYPES`: the source cases verify exact READ/WRITE argument types, contract result types, required identity/version errors, argument error priority, and EMIT types/classifications. |
| `NSL-RUST-COMP-019` | Additional type-stage evidence. `R5-S04-DIAGNOSTICS`: differential errors match codes, messages, positions and snippets; [boundary tests](tests/type_boundaries.rs) preserve full spans and Include fragment locations. |

`R5-S04-BOUNDARY` additionally verifies an 8,192-term binary expression and 128 nested foreach scopes on a 512 KiB thread stack, missing diagnostic context, inconsistent inputs, and Optional unwrapping/promotion rejection. Identical type-check calls produce identical annotation maps or errors.

Regenerate from the frozen Oracle checkout using Python 3.12. Each output was generated twice and compared byte-for-byte with its committed file; all SHA-256 values matched.

```bash
.venv/bin/python -I -B rust/crates/nsl-compiler/tests/fixtures/generate_types.py "$oracle_dir" rules > "$oracle_dir/type-rules-oracle.tsv"
.venv/bin/python -I -B rust/crates/nsl-compiler/tests/fixtures/generate_types.py "$oracle_dir" skills > "$oracle_dir/type-skills-oracle.tsv"
.venv/bin/python -I -B rust/crates/nsl-compiler/tests/fixtures/generate_types.py "$oracle_dir" unicode > "$oracle_dir/printable.rs"
cmp "$oracle_dir/type-rules-oracle.tsv" rust/crates/nsl-compiler/tests/fixtures/type-rules-oracle.tsv
cmp "$oracle_dir/type-skills-oracle.tsv" rust/crates/nsl-compiler/tests/fixtures/type-skills-oracle.tsv
cmp "$oracle_dir/printable.rs" rust/crates/nsl-compiler/src/types/printable.rs
```

| Output | SHA-256 |
| --- | --- |
| `type-rules-oracle.tsv` | `8ee6c2a4e04b3b23a1ee5c9a73e448eac3d46f02bdb5871123d37e79e7b6a29f` |
| `type-skills-oracle.tsv` | `a352b4812e14ba8357d7cac57e923deb3c7641e8b2af1d2b26aeed07e6cab40a` |
| `printable.rs` | `181ce0e11808f3433f7182a105477ac998897ea9af350dfc23f214b7b7ef3e9a` |

Local verification on pinned Rust 1.99.1 passed: fmt, locked workspace check, all-target/all-feature Clippy with warnings denied, architecture guard and its 7 tests, 93 Rust tests, and a frozen workspace build. The mandatory Python gate ran once: 2,333 passed, statement coverage 99.29%, branch coverage 98.14%.

Rust coverage uses `nightly-2026-11-15`, `-C instrument-coverage -Z coverage-options=branch`, and matching LLVM tools, with only workspace `crates/*/src` files included. The statement figure uses LLVM executable-line coverage, reported separately from branch coverage: 5,148/5,192 = **99.15%**, and 773/782 = **98.85%**. The compiler crate is 99.19%/98.28%; the new type modules are 99.63%/100.00%. Tests and generated Unicode constant tables are excluded from the reported executable source totals.
