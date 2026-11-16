use nsl_source::{
    HeaderErrorKind, LanguageHeaderScanner, SourceError, SourceFile, SourceId,
    SourceLanguageVersion, SourcePosition, SourceSpan,
};

const ORACLE: &str = include_str!("fixtures/source-oracle.tsv");

fn rows() -> impl Iterator<Item = Vec<&'static str>> {
    ORACLE
        .lines()
        .skip(1)
        .map(|line| line.split('\t').collect())
}

fn bytes(hex: &str) -> Vec<u8> {
    if hex == "-" {
        return Vec::new();
    }
    assert!(hex.len().is_multiple_of(2));
    (0..hex.len())
        .step_by(2)
        .map(|start| u8::from_str_radix(&hex[start..start + 2], 16).unwrap())
        .collect()
}

fn text(hex: &str) -> String {
    String::from_utf8(bytes(hex)).unwrap()
}

fn position(input: &str) -> SourcePosition {
    let numbers: Vec<usize> = input.split(',').map(|part| part.parse().unwrap()).collect();
    let [offset, line, column] = numbers.as_slice() else {
        panic!("invalid fixture position: {input}");
    };
    SourcePosition::new(*offset, *line, *column).unwrap()
}

fn load_source(path: &str, payload: &str, requested: &str) -> Result<SourceFile, SourceError> {
    let requested = (requested != "none").then(|| text(requested));
    SourceFile::from_bytes(&text(path), &bytes(payload), requested.as_deref())
}

#[test]
fn utf8_validation_and_source_identity_match_frozen_oracle() {
    for row in rows() {
        match row.as_slice() {
            [
                "source",
                label,
                path,
                payload,
                requested,
                expected,
                id,
                encoding,
            ] => {
                let source = load_source(path, payload, requested).unwrap();
                assert_eq!(source.logical_path(), text(path), "{label}");
                assert_eq!(source.text(), text(expected), "{label}");
                assert_eq!(source.source_id().as_str(), *id, "{label}");
                assert_eq!(source.encoding(), *encoding, "{label}");
            }
            ["source_error", label, path, payload, requested, message] => {
                let error = load_source(path, payload, requested).unwrap_err();
                assert!(matches!(
                    error,
                    SourceError::InvalidLogicalPath | SourceError::InvalidUtf8
                ));
                assert_eq!(error.to_string(), *message, "{label}");
            }
            _ if matches!(row[0], "span" | "header" | "header_error") => {}
            _ => panic!("invalid fixture row: {row:?}"),
        }
    }
}

#[test]
fn utf8_byte_ranges_have_oracle_scalar_spans() {
    for row in rows().filter(|row| row[0] == "span") {
        let [
            _,
            source_text,
            start,
            end,
            expected_start,
            expected_end,
            id,
            path,
        ] = row.as_slice()
        else {
            panic!("invalid span row: {row:?}");
        };
        let source = SourceFile::memory(&text(source_text));
        let start = start.parse::<usize>().unwrap();
        let end = end.parse::<usize>().unwrap();
        let actual = source.span_at_bytes(start..end).unwrap();
        assert_eq!(source.logical_path(), *path);
        assert_eq!(actual.source_id().as_str(), *id);
        assert_eq!(actual.start(), position(expected_start));
        assert_eq!(actual.end(), position(expected_end));
        assert_eq!(
            actual.start().offset(),
            source.text()[..start].chars().count()
        );
        assert_eq!(source.position_at_byte(end), Ok(actual.end()));
    }
}

