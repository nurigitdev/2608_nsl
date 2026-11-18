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

## R5-S05 Static resource bounds

`StaticBoundAnalyzer::new(sources).analyze(&statements)` analyzes typed IR independently of lowering. It returns the existing IR `StaticAnalysis` model with exact maximum tool-call, loop-iteration, and emitted-record counts. Successful analyses have `bounded = true`; a nonpositive foreach maximum returns `UnboundedStructureError`. All counts use arbitrary-precision integers. The closed IR enums exhaustively cover every supported statement and expression; unknown shapes and noninteger maxima are rejected by model/admission boundaries rather than interpreted as zero cost.

Sequential statements add their costs. READ and WRITE each count one call plus all calls in their arguments. Field/Projection, built-in calls and binary expressions count calls in their children. CHECK counts calls in its condition; EMIT counts one record and calls in all its fields; Approval has no execution-count cost. A foreach collection expression is evaluated once on entry. Its body uses the declared static maximum, including nested loops:

```text
tool_calls(foreach)      = calls(collection) + max * calls(body)
loop_iterations(foreach) = max * (1 + iterations(body))
emit_records(foreach)    = max * emits(body)
```

`check(&statements, &limits, &skill_span, &limits_span)` additionally compares counts with the declared limits in Oracle order: tool calls (`NSL-E6101`), loops (`NSL-E6102`), then emitted records (`NSL-E6103`). Equality is permitted. An unprovable bound maps to `NSL-E6104` at the skill span. Other semantic bound errors retain the limits span and its source path, position, and snippet through `SourceDiagnosticContext`, including limits composed from Include fragments.

The user-approved R5-S05 conversion rule preserves the Oracle's Python 3.12 default boundary. Computation and `analyze` results remain exact even beyond 4,300 decimal digits. Only when an exceeded-limit diagnostic needs to format an actual or declared integer wider than 4,300 digits does `check` return `BoundError::IntegerConversion(BoundIntegerConversionError)`. This abort has no NSL diagnostic code and retains the limits `SourceSpan`. The sign does not count as a digit. Earlier ordinary errors retain priority over a later conversion abort. This represents the Oracle's raw `ValueError` as a Rust type error with source context; it does not replace the Oracle's formatting-limit failure with a semantic bound diagnostic.

Collection size and duration remain declared Runtime limits. The frozen compiler does not infer collection cardinality, compare foreach maxima with `collection_size`, or reduce iteration counts based on that limit. The existing Source/NSO boundaries admit the declarations, the analyzer preserves them unchanged, and Runtime will enforce actual sizes and duration under `NSL-RUST-RUN-008/011`. Approval linkage, WRITE safety and complete compiler/lowering integration remain R5-S06, R5-S07 and R5-S08 respectively.

| Rust SRS | Status, verification ID, and evidence in R5-S05 |
| --- | --- |
| `NSL-RUST-COMP-007` | Implemented in the static bound stage. `R5-S05-BOUND-ORACLE`: [Oracle differential tests](tests/bound_oracle.rs) verify 86 IR-shape cases and 61 NSL 0.1/0.2 compiler cases, including empty bodies, nested/sequential loops, READ/WRITE counting, exact limit equality, over-limit rejection and nonpositive model maxima. |
| `NSL-RUST-COMP-019` | Additional bound-stage evidence. `R5-S05-BOUND-DIAGNOSTICS`: source cases match codes, messages, positions and snippets; included limits and the approved codeless conversion abort retain their source spans. |

`R5-S05-BOUND-BOUNDARY` in the [boundary tests](tests/bound_boundaries.rs) verifies 4,299/4,300/4,301-digit results and diagnostic boundaries, exact values across `u64::MAX`, much wider nested products, sign handling, missing diagnostic source context, diagnostic priority, unchanged collection/duration declarations, and deterministic repeated analysis. A 512 KiB thread stack handles a binary expression containing 8,192 READ nodes and 2,048 nested foreach frames; test teardown is also iterative.

The model fixture uses a structural postfix representation; expression types/values that do not affect execution counts are reduced to placeholders. Source cases observe actual typed IR passed by the frozen compiler to `StaticBoundAnalyzer`. The nonpositive IR cases inject maxima at this analysis seam because the source parser already forbids them. The generator observes very wide result integers in small decimal chunks without changing Python's integer-conversion setting or the Oracle implementation.

