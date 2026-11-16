# NSL source model and language header

`SourceFile::from_bytes` validates UTF-8 and strips one leading BOM, following the Oracle's `utf-8-sig` entry point. `from_text` preserves the supplied text, including any BOM. The logical path retains its spelling and must end in `.ns`. The derived `SourceId` is `source:` followed by the lowercase SHA-256 digest of the UTF-8 path, a zero byte, and the decoded source text. A supplied nonempty SourceId is preserved. The anonymous text entry point uses `memory:root` and `memory/root.ns`.

`SourcePosition` uses a zero-based Unicode scalar offset and one-based line and column numbers. LF advances the line. CR and tab each advance the column. `SourceFile::span_at_bytes` translates UTF-8 byte ranges to half-open, source-identified spans. Empty spans, including EOF, are valid.

`LanguageHeaderScanner` reads the root declaration for versions `0.1` and `0.2` within the Oracle's 4,096-byte scan bound. It preserves the declaration and quoted-version spans. Its errors carry an Oracle code (`NSL-E1004`–`NSL-E1009`), source identity and location. The display text uses fixed messages without embedding source text.

| Rust SRS | R4-S01 evidence |
| --- | --- |
| `NSL-RUST-SRC-001`, `002`, `007` | [`header_version_spans_and_diagnostics_match_frozen_oracle`](tests/source_oracle.rs) verifies 33 leading header, diagnostic, and scan-bound cases. |
| `NSL-RUST-SRC-005` | `utf8_byte_ranges_have_oracle_scalar_spans` compares 14 token and EOF positions with the Oracle lexer. The constructor test also checks invalid byte boundaries, coordinates, and reversed spans. |
| `NSL-RUST-SRC-006` | `utf8_validation_and_source_identity_match_frozen_oracle` compares 25 inputs including BOMs, multibyte text, malformed UTF-8, and logical path errors. |

Regenerate the fixture from the repository root with Python 3.12.

```bash
oracle_dir=$(mktemp -d)
git archive 28a949ec96581cbdc52c8133527b23bb5b72ee0a nsl | tar -x -C "$oracle_dir"
.venv/bin/python -I -B rust/crates/nsl-source/tests/fixtures/generate_source.py "$oracle_dir" > "$oracle_dir/source-oracle.tsv"
cmp "$oracle_dir/source-oracle.tsv" rust/crates/nsl-source/tests/fixtures/source-oracle.tsv
```

## Version-aware lexer

`Lexer::scan` takes an explicit language version and collects recoverable lexical diagnostics. `Lexer::tokenize` returns the first ordinary diagnostic if scanning completes. `Lexer::tokenize_root` obtains the version from the root header first. NSL 0.2 reserves `write`, `approve`, and `approved_by`. NSL 0.1 treats these words as identifiers. Tokens carry their kind, normalized value, original lexeme, and half-open `SourceSpan` with EOF at the final source position.

Numeric tokens follow Python 3.12's Unicode 15.0 `str.isdigit()` classification. Duration conversion accepts Unicode decimal digits and permits at most 4,300 input digits, including leading zeros. The resulting milliseconds are stored as a canonical base-10 string so multiplication can produce the exact value at the input limit. The Oracle raises `ValueError` for `²s` and over-limit durations. Rust returns a fatal `DurationError` with the literal's span and no NSL diagnostic code, even if an ordinary diagnostic occurred earlier. Plain integer and decimal tokens retain their input spelling.

| Rust SRS | Status and verification in R4-S02 |
| --- | --- |
| `NSL-RUST-SRC-003` | Implemented. [Lexer Oracle differential test](tests/lexer_oracle.rs) compares 58 cases across both language versions, including keyword handling, token values and lexemes, diagnostic codes, and duration failures. |
| `NSL-RUST-SRC-005` | Verified for Lexer tokens and diagnostics. The same differential test compares source IDs, Unicode scalar coordinates, and EOF spans. |

Regenerate the Lexer fixture and Unicode digit tables with the same Python 3.12 environment and Oracle checkout used above.

```bash
.venv/bin/python -I -B rust/crates/nsl-source/tests/fixtures/generate_lexer.py "$oracle_dir" > "$oracle_dir/lexer-oracle.tsv"
.venv/bin/python -I -B rust/crates/nsl-source/tests/fixtures/generate_lexer.py --digit-tables > "$oracle_dir/unicode_digits.rs"
cmp "$oracle_dir/lexer-oracle.tsv" rust/crates/nsl-source/tests/fixtures/lexer-oracle.tsv
cmp "$oracle_dir/unicode_digits.rs" rust/crates/nsl-source/src/lexer/unicode_digits.rs
```

