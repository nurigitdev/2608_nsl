//! Reproducible bounded mutation campaign for the public source front end.

use nsl_source::ast::Ast;
use nsl_source::{
    Lexer, LexerError, ParseError, ParseLimits, ParseMode, Parser, SourceError, SourceFile,
    SourceLanguageVersion, SourcePosition, SourceSpan, TokenKind,
};

const SEEDS: [u64; 4] = [
    0x521e_58b7_045d_9f3b,
    0x0d62_76e8_af38_4271,
    0xeeb3_12d7_4bc1_65a9,
    0x1f53_6c98_e7ab_542d,
];
const ROUNDS: usize = 512;
const MAX_BYTES: usize = 512;
const FRAGMENTS: &[&str] = &[
    "",
    "// 다국어😀\r\n",
    "include \"cafés/a.ns\";",
    "context { cols: List<Money<JPY>> from \"user.cols\"; }",
    "requires { tool write version \"1\"; }",
    "limits { tool_calls 1; loop_iterations 2; emitted_rows 3; collection_size 4; duration ٣s; }",
];
const STATEMENTS: &[&str] = &[
    "let net = (records.sales + 03.50) * 4;",
    "let amount = sum(read Sales.Read(scope: \"부서😀\", page: ๒));",
    "foreach row in rows max １ { emit { out: row.amount; } }",
    "approve CHANGE { message \"review\"; } let done = write Ops.Update(a = 1) approved_by CHANGE;",
    "let approve = write(approved_by);",
    "check REVIEW { assert score >= 3; severity WARN; on_fail CONTINUE; message \"note\"; }",
];
const INSERTIONS: &[&str] = &[
    "\0",
    "\u{feff}",
    "\u{2028}",
    "😀",
    "\u{301}",
    "²s",
    "١.٢",
    "©",
    "true",
    "language NSL ",
    "//",
    "\r\n",
    "\"\\",
    "\"",
    "}",
    ")",
    "(",
    ">",
    ".",
    "=",
    "<eof>",
];

struct Random(u64);

impl Random {
    fn below(&mut self, stop: usize) -> usize {
        self.0 ^= self.0 << 13;
        self.0 ^= self.0 >> 7;
        self.0 ^= self.0 << 17;
        (self.0 % stop as u64) as usize
    }
}

fn mutate(text: &str, random: &mut Random, edits: usize) -> String {
    let mut chars: Vec<_> = text.chars().collect();
    for _ in 0..edits {
        let at = random.below(chars.len() + 1);
        match random.below(3) {
            0 => {
                let end = at + random.below(chars.len() - at + 1);
                chars.drain(at..end);
            }
            1 => {
                let insert = INSERTIONS[random.below(INSERTIONS.len())];
                chars.splice(at..at, insert.chars());
            }
            _ => chars.insert(at, char::from(random.below(128) as u8)),
        }
    }
    chars.into_iter().collect()
}

/// Coordinates are computed directly from UTF-8 before invoking the lexer or parser.
struct SourceView<'a> {
    source: &'a SourceFile,
    positions: Vec<(usize, SourcePosition)>,
}

impl<'a> SourceView<'a> {
    fn new(source: &'a SourceFile) -> Self {
        let (mut line, mut column) = (1, 1);
        let mut positions = Vec::new();
        for (offset, (byte, ch)) in source.text().char_indices().enumerate() {
            positions.push((byte, SourcePosition::new(offset, line, column).unwrap()));
            if ch == '\n' {
                line += 1;
                column = 1;
            } else {
                column += 1;
            }
        }
        positions.push((
            source.text().len(),
            SourcePosition::new(positions.len(), line, column).unwrap(),
        ));
        Self { source, positions }
    }

    fn byte(&self, position: SourcePosition) -> usize {
        let (byte, expected) = self.positions[position.offset()];
        assert_eq!(position, expected);
        byte
    }

    fn lexeme(&self, span: &SourceSpan) -> &str {
        assert_eq!(span.source_id(), self.source.source_id());
        &self.source.text()[self.byte(span.start())..self.byte(span.end())]
    }
}

fn lexer_error(view: &SourceView<'_>, error: &LexerError) {
    match error {
        LexerError::Header(error) => {
            assert_eq!(&error.source_id, view.source.source_id());
            view.byte(error.position);
        }
        LexerError::Diagnostic(error) => {
            assert_eq!(&error.source_id, view.source.source_id());
            view.byte(error.position);
        }
        LexerError::Duration(error) => {
            assert!(!view.lexeme(&error.span).is_empty());
        }
    }
}

fn explore_lexer(view: &SourceView<'_>, version: SourceLanguageVersion) -> bool {
    match Lexer::scan(view.source, version) {
        Ok(scan) => {
            let mut previous = 0;
            let (eof, body) = scan.tokens.split_last().unwrap();
            assert_eq!(eof.kind, TokenKind::Eof);
            assert_eq!(view.byte(eof.span.start()), view.source.text().len());
            assert_eq!(view.lexeme(&eof.span), "");
            for token in body {
                assert_ne!(token.kind, TokenKind::Eof);
                assert!(token.span.start().offset() >= previous);
                assert_eq!(view.lexeme(&token.span), token.lexeme);
                previous = token.span.end().offset();
            }
            for diagnostic in &scan.diagnostics {
                assert_eq!(&diagnostic.source_id, view.source.source_id());
                view.byte(diagnostic.position);
            }
            let tokenization = Lexer::tokenize(view.source, version);
            if let Some(first) = scan.diagnostics.first() {
                assert_eq!(tokenization, Err(LexerError::Diagnostic(first.clone())));
            } else {
                assert_eq!(tokenization.unwrap(), scan.tokens);
                return true;
            }
        }
        Err(error) => {
            assert!(!view.lexeme(&error.span).is_empty());
            assert_eq!(
                Lexer::tokenize(view.source, version),
                Err(LexerError::Duration(error))
            );
        }
    }
    false
}

