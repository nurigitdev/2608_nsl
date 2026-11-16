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