Regenerate from Oracle `28a949ec96581cbdc52c8133527b23bb5b72ee0a` with Python 3.12:

```bash
.venv/bin/python -I -B rust/crates/nsl-compiler/tests/fixtures/generate_bounds.py "$oracle_dir" models > "$oracle_dir/bounds-model-oracle.tsv"
.venv/bin/python -I -B rust/crates/nsl-compiler/tests/fixtures/generate_bounds.py "$oracle_dir" sources > "$oracle_dir/bounds-source-oracle.tsv"
cmp "$oracle_dir/bounds-model-oracle.tsv" rust/crates/nsl-compiler/tests/fixtures/bounds-model-oracle.tsv
cmp "$oracle_dir/bounds-source-oracle.tsv" rust/crates/nsl-compiler/tests/fixtures/bounds-source-oracle.tsv
```

Both fixtures were generated twice; the files and SHA-256 values matched each other and the committed files.

| Output | SHA-256 |
| --- | --- |
| `bounds-model-oracle.tsv` | `7fb7491e3594b1ef58555a1a32a973dce98c952c2a1bcadc5d5b14af171537f0` |
| `bounds-source-oracle.tsv` | `a3537979ca66c8efc635bbbca0cc16419cd22c6bb3fcc8c7f5481d86445b9dbf` |

Pinned Rust 1.99.1 passes fmt, locked workspace check, all-target/all-feature Clippy with warnings denied, the architecture guard and its 7 tests, 100 Rust tests, and a frozen workspace build. The mandatory Python gate ran once across R5-S05, before its decision checkpoint: 2,333 passed, statement 99.29%, branch 98.14%; Python production sources are unchanged.

Using the same pinned LLVM coverage method as R5-S04, Rust workspace executable-line coverage (the statement figure) is **99.18%** (5,338/5,382), and branch coverage is **98.87%** (785/794). Compiler coverage is 99.29%/98.39%. The new bound modules are **100.00%/100.00%** (190/190 executable lines and 12/12 branches).

## R5-S06 Approval binding analysis

`ApprovalBindingAnalyzer::new(sources).analyze(&skill)` runs on a parsed/composed AST before ordinary value-name/type analysis. NSL 0.1 returns an empty analysis. NSL 0.2 returns immutable views of approval declarations and their WRITE bindings. Each declaration reuses the existing `ApprovalSymbol` and independent `ApprovalSymbolId` counter, plus its event order. Each binding records that approval ID, the WRITE's arena-local `ExpressionId`, its event order, and whether it occurs inside a foreach body. Analysis results and their arena IDs belong to the originating skill.

Approval declarations and writes are visited in the Oracle's preorder: a WRITE event precedes its argument events; arguments, binary operands, statements, and EMIT fields retain source order. Only Approval/WRITE events increment the one-based order. A foreach collection is visited before its body and retains the enclosing scope flag. The body and its descendants are inside foreach; a nested foreach's collection is therefore inside its enclosing foreach body.

The analyzer validates in three steps:

1. Collect all declarations in event order. An approval inside foreach produces `NSL-E5305` at its ID token; a duplicate skill-scope ID produces `NSL-E2002` at the second ID token. Declaration errors take precedence over every WRITE-reference error, including errors in earlier source text.
2. Resolve WRITE approvals in event order. Unknown IDs produce `NSL-E5301`, declarations that do not precede their WRITE produce `NSL-E5302`, and a second WRITE using an already-linked approval produces `NSL-E5303`. These errors point at the WRITE's `approved_by` ID token. Lookup uses only the approval namespace; INPUT/CONTEXT/VARIABLE/ITERATOR/CHECK names do not declare approvals.
3. Reject the first unlinked approval in declaration order with `NSL-E5304` at its declaration ID. Each successful declaration is linked to exactly one WRITE.

The new compiler-internal `ApprovalTable` owns namespace lookup, duplicate rejection and sequential ID allocation for both this stage and the existing symbol resolver. Its behavior-preserving extraction passed all 100 existing Rust tests before and after refactoring. The symbol resolver retains its existing declaration-only scope; approval linkage diagnostics belong to this new stage. Each Rust analyzer call uses a fresh local table, preventing failed or previous skill analyses from carrying names, counters or foreach state into later calls. This matches the frozen compiler's fresh analyzer per compilation.

