use nsl_core::{UtcInstant, UtcInstantError};
#[test]
fn utc_civil_and_fraction_boundaries_reject_invalid_fields_without_panics() {
    for (year, month, day, hour, minute, second, micro) in [
        (0, 1, 1, 0, 0, 0, 0),
        (10000, 1, 1, 0, 0, 0, 0),
        (2026, 0, 1, 0, 0, 0, 0),
        (2026, 13, 1, 0, 0, 0, 0),
        (2026, 1, 0, 0, 0, 0, 0),
        (2026, 2, 29, 0, 0, 0, 0),
        (1900, 2, 29, 0, 0, 0, 0),
        (2000, 2, 30, 0, 0, 0, 0),
        (2026, 1, 1, 24, 0, 0, 0),
        (2026, 1, 1, 0, 60, 0, 0),
        (2026, 1, 1, 0, 0, 60, 0),
        (2026, 1, 1, 0, 0, 0, 1000000),
    ] {
        assert_eq!(
            UtcInstant::new(year, month, day, hour, minute, second, micro),
            Err(UtcInstantError::InvalidDateTime)
        );
    }
    for (year, month, day) in [
        (1, 1, 1),
        (4, 2, 29),
        (1900, 2, 28),
        (2000, 2, 29),
        (2026, 12, 31),
        (9999, 12, 31),
    ] {
        for micro in [0, 1, 999998, 999999] {
            let t = UtcInstant::new(year, month, day, 23, 59, 59, micro).unwrap();
            assert_eq!(t.to_string().parse::<UtcInstant>().unwrap(), t);
        }
    }
    for text in [
        "가+00:00",
        "2026+00:00",
        "2026/10-20T01:02:03+00:00",
        "2026-10/20T01:02:03+00:00",
        "2026-10-20 01:02:03+00:00",
        "2026-10-20T01-02:03+00:00",
        "2026-10-20T01:02-03+00:00",
        "2026-1x-20T01:02:03+00:00",
        "2026-10-20T01:02:03x1+00:00",
        "2026-10-20T01:02:03.+00:00",
        "2026-10-20T01:02:03.0000001+00:00",
        "2026-10-20T01:02:03.abc+00:00",
    ] {
        assert_eq!(
            text.parse::<UtcInstant>(),
            Err(UtcInstantError::InvalidDateTime),
            "{text}"
        );
    }
    for text in ["", "2026-10-20T01:02:03", "2026-10-20T10:02:03+09:00"] {
        assert_eq!(text.parse::<UtcInstant>(), Err(UtcInstantError::NotUtc));
    }
    for e in [UtcInstantError::NotUtc, UtcInstantError::InvalidDateTime] {
        assert!(!e.to_string().is_empty());
        assert!(std::error::Error::source(&e).is_none());
    }
}
#[test]
fn utc_ordering_is_microsecond_exact_across_day_year_and_leap_boundaries() {
    for pair in [
        (
            "2026-10-20T01:02:03+00:00",
            "2026-10-20T01:02:03.000001+00:00",
        ),
        (
            "1999-12-31T23:59:59.999999+00:00",
            "2000-01-01T00:00:00+00:00",
        ),
        (
            "2000-02-28T23:59:59.999999+00:00",
            "2000-02-29T00:00:00+00:00",
        ),
    ] {
        assert!(pair.0.parse::<UtcInstant>().unwrap() < pair.1.parse::<UtcInstant>().unwrap());
    }
}
