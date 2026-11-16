use nsl_source::{
    DurationError, DurationErrorKind, Lexer, LexerError, SourceFile, SourceLanguageVersion,
    SourcePosition, Token, TokenValue,
};
use sha2::{Digest, Sha256};

const ORACLE: &str = include_str!("fixtures/lexer-oracle.tsv");

fn decode_text(hex: &str) -> String {
    if hex == "-" {
        return String::new();
    }
    assert!(hex.len().is_multiple_of(2));
    let bytes = (0..hex.len())
        .step_by(2)
        .map(|start| u8::from_str_radix(&hex[start..start + 2], 16).unwrap())
        .collect();
    String::from_utf8(bytes).unwrap()
}

fn position(input: &str) -> SourcePosition {
    let numbers: Vec<usize> = input.split(',').map(|part| part.parse().unwrap()).collect();
    let [offset, line, column] = numbers.as_slice() else {
        panic!("invalid fixture position: {input}");
    };
    SourcePosition::new(*offset, *line, *column).unwrap()
}

fn assert_field(actual: &str, expected: &str, label: &str) {
    if let Some(expected_hash) = expected.strip_prefix("sha256:") {
        let hash: String = Sha256::digest(actual.as_bytes())
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect();
        assert_eq!(hash, expected_hash, "{label}");
    } else {
        assert_eq!(actual, decode_text(expected), "{label}");
    }
}

