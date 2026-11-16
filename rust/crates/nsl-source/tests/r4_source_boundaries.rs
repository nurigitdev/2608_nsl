use nsl_source::ast::Ast;
use nsl_source::{
    HeaderErrorKind, LanguageHeaderScanner, ParseDiagnosticKind, ParseError, ParseLimits,
    ParseMode, Parser, SourceError, SourceFile,
};

fn parse(source: &SourceFile, max_nesting: usize) -> Result<Ast, ParseError> {
    Parser::parse(source, ParseMode::RootSkill, ParseLimits { max_nesting })
}

#[test]
fn malformed_utf8_is_rejected_even_beyond_the_header_window() {
    let header = b"\xef\xbb\xbflanguage NSL \"0.2\"; //";
    for raw in [
        b"\x80".as_slice(),
        b"\xc0\xaf",
        b"\xed\xa0\x80",
        b"\xf4\x90\x80\x80",
        b"\xf0\x9f\xa6",
    ] {
        let mut bytes = header.to_vec();
        bytes.resize(LanguageHeaderScanner::MAX_HEADER_BYTES + 1, b' ');
        bytes.extend_from_slice(raw);
        assert_eq!(
            SourceFile::from_bytes("root.ns", &bytes, None),
            Err(SourceError::InvalidUtf8)
        );
    }
}

#[test]
fn large_utf8_header_trivia_respects_the_byte_window() {
    let cap = LanguageHeaderScanner::MAX_HEADER_BYTES;
    let declaration = "language NSL \"0.2\";";
    let inside = format!("//{}", "😀".repeat((cap - declaration.len() - 4) / 4));
    for offset in [cap - 1, cap, cap + 1] {
        let spaces = offset - inside.len() - declaration.len() - 1;
        let text = format!("{inside}{}\n{declaration}", " ".repeat(spaces));
        let source = SourceFile::memory(&text);
        assert_eq!(source.text().len(), offset);
        let header = LanguageHeaderScanner::scan(&source);
        if offset <= cap {
            assert_eq!(
                header.unwrap().span.end(),
                source.position_at_byte(offset).unwrap()
            );
        } else {
            let error = header.unwrap_err();
            assert_eq!(error.kind, HeaderErrorKind::Limit);
            assert_eq!(error.position, source.position_at_byte(offset).unwrap());
        }
    }

    let large_comment = format!("//{}\n", "🦊".repeat(16_384));
    let before = SourceFile::memory(&format!("{large_comment}{declaration}"));
    let err = LanguageHeaderScanner::scan(&before).unwrap_err();
    assert_eq!(err.kind, HeaderErrorKind::Limit);
    assert_eq!(err.position, before.position_at_byte(cap + 2).unwrap());

    let valid = SourceFile::memory(&format!(
        "{declaration}{large_comment}skill T {{ version \"1\"; risk READ_ONLY; include \"a.ns\"; }}"
    ));
    assert!(matches!(parse(&valid, 0), Ok(Ast::RootSkill(_))));
}

#[test]
fn deeply_unclosed_constructs_return_limits_or_parser_diagnostics() {
    let depth = 4096;
    let prefix =
        "language NSL \"0.2\"; skill T { version \"1\"; risk READ_ONLY; include \"a.ns\"; ";
    for (lead, opening, tail, code) in [
        ("let x = ", "(", "1;}", ParseDiagnosticKind::ExpectedToken),
        ("let x = ", "f(", "1;}", ParseDiagnosticKind::ExpectedToken),
        (
            "input { x: ",
            "List<",
            "Int;}}",
            ParseDiagnosticKind::ExpectedToken,
        ),
        (
            "",
            "foreach x in xs max 1 {",
            "let y = x;}",
            ParseDiagnosticKind::UnexpectedStatement,
        ),
    ] {
        let source = SourceFile::memory(&format!("{prefix}{lead}{}{tail}", opening.repeat(depth)));
        let error = parse(&source, 64).unwrap_err();
        let ParseError::Nesting(error) = error else {
            panic!("expected nesting limit");
        };
        let at = prefix.len() + lead.len() + 64 * opening.len();
        let (skip, width) = match opening {
            "f(" => (1, 1),
            "List<" => (0, 4),
            "foreach x in xs max 1 {" => (0, 7),
            _ => (0, 1),
        };
        assert_eq!(
            error.span,
            source.span_at_bytes(at + skip..at + skip + width).unwrap()
        );

        let error = parse(&source, depth).unwrap_err();
        let ParseError::Diagnostic(error) = error else {
            panic!("expected syntax diagnostic");
        };
        assert_eq!(error.kind, code);
        let expected = if code == ParseDiagnosticKind::UnexpectedStatement {
            source.text().len()
        } else {
            source.text().rfind(';').unwrap()
        };
        assert_eq!(
            error.span.start(),
            source.position_at_byte(expected).unwrap()
        );
    }
}