Diagnostics reuse `SourceDiagnosticContext` and retain full spans, logical paths, source positions and snippets. Invalid arena references return typed `ApprovalAnalysisError` values. Traversal uses an explicit work stack, including deeply nested calls and loops. WRITE count, prohibition of WRITE inside foreach and Tool Contract safety remain R5-S07; complete lowering/public compiler integration remains R5-S08. Thus two distinct approvals linked to two writes can pass this stage, as in the Oracle, before the later strict WRITE-count check.

| Rust SRS | Status, verification ID, and evidence in R5-S06 |
| --- | --- |
| `NSL-RUST-COMP-010` | Implemented in the approval-binding stage. `R5-S06-APPROVAL-ORACLE`: [Oracle differential tests](tests/approval_oracle.rs) verify declared approval lookup, independent namespace/IDs, and skill-scope declarations. |
| `NSL-RUST-COMP-011` | Implemented. `R5-S06-APPROVAL-ORDER`: the differential cases verify declaration-before-WRITE, preorder for nested writes, event-only ordering and collection/body scope flags. |
| `NSL-RUST-COMP-012` | Implemented in this stage. `R5-S06-APPROVAL-LINKS`: duplicate declarations, unknown/unused approvals and repeated WRITE use are rejected; the AST/grammar has one explicit approval ID per WRITE. |
| `NSL-RUST-COMP-019` | Additional approval-stage evidence. `R5-S06-APPROVAL-DIAGNOSTICS`: 385 frozen cases compare codes, messages, positions, full spans and snippets, including declaration/reference/unused-error priority. The generator also checks that rejected ordinary source cases produce the same first error through the Oracle's full compiler. |

The fixture includes all 341 sequences of zero through four events drawn from declare A, declare B, write A and write B, plus targeted namespace, scope, nesting, Unicode source/snippet/path, case-sensitive ID, NSL 0.1 and missing-context cases. One explicit model case changes a parsed NSL 0.2 AST to version 0.1 to verify that the analyzer bypasses approval nodes for 0.1; it does not add 0.2 grammar to the 0.1 parser. Successful standalone analysis cases intentionally include shapes rejected by later typing or WRITE safety.

`R5-S06-APPROVAL-BOUNDARY` in the [boundary tests](tests/approval_boundaries.rs) verifies allocation compatibility with the existing symbol resolver, missing source context, error chaining, invalid statement references, isolation between skills and error paths, 4,096 nested foreach/call constructs and a chain of 8,192 binary operators on a 512 KiB stack, and 0/1/2/9,999/10,000/10,001 approval pairs. Opaque expression IDs cannot be forged into parser-created AST nodes; the defensive expression-error public contract is also checked.

Regenerate with Python 3.12 from the same frozen Oracle checkout:

```bash
.venv/bin/python -I -B rust/crates/nsl-compiler/tests/fixtures/generate_approvals.py "$oracle_dir" > "$oracle_dir/approval-oracle.tsv"
cmp "$oracle_dir/approval-oracle.tsv" rust/crates/nsl-compiler/tests/fixtures/approval-oracle.tsv
```

Two generations matched byte-for-byte and matched the committed fixture. SHA-256: `f9a6e09728076959289cc0da170a074d50d899f4bf3389ad3b21ba19166a1da1`.

Pinned Rust 1.99.1 passes fmt, locked workspace check, all-target/all-feature Clippy with warnings denied, the architecture guard and its 7 tests, 107 Rust tests, and a frozen build. Dependencies and `Cargo.lock` are unchanged. The mandatory Python gate ran once: 2,333 passed, statement 99.29%, branch 98.14%.

The same pinned LLVM method reports workspace executable-line coverage (the statement figure) **99.22%** (5,568/5,612) and branch coverage **98.89%** (805/814). Compiler coverage is 99.39%/98.54%. The new approval modules are **100.00%/100.00%** (210/210 lines, 18/18 branches); the common approval table is **100.00%/100.00%** (26/26 lines, 2/2 branches).