fn assert_tokens(tokens: &[Token<'_>], rows: &[&[&str]], source_id: &str, label: &str) {
    let expected: Vec<_> = rows.iter().filter(|row| row[0] == "token").collect();
    assert_eq!(tokens.len(), expected.len(), "{label}");
    for (token, row) in tokens.iter().zip(expected) {
        let ["token", kind, value_kind, value, lexeme, start, end] = **row else {
            panic!("invalid token row: {row:?}");
        };
        assert_eq!(token.kind.as_str(), kind, "{label}");
        let (actual_kind, actual_value) = match &token.value {
            TokenValue::Text(text) => ("text", text.as_ref()),
            TokenValue::Boolean(true) => ("bool", "true"),
            TokenValue::Boolean(false) => ("bool", "false"),
            TokenValue::DurationMilliseconds(text) => ("milliseconds", text.as_str()),
        };
        assert_eq!(actual_kind, value_kind, "{label}");
        assert_field(actual_value, value, label);
        assert_field(token.lexeme, lexeme, label);
        assert_eq!(token.span.source_id().as_str(), source_id, "{label}");
        assert_eq!(token.span.start(), position(start), "{label}");
        assert_eq!(token.span.end(), position(end), "{label}");
    }
}

fn assert_duration_error(error: &DurationError, kind: &str, source: &SourceFile, label: &str) {
    let (expected_kind, message) = match kind {
        "non_decimal_digit" => (
            DurationErrorKind::NonDecimalDigit,
            "duration contains a non-decimal digit",
        ),
        "too_many_digits" => (
            DurationErrorKind::TooManyDigits,
            "duration exceeds 4300 decimal digits",
        ),
        _ => panic!("invalid duration error kind: {kind}"),
    };
    assert_eq!(error.kind, expected_kind, "{label}");
    assert_eq!(error.to_string(), message, "{label}");
    assert_eq!(error.span.source_id(), source.source_id(), "{label}");
}

fn assert_scan(
    source: &SourceFile,
    version: SourceLanguageVersion,
    rows: &[&[&str]],
    source_id: &str,
    label: &str,
) {
    if let [["duration_error", kind]] = rows {
        let error = Lexer::scan(source, version).unwrap_err();
        assert_duration_error(&error, kind, source, label);
        assert_eq!(
            Lexer::tokenize(source, version),
            Err(LexerError::Duration(error)),
            "{label}"
        );
        return;
    }

    let result = Lexer::scan(source, version).unwrap();
    assert_tokens(&result.tokens, rows, source_id, label);
    let diagnostics: Vec<_> = rows.iter().filter(|row| row[0] == "diag").collect();
    assert_eq!(result.diagnostics.len(), diagnostics.len(), "{label}");
    for (diagnostic, row) in result.diagnostics.iter().zip(diagnostics) {
        let ["diag", code, coords] = **row else {
            panic!("invalid diagnostic row: {row:?}");
        };
        assert_eq!(diagnostic.kind.code(), code, "{label}");
        assert_eq!(diagnostic.position, position(coords), "{label}");
        assert_eq!(diagnostic.source_id.as_str(), source_id, "{label}");
    }
    let expected = result
        .diagnostics
        .into_iter()
        .next()
        .map_or(Ok(result.tokens), |error| {
            Err(LexerError::Diagnostic(error))
        });
    assert_eq!(Lexer::tokenize(source, version), expected, "{label}");
}

fn assert_root(source: &SourceFile, rows: &[&[&str]], source_id: &str, label: &str) {
    use std::error::Error;

    let actual = Lexer::tokenize_root(source);
    match rows {
        [["root_error", code, coords]] => {
            let error = actual.unwrap_err();
            let (actual_code, actual_position, actual_source_id) = match &error {
                LexerError::Header(error) => (error.kind.code(), error.position, &error.source_id),
                LexerError::Diagnostic(error) => {
                    (error.kind.code(), error.position, &error.source_id)
                }
                LexerError::Duration(error) => panic!("{label}: unexpected duration error {error}"),
            };
            assert_eq!(actual_code, *code, "{label}");
            assert_eq!(actual_position, position(coords), "{label}");
            assert_eq!(actual_source_id.as_str(), source_id, "{label}");
            assert!(error.to_string().contains(code), "{label}");
            assert_eq!(
                error.source().unwrap().to_string(),
                error.to_string(),
                "{label}"
            );
        }
        [["duration_error", kind]] => {
            let error = actual.unwrap_err();
            let LexerError::Duration(duration) = &error else {
                panic!("{label}: expected duration error, got {error:?}");
            };
            assert_duration_error(duration, kind, source, label);
            assert_eq!(error.to_string(), duration.to_string(), "{label}");
            assert_eq!(
                error.source().unwrap().to_string(),
                duration.to_string(),
                "{label}"
            );
        }
        _ => assert_tokens(&actual.unwrap(), rows, source_id, label),
    }
}

#[test]
fn versioned_tokens_spans_diagnostics_and_duration_failures_match_frozen_oracle() {
    let remainder = ORACLE
        .strip_prefix("# Python Oracle 28a949ec96581cbdc52c8133527b23bb5b72ee0a\n")
        .unwrap();
    for case in remainder
        .trim_end()
        .strip_prefix("case\t")
        .unwrap()
        .split("\ncase\t")
    {
        let (metadata, outcomes) = case.split_once('\n').unwrap();
        let fields: Vec<_> = metadata.split('\t').collect();
        let [label, mode, version, prefix, unit, count, tail, source_id] = fields.as_slice() else {
            panic!("invalid fixture case: {metadata}");
        };
        let mut text = decode_text(prefix);
        text.push_str(&decode_text(unit).repeat(count.parse().unwrap()));
        text.push_str(&decode_text(tail));
        let source = SourceFile::from_text("lexer/fixture.ns", &text, None).unwrap();
        assert_eq!(source.source_id().as_str(), *source_id, "{label}");
        let outcomes: Vec<Vec<_>> = outcomes
            .lines()
            .map(|row| row.split('\t').collect())
            .collect();
        let outcomes: Vec<_> = outcomes.iter().map(Vec::as_slice).collect();

        match (*mode, *version) {
            ("scan", "0.1") => assert_scan(
                &source,
                SourceLanguageVersion::V0_1,
                &outcomes,
                source_id,
                label,
            ),
            ("scan", "0.2") => assert_scan(
                &source,
                SourceLanguageVersion::V0_2,
                &outcomes,
                source_id,
                label,
            ),
            ("root", "0.1" | "0.2" | "-") => assert_root(&source, &outcomes, source_id, label),
            _ => panic!("invalid fixture mode or version: {metadata}"),
        }
    }
}

#[test]
fn fatal_duration_error_preserves_only_the_offending_literal_span() {
    let source = SourceFile::memory("@ ②ms write");
    let error = Lexer::scan(&source, SourceLanguageVersion::V0_2).unwrap_err();
    assert_eq!(error.kind, DurationErrorKind::NonDecimalDigit);
    assert_eq!(error.span, source.span_at_bytes(2..7).unwrap());
}
