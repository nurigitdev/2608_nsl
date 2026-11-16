use std::collections::BTreeMap;

use nsl_core::{CanonicalJsonError, MAX_JSON_INTEGER_DIGITS, ValueData, canonical_json};

use super::{NsoCodecError, NsoLimit, NsoLimits, Result};

pub(super) fn parse(bytes: &[u8], limits: NsoLimits) -> Result<ValueData> {
    if bytes.len() > limits.max_bytes {
        return Err(NsoCodecError::Limit(NsoLimit::Bytes));
    }
    let text = std::str::from_utf8(bytes).map_err(|error| NsoCodecError::Json {
        offset: error.valid_up_to(),
        reason: "expected UTF-8 JSON",
    })?;
    Parser {
        text,
        offset: 0,
        limits,
        nodes: 0,
    }
    .document()
}

enum Frame {
    Array(Vec<ValueData>),
    Object {
        fields: BTreeMap<String, ValueData>,
        key: String,
    },
}

struct Parser<'a> {
    text: &'a str,
    offset: usize,
    limits: NsoLimits,
    nodes: usize,
}

impl Parser<'_> {
    fn document(&mut self) -> Result<ValueData> {
        let mut frames = Vec::new();
        loop {
            self.space();
            self.check_next_value(frames.len())?;
            self.nodes += 1;
            let mut value = match self.peek() {
                Some(b'[') => {
                    self.offset += 1;
                    self.space();
                    if !self.eat(b']') {
                        frames.push(Frame::Array(Vec::new()));
                        continue;
                    }
                    ValueData::Array(Vec::new())
                }
                Some(b'{') => {
                    self.offset += 1;
                    self.space();
                    if !self.eat(b'}') {
                        self.check_next_value(frames.len() + 1)?;
                        let key = self.key(&BTreeMap::new())?;
                        frames.push(Frame::Object {
                            fields: BTreeMap::new(),
                            key,
                        });
                        continue;
                    }
                    ValueData::Object(BTreeMap::new())
                }
                Some(b'"') => ValueData::String(self.string()?),
                Some(b'n') => self.keyword("null", ValueData::Null)?,
                Some(b't') => self.keyword("true", ValueData::Bool(true))?,
                Some(b'f') => self.keyword("false", ValueData::Bool(false))?,
                Some(b'-' | b'0'..=b'9') => self.integer()?,
                _ => return Err(self.error("expected JSON value")),
            };

            loop {
                self.space();
                value = match frames.pop() {
                    Some(Frame::Array(mut items)) => {
                        items.push(value);
                        if self.eat(b',') {
                            frames.push(Frame::Array(items));
                            break;
                        }
                        self.require(b']', "expected comma or closing bracket")?;
                        ValueData::Array(items)
                    }
                    Some(Frame::Object { mut fields, key }) => {
                        fields.insert(key, value);
                        if self.eat(b',') {
                            self.check_next_value(frames.len() + 1)?;
                            let key = self.key(&fields)?;
                            frames.push(Frame::Object { fields, key });
                            break;
                        }
                        self.require(b'}', "expected comma or closing brace")?;
                        ValueData::Object(fields)
                    }
                    None if self.offset == self.text.len() => return Ok(value),
                    None => return Err(self.error("unexpected trailing data")),
                };
            }
        }
    }

    fn error(&self, reason: &'static str) -> NsoCodecError {
        NsoCodecError::Json {
            offset: self.offset,
            reason,
        }
    }

    fn check_next_value(&self, depth: usize) -> Result<()> {
        if depth > self.limits.max_depth {
            return Err(NsoCodecError::Limit(NsoLimit::Depth));
        }
        if self.nodes == self.limits.max_nodes {
            return Err(NsoCodecError::Limit(NsoLimit::Nodes));
        }
        Ok(())
    }

    fn peek(&self) -> Option<u8> {
        self.text.as_bytes().get(self.offset).copied()
    }

    fn eat(&mut self, byte: u8) -> bool {
        if self.peek() != Some(byte) {
            return false;
        }
        self.offset += 1;
        true
    }

    fn space(&mut self) {
        while matches!(self.peek(), Some(b' ' | b'\t' | b'\n' | b'\r')) {
            self.offset += 1;
        }
    }

    fn require(&mut self, byte: u8, reason: &'static str) -> Result<()> {
        if self.eat(byte) {
            Ok(())
        } else {
            Err(self.error(reason))
        }
    }

    fn key(&mut self, fields: &BTreeMap<String, ValueData>) -> Result<String> {
        self.space();
        let key = self.string()?;
        if fields.contains_key(&key) {
            return Err(self.error("duplicate object field"));
        }
        self.space();
        self.require(b':', "expected colon after object field")?;
        Ok(key)
    }

    fn keyword(&mut self, expected: &str, value: ValueData) -> Result<ValueData> {
        if self.text[self.offset..].starts_with(expected) {
            self.offset += expected.len();
            Ok(value)
        } else {
            Err(self.error("invalid JSON keyword"))
        }
    }

    fn integer(&mut self) -> Result<ValueData> {
        let start = self.offset;
        self.eat(b'-');
        let digits = self.offset;
        match self.peek() {
            Some(b'0') => self.offset += 1,
            Some(b'1'..=b'9') => {
                while matches!(self.peek(), Some(b'0'..=b'9')) {
                    self.offset += 1;
                }
            }
            _ => return Err(self.error("expected decimal digit")),
        }
        if matches!(self.peek(), Some(b'0'..=b'9')) {
            return Err(self.error("leading zero in number"));
        }
        if matches!(self.peek(), Some(b'.' | b'e' | b'E')) {
            return Err(self.error("NSO number tokens must be integers"));
        }
        if self.offset - digits > MAX_JSON_INTEGER_DIGITS {
            return Err(self.error("integer token exceeds 4300 digits"));
        }
        let text = &self.text[start..self.offset];
        let text = if text == "-0" { "0" } else { text };
        text.parse()
            .map(ValueData::Number)
            .map_err(|_| self.error("invalid integer"))
    }

    fn string(&mut self) -> Result<String> {
        self.require(b'"', "expected JSON string")?;
        let mut value = String::new();
        loop {
            let start = self.offset;
            while matches!(self.peek(), Some(byte) if byte > 0x1f && !matches!(byte, b'"' | b'\\'))
            {
                self.offset += 1;
            }
            // The delimiters are ASCII, so both endpoints are UTF-8 boundaries.
            value.push_str(&self.text[start..self.offset]);
            match self.peek() {
                Some(b'"') => {
                    self.offset += 1;
                    return Ok(value);
                }
                Some(b'\\') => {
                    self.offset += 1;
                    value.push(self.escape()?);
                }
                Some(_) => return Err(self.error("unescaped control in string")),
                None => return Err(self.error("unterminated string")),
            }
        }
    }

    fn escape(&mut self) -> Result<char> {
        let byte = self
            .peek()
            .ok_or_else(|| self.error("unterminated string escape"))?;
        self.offset += 1;
        match byte {
            b'"' | b'\\' | b'/' => Ok(char::from(byte)),
            b'b' => Ok('\u{0008}'),
            b'f' => Ok('\u{000c}'),
            b'n' => Ok('\n'),
            b'r' => Ok('\r'),
            b't' => Ok('\t'),
            b'u' => self.unicode_escape(),
            _ => Err(self.error("invalid string escape")),
        }
    }

    fn unicode_escape(&mut self) -> Result<char> {
        let unit = self.hex_unit()?;
        let scalar = match unit {
            0xd800..=0xdbff => {
                self.require(b'\\', "expected low surrogate escape")?;
                self.require(b'u', "expected low surrogate escape")?;
                let low = self.hex_unit()?;
                if !(0xdc00..=0xdfff).contains(&low) {
                    return Err(self.error("expected low surrogate"));
                }
                0x10000 + ((unit - 0xd800) << 10) + (low - 0xdc00)
            }
            other => other,
        };
        char::from_u32(scalar).ok_or_else(|| self.error("invalid Unicode scalar"))
    }

    fn hex_unit(&mut self) -> Result<u32> {
        let mut value = 0;
        for _ in 0..4 {
            let digit = self
                .peek()
                .and_then(|byte| char::from(byte).to_digit(16))
                .ok_or_else(|| self.error("invalid Unicode escape"))?;
            self.offset += 1;
            value = value * 16 + digit;
        }
        Ok(value)
    }
}

pub(super) fn canonical(value: &ValueData) -> Result<Vec<u8>> {
    canonical_json(value)
        .map_err(|CanonicalJsonError::IntegerTooWide| NsoCodecError::IntegerTooWide)
}

#[cfg(test)]
mod tests {
    use super::{canonical, parse};
    use crate::NsoLimits;

    #[test]
    fn canonical_json_normalizes_keys_and_integer_zero() {
        let input = br#"{"z":null,"\u0061":-0}"#;
        let limits = NsoLimits {
            max_bytes: input.len(),
            max_depth: 1,
            max_nodes: 3,
        };
        let data = parse(input, limits).unwrap();
        assert_eq!(canonical(&data).unwrap(), br#"{"a":0,"z":null}"#);
    }
}