## Parser and AST

`Parser::parse(&source, mode, limits)` takes a `SourceFile`, an explicit `ParseMode`, and `ParseLimits { max_nesting }`. Root mode validates the required header and uses its version. Fragment mode uses the version supplied by the caller and accepts include, requires, context, and limits declarations. The AST retains declaration and argument order, the full spans of WRITE and approval nodes, and the individual approval reference and declaration ID spans.

The nesting budget counts simultaneously open grouping parentheses, calls (including READ and WRITE), `List` wrappers, and `foreach` statements, starting with the collection expression. A budget of zero permits flat syntax and binary operator chains. The first opening beyond the budget returns `NestingError` with that token's span. Heap-backed parser stacks and indexed AST children allow deeply nested inputs within the requested budget without recursive parsing or destruction.

Integer conversions in expressions, limits, and `foreach max` accept Unicode decimal digits and up to 4,300 input digits, including leading zeros. The AST stores exact integers and decimals using Core types. Decimal source literals preserve scale and precision, and converted duration milliseconds may exceed 4,300 digits. `NumericError` reports integer or decimal conversion failures with the original token's span. Both `NumericError` and `NestingError` carry no NSL diagnostic code. Ordinary parser diagnostics preserve the Oracle code and token start position.

| Rust SRS | Status and verification in R4-S03 |
| --- | --- |
| `NSL-RUST-SRC-004` | Implemented. [Parser Oracle differential test](tests/parser_oracle.rs) compares 122 cases including full AST projections, malformed productions, explicit modes, and numeric boundaries across NSL 0.1 and 0.2. |
| `NSL-RUST-SRC-005` | Verified for Parser nodes and diagnostics. The same test compares node and metadata spans and diagnostic positions. |
| `NSL-RUST-SRC-013` | Boundary evidence. [Parser boundary tests](tests/parser_boundaries.rs) verify exact nesting rejection spans, level reuse, and deeply nested input on a small thread stack. |

Regenerate the Parser fixture with the same Python 3.12 environment and Oracle checkout.

```bash
.venv/bin/python -I -B rust/crates/nsl-source/tests/fixtures/generate_parser.py "$oracle_dir" > "$oracle_dir/parser-oracle.tsv"
cmp "$oracle_dir/parser-oracle.tsv" rust/crates/nsl-source/tests/fixtures/parser-oracle.tsv
```

## R4 exit gate

Run the source and compiler integration targets from the Rust workspace using the pinned toolchain. The workspace quality workflow runs these targets on Linux and Windows.

```bash
cargo test --locked -p nsl-source -p nsl-compiler --all-targets
cargo test --locked -p nsl-source --test r4_fuzz -- --nocapture
```

| Exit criterion | R4-S05 evidence |
| --- | --- |
| Source differential | Regenerate and compare the source and lexer fixtures against Oracle `28a949e` using the commands above. The Rust targets verify UTF-8 admission, header codes and spans, and 58 lexer cases across NSL 0.1 and 0.2. [Source boundary tests](tests/r4_source_boundaries.rs) also cover invalid bytes after the header window and a 64 KiB UTF-8 comment. |
| Parser differential | Regenerate the 122-case Parser fixture from the same Oracle and compare the complete semantic AST projections and diagnostics. The source boundary tests check unclosed groups, calls, List types, and foreach blocks at 4,096 levels. |
| Include security | Regenerate and compare the 94-case [Include fixture](../nsl-compiler/README.md). The [Include gate tests](../nsl-compiler/tests/r4_include_gate.rs) exercise the default file and byte budgets at their boundaries and reject a too-deep reference before resolver access, even when the target was already visited. |
| Parser bounded fuzz | [`r4_fuzz`](tests/r4_fuzz.rs) runs 512 rounds with each of four fixed seeds. Each round exercises raw bytes, a mutated fragment, and a mutated skill body under a versioned header. The 6,144 inputs are at most 512 bytes each. The target checks byte admission, token and diagnostic spans, scan/tokenize agreement, parser determinism, and structured rejections. A panic or crash fails the test. |

`NSL-RUST-SRC-013` and `NSL-RUST-SRC-014` are verified for this bounded source campaign. Include security evidence for `NSL-RUST-SRC-010` and `NSL-RUST-SRC-011` includes the default resource limits and previously visited targets. The source portion of `NSL-RUST-QUAL-009` has a test target. This run supplies the source campaign evidence for `NSL-RUST-QUAL-010`.
