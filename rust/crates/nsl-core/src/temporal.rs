//! Business Date/DateTime normalization shared by checkpoint and WRITE values.
//! Security/evidence instants remain the separate UTC-only UtcInstant contract.
use crate::UtcInstant;

/// Normalize the business date text admitted by the frozen checkpoint boundary.
pub fn normalize_date(text: &str) -> Option<String> {
    temporal(text, false, true)
}
/// Normalize business DateTime, retaining naive/offset and microsecond semantics.
/// Callers whose boundary requires timezone awareness must additionally enforce it.
pub fn normalize_datetime(text: &str) -> Option<String> {
    temporal(text, true, true)
}

/// Normalize a business DateTime for value encoding, preserving fractional
/// timezone offsets that the frozen fromisoformat decoder normalizes to UTC.
pub fn canonical_datetime(text: &str) -> Option<String> {
    temporal(text, true, false)
}

fn temporal(s: &str, datetime: bool, decoder_compat: bool) -> Option<String> {
    let (date, time) = if datetime {
        let split = if s.as_bytes().get(4) == Some(&b'-') {
            10
        } else {
            8
        };
        (s.get(..split)?, Some(s.get(split + 1..)?))
    } else {
        (s, None)
    };
    let normalized = if date.len() == 8 && date.is_ascii() {
        format!("{}-{}-{}", &date[..4], &date[4..6], &date[6..])
    } else {
        date.to_string()
    };
    format!("{normalized}T00:00:00+00:00")
        .parse::<UtcInstant>()
        .ok()?;
    let Some(time) = time else {
        return Some(normalized);
    };
    let (body, zone) = if let Some(i) = time.find(['+', '-']) {
        (&time[..i], Some(&time[i..]))
    } else if let Some(body) = time.strip_suffix('Z') {
        (body, Some("+00:00"))
    } else {
        (time, None)
    };
    fn clock(s: &str) -> Option<(u32, u32, u32, u32)> {
        // Python also accepts a fourth colon as the fractional-second separator.
        let legacy = if s.bytes().filter(|b| *b == b':').count() == 3 && !s.contains(['.', ',']) {
            let (a, b) = s.rsplit_once(':')?;
            Some(format!("{a}.{b}"))
        } else {
            None
        };
        let s = legacy.as_deref().unwrap_or(s);
        let split = s.split_once(['.', ',']);
        if split.is_some_and(|(_, fraction)| fraction.is_empty()) {
            return None;
        }
        let (whole, fraction) = split.unwrap_or((s, ""));
        if !whole.is_ascii() || !fraction.bytes().all(|b| b.is_ascii_digit()) {
            return None;
        }
        let items = if whole.contains(':') {
            whole.split(':').collect::<Vec<_>>()
        } else {
            if whole.len() != 2 && whole.len() != 4 && whole.len() != 6 {
                return None;
            }
            whole
                .as_bytes()
                .chunks(2)
                .map(|v| std::str::from_utf8(v).ok())
                .collect::<Option<Vec<_>>>()?
        };
        if items.len() > 3 {
            return None;
        }
        let n = |i: usize| -> Option<u32> {
            items.get(i).map_or(Some(0), |s| {
                if s.len() == 2 && s.bytes().all(|b| b.is_ascii_digit()) {
                    s.parse().ok()
                } else {
                    None
                }
            })
        };
        let h = n(0)?;
        let m = n(1)?;
        let sec = n(2)?;
        if h > 23 || m > 59 || sec > 59 {
            return None;
        }
        let f = &fraction[..fraction.len().min(6)];
        let us = if f.is_empty() {
            0
        } else {
            f.parse::<u32>().ok()? * 10u32.pow(6 - f.len() as u32)
        };
        Some((h, m, sec, us))
    }
    let (h, m, sec, us) = clock(body)?;
    let mut out = format!("{normalized}T{h:02}:{m:02}:{sec:02}");
    if us != 0 {
        out.push_str(&format!(".{us:06}"));
    }
    if let Some(z) = zone {
        let (h, m, sec, mut us) = clock(z.get(1..)?)?;
        // Python 3.12 treats a zero whole-second offset as UTC even when a
        // fractional offset suffix is present. Preserve the frozen normalization.
        if decoder_compat && h == 0 && m == 0 && sec == 0 {
            us = 0;
        }
        let sign = if h == 0 && m == 0 && sec == 0 && us == 0 {
            '+'
        } else {
            z.chars().next()?
        };
        out.push_str(&format!("{sign}{h:02}:{m:02}"));
        if sec != 0 || us != 0 {
            out.push_str(&format!(":{sec:02}"));
            if us != 0 {
                out.push_str(&format!(".{us:06}"));
            }
        }
    }
    Some(out)
}