#[test]
fn header_version_spans_and_diagnostics_match_frozen_oracle() {
    for row in rows().filter(|row| matches!(row[0], "header" | "header_error")) {
        let [
            kind,
            label,
            mode,
            prefix,
            unit,
            repetitions,
            tail,
            expected @ ..,
        ] = row.as_slice()
        else {
            panic!("invalid header row: {row:?}");
        };
        let text = text(prefix) + &text(unit).repeat(repetitions.parse().unwrap()) + &text(tail);
        let source = match *mode {
            "text" => SourceFile::from_text("header.ns", &text, None),
            "bytes" => SourceFile::from_bytes("header.ns", text.as_bytes(), None),
            _ => panic!("invalid header source mode: {mode}"),
        }
        .unwrap();
        let result = LanguageHeaderScanner::scan(&source);
        match (*kind, expected) {
            ("header", [version, start, end, version_start, version_end]) => {
                let header = result.unwrap_or_else(|error| panic!("{label}: {error}"));
                assert_eq!(header.version.as_str(), *version, "{label}");
                assert_eq!(header.span.source_id(), source.source_id(), "{label}");
                assert_eq!(
                    header.version_span.source_id(),
                    source.source_id(),
                    "{label}"
                );
                assert_eq!(header.span.start(), position(start), "{label}");
                assert_eq!(header.span.end(), position(end), "{label}");
                assert_eq!(
                    header.version_span.start(),
                    position(version_start),
                    "{label}"
                );
                assert_eq!(header.version_span.end(), position(version_end), "{label}");
            }
            ("header_error", [code, location]) => {
                let error = result.expect_err(label);
                assert_eq!(error.source_id, *source.source_id(), "{label}");
                assert_eq!(error.kind.code(), *code, "{label}");
                assert_eq!(error.position, position(location), "{label}");
                assert!(error.to_string().starts_with(code), "{label}");
            }
            _ => panic!("invalid header outcome: {row:?}"),
        }
    }
}

#[test]
fn invalid_source_coordinates_and_paths_remain_typed_errors() {
    let id = SourceId::new("source:explicit").unwrap();
    let source = SourceFile::new(id.clone(), "example.ns", "é\n").unwrap();
    assert_eq!(source.source_id(), &id);
    assert_eq!(SourceId::new(""), Err(SourceError::MissingSourceId));
    assert_eq!(
        SourcePosition::new(0, 0, 1),
        Err(SourceError::InvalidPosition)
    );
    assert_eq!(
        SourcePosition::new(0, 1, 0),
        Err(SourceError::InvalidPosition)
    );
    assert_eq!(
        source.position_at_byte(1),
        Err(SourceError::InvalidByteOffset)
    );
    assert_eq!(
        source.position_at_byte(4),
        Err(SourceError::InvalidByteOffset)
    );
    assert_eq!(
        source.span_at_bytes(0..1),
        Err(SourceError::InvalidByteOffset)
    );
    let reversed_start = 2;
    assert_eq!(
        source.span_at_bytes(reversed_start..0),
        Err(SourceError::ReversedSpan)
    );

    let start = SourcePosition::new(0, 1, 1).unwrap();
    let end = SourcePosition::new(1, 1, 2).unwrap();
    assert_eq!(
        SourceSpan::new(id.clone(), end, start),
        Err(SourceError::ReversedSpan)
    );
    assert_eq!(SourceSpan::new(id, start, start).unwrap().end(), start);
    for (error, message) in [
        (SourceError::MissingSourceId, "source_id is required"),
        (
            SourceError::InvalidPosition,
            "source line and column must be positive",
        ),
        (
            SourceError::InvalidByteOffset,
            "source byte offset must be on a UTF-8 character boundary",
        ),
        (
            SourceError::ReversedSpan,
            "source span end must not precede start",
        ),
    ] {
        assert_eq!(error.to_string(), message);
    }
}

#[test]
fn unsupported_version_diagnostic_does_not_echo_the_source() {
    let source = SourceFile::memory("language NSL \"fixture text\";");
    let error = LanguageHeaderScanner::scan(&source).unwrap_err();
    assert_eq!(error.kind, HeaderErrorKind::UnsupportedVersion);
    assert_eq!(
        error.to_string(),
        "NSL-E1006 at 1:14: unsupported NSL version"
    );
    assert_eq!(LanguageHeaderScanner::MAX_HEADER_BYTES, 4096);
    assert_eq!(
        LanguageHeaderScanner::scan(&SourceFile::memory("language NSL \"0.2\";"))
            .unwrap()
            .version,
        SourceLanguageVersion::V0_2
    );
}
