use nsl_core::{REDACTED, redact_text};
fn decode(value: &str) -> String {
    if value == "-" {
        return String::new();
    }
    String::from_utf8(
        (0..value.len())
            .step_by(2)
            .map(|i| u8::from_str_radix(&value[i..i + 2], 16).unwrap())
            .collect(),
    )
    .unwrap()
}
#[test]
fn frozen_redaction_rules_match_for_unicode_quotes_patterns_and_generated_combinations() {
    let mut count = 0;
    for row in include_str!("fixtures/redaction-oracle.tsv")
        .lines()
        .filter(|r| !r.starts_with('#'))
    {
        let c = row.split('\t').collect::<Vec<_>>();
        let input = decode(c[1]);
        let expected = decode(c[2]);
        assert_eq!(
            redact_text(&input),
            expected,
            "case {} input {input:?}",
            c[0]
        );
        count += 1;
    }
    assert_eq!(count, 9846);
    assert_eq!(REDACTED, "[REDACTED]");
}
#[test]
fn bounded_size_extremes_keep_plain_unicode_and_multiple_independent_matches() {
    for count in [0, 1, 4095, 4096, 4097] {
        let input = "가😀".repeat(count);
        assert_eq!(redact_text(&input), input);
        let input = "Bearer synthetic; ".repeat(count);
        assert_eq!(redact_text(&input), "Bearer [REDACTED]; ".repeat(count));
    }
}