fn explore_parser(view: &SourceView<'_>, mode: ParseMode, limits: ParseLimits) -> bool {
    let result = Parser::parse(view.source, mode, limits);
    assert_eq!(result, Parser::parse(view.source, mode, limits));
    match result {
        Ok(Ast::RootSkill(ast)) => {
            assert_eq!(mode, ParseMode::RootSkill);
            assert!(!view.lexeme(&ast.span).is_empty());
            assert!(!view.lexeme(&ast.language_version_span).is_empty());
            for &id in &ast.body {
                assert!(!view.lexeme(&ast.statement(id).unwrap().span).is_empty());
            }
        }
        Ok(Ast::IncludeFragment(ast)) => {
            assert!(matches!(mode, ParseMode::IncludeFragment { .. }));
            view.lexeme(&ast.span);
        }
        Err(error) => {
            match error {
                ParseError::Lexer(error) => lexer_error(view, &error),
                ParseError::Diagnostic(error) => {
                    view.lexeme(&error.span);
                }
                ParseError::Numeric(error) => {
                    assert!(!view.lexeme(&error.span).is_empty());
                }
                ParseError::Nesting(error) => {
                    assert_eq!(error.max_nesting, limits.max_nesting);
                    assert!(!view.lexeme(&error.span).is_empty());
                }
            }
            return false;
        }
    }
    true
}

#[derive(Default)]
struct Counts {
    invalid_utf8: usize,
    lex_ok: usize,
    lex_errors: usize,
    parse_ok: usize,
    parse_errors: usize,
}

impl Counts {
    fn exercise(&mut self, bytes: &[u8], max_nesting: usize) {
        assert!(bytes.len() <= MAX_BYTES);
        let source = match SourceFile::from_bytes("fuzz.ns", bytes, Some("fuzz")) {
            Ok(source) => source,
            Err(error) => {
                assert_eq!(error, SourceError::InvalidUtf8);
                assert!(std::str::from_utf8(bytes).is_err());
                self.invalid_utf8 += 1;
                return;
            }
        };
        let view = SourceView::new(&source);
        let limits = ParseLimits { max_nesting };
        for language_version in [SourceLanguageVersion::V0_1, SourceLanguageVersion::V0_2] {
            if explore_lexer(&view, language_version) {
                self.lex_ok += 1;
            } else {
                self.lex_errors += 1;
            }
            self.inspect_parse(
                &view,
                ParseMode::IncludeFragment { language_version },
                limits,
            );
        }
        self.inspect_parse(&view, ParseMode::RootSkill, limits);
    }

    fn inspect_parse(&mut self, view: &SourceView<'_>, mode: ParseMode, limits: ParseLimits) {
        if explore_parser(view, mode, limits) {
            self.parse_ok += 1;
        } else {
            self.parse_errors += 1;
        }
    }
}

#[test]
fn bounded_source_lexer_parser_campaign() {
    let mut counts = Counts::default();
    for seed in SEEDS {
        let mut random = Random(seed);
        for round in 0..ROUNDS {
            let byte_count = random.below(MAX_BYTES + 1);
            let raw: Vec<_> = (0..byte_count).map(|_| random.below(256) as u8).collect();
            counts.exercise(&raw, 4);

            let text = FRAGMENTS[random.below(FRAGMENTS.len())];
            let changes = random.below(9);
            counts.exercise(mutate(text, &mut random, changes).as_bytes(), round % 5);

            let version = if round % 2 == 0 { "0.1" } else { "0.2" };
            let stmt = STATEMENTS[random.below(STATEMENTS.len())];
            let changes = random.below(9);
            let stmt = mutate(stmt, &mut random, changes);
            let root = format!(
                "language NSL \"{version}\"; skill T {{version \"1\"; risk READ_ONLY; include \"base.ns\"; {stmt}}}"
            );
            counts.exercise(root.as_bytes(), round % 9);
        }
    }

    let cases = SEEDS.len() * ROUNDS * 3;
    assert_eq!(
        counts.lex_ok + counts.lex_errors,
        (cases - counts.invalid_utf8) * 2
    );
    assert_eq!(
        counts.parse_ok + counts.parse_errors,
        (cases - counts.invalid_utf8) * 3
    );
    assert!(counts.invalid_utf8 > 0 && counts.lex_ok > 0 && counts.lex_errors > 0);
    assert!(counts.parse_ok > 0 && counts.parse_errors > 0);
    println!(
        "{cases} source inputs, {} invalid UTF-8, {} lexer accepts, {} lexer rejections, {} parser accepts, {} parser rejections",
        counts.invalid_utf8, counts.lex_ok, counts.lex_errors, counts.parse_ok, counts.parse_errors
    );
}
