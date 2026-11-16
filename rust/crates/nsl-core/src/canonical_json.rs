use std::fmt;

use crate::ValueData;

/// The integer conversion bound of the frozen CPython 3.12 Oracle.
pub const MAX_JSON_INTEGER_DIGITS: usize = 4_300;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum CanonicalJsonError {
    IntegerTooWide,
}

impl fmt::Display for CanonicalJsonError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::IntegerTooWide => write!(
                f,
                "canonical JSON integer exceeds {MAX_JSON_INTEGER_DIGITS} digits"
            ),
        }
    }
}

impl std::error::Error for CanonicalJsonError {}

/// Encode NSL structured data as sorted, compact UTF-8 JSON in Oracle format.
pub fn canonical_json(value: &ValueData) -> Result<Vec<u8>, CanonicalJsonError> {
    let mut output = String::new();
    write_value(&mut output, value)?;
    Ok(output.into_bytes())
}

fn quoted(output: &mut String, text: &str) {
    const HEX: &[u8] = b"0123456789abcdef";
    output.push('"');
    for character in text.chars() {
        match character {
            '"' => output.push_str("\\\""),
            '\\' => output.push_str("\\\\"),
            '\u{8}' => output.push_str("\\b"),
            '\u{c}' => output.push_str("\\f"),
            '\n' => output.push_str("\\n"),
            '\r' => output.push_str("\\r"),
            '\t' => output.push_str("\\t"),
            '\u{0}'..='\u{1f}' => {
                let byte = character as usize;
                output.push_str("\\u00");
                output.push(char::from(HEX[byte >> 4]));
                output.push(char::from(HEX[byte & 15]));
            }
            other => output.push(other),
        }
    }
    output.push('"');
}

fn write_value(output: &mut String, value: &ValueData) -> Result<(), CanonicalJsonError> {
    match value {
        ValueData::Null => output.push_str("null"),
        ValueData::Bool(true) => output.push_str("true"),
        ValueData::Bool(false) => output.push_str("false"),
        ValueData::Number(value) => {
            let text = value.as_str();
            if text.trim_start_matches('-').len() > MAX_JSON_INTEGER_DIGITS {
                return Err(CanonicalJsonError::IntegerTooWide);
            }
            output.push_str(text);
        }
        ValueData::String(text) => quoted(output, text),
        ValueData::Array(items) => {
            output.push('[');
            for (index, item) in items.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                write_value(output, item)?;
            }
            output.push(']');
        }
        ValueData::Object(fields) => {
            output.push('{');
            for (index, (key, item)) in fields.iter().enumerate() {
                if index != 0 {
                    output.push(',');
                }
                quoted(output, key);
                output.push(':');
                write_value(output, item)?;
            }
            output.push('}');
        }
    }
    Ok(())
}
