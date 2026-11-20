use std::{fmt, str::FromStr};

/// UTC civil instant at the Oracle's microsecond precision (years 1..=9999).
/// It is separate from NSL DateTime business values, which may carry offsets.
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
pub struct UtcInstant {
    year: u16,
    month: u8,
    day: u8,
    hour: u8,
    minute: u8,
    second: u8,
    microsecond: u32,
}
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum UtcInstantError {
    InvalidDateTime,
    NotUtc,
}
impl fmt::Display for UtcInstantError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(match self {
            Self::InvalidDateTime => "invalid UTC date/time or microsecond precision",
            Self::NotUtc => "security/evidence timestamp must be timezone-aware UTC",
        })
    }
}
impl std::error::Error for UtcInstantError {}
impl UtcInstant {
    pub fn new(
        year: u16,
        month: u8,
        day: u8,
        hour: u8,
        minute: u8,
        second: u8,
        microsecond: u32,
    ) -> Result<Self, UtcInstantError> {
        let leap =
            year.is_multiple_of(4) && (!year.is_multiple_of(100) || year.is_multiple_of(400));
        let days = [
            31,
            if leap { 29 } else { 28 },
            31,
            30,
            31,
            30,
            31,
            31,
            30,
            31,
            30,
            31,
        ];
        if !(1..=9999).contains(&year)
            || !(1..=12).contains(&month)
            || day == 0
            || day > days[usize::from(month.saturating_sub(1).min(11))]
            || hour > 23
            || minute > 59
            || second > 59
            || microsecond > 999_999
        {
            return Err(UtcInstantError::InvalidDateTime);
        }
        Ok(Self {
            year,
            month,
            day,
            hour,
            minute,
            second,
            microsecond,
        })
    }
}
impl fmt::Display for UtcInstant {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(
            f,
            "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}",
            self.year, self.month, self.day, self.hour, self.minute, self.second
        )?;
        if self.microsecond != 0 {
            write!(f, ".{:06}", self.microsecond)?;
        }
        f.write_str("+00:00")
    }
}
impl FromStr for UtcInstant {
    type Err = UtcInstantError;
    /// Parse UTC ISO text; Z and negative zero offsets normalize to +00:00.
    fn from_str(text: &str) -> Result<Self, Self::Err> {
        let raw = text
            .strip_suffix("+00:00")
            .or_else(|| text.strip_suffix("-00:00"))
            .or_else(|| text.strip_suffix('Z'))
            .ok_or(UtcInstantError::NotUtc)?;
        if !raw.is_ascii()
            || raw.len() < 19
            || raw.as_bytes()[4] != b'-'
            || raw.as_bytes()[7] != b'-'
            || raw.as_bytes()[10] != b'T'
            || raw.as_bytes()[13] != b':'
            || raw.as_bytes()[16] != b':'
        {
            return Err(UtcInstantError::InvalidDateTime);
        }
        let number = |s: &str| -> Result<u32, UtcInstantError> {
            if !s.bytes().all(|b| b.is_ascii_digit()) {
                return Err(UtcInstantError::InvalidDateTime);
            }
            Ok(s.bytes().fold(0, |n, b| n * 10 + u32::from(b - b'0')))
        };
        let micros = if raw.len() == 19 {
            0
        } else {
            let tail = raw[19..]
                .strip_prefix('.')
                .ok_or(UtcInstantError::InvalidDateTime)?;
            if tail.is_empty() || tail.len() > 6 {
                return Err(UtcInstantError::InvalidDateTime);
            }
            number(tail)? * 10_u32.pow(6 - tail.len() as u32)
        };
        Self::new(
            number(&raw[..4])? as u16,
            number(&raw[5..7])? as u8,
            number(&raw[8..10])? as u8,
            number(&raw[11..13])? as u8,
            number(&raw[14..16])? as u8,
            number(&raw[17..19])? as u8,
            micros,
        )
    }
}
