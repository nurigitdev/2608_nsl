use super::{
    is_auth_token_character, is_inline_label, matches_folded, python_whitespace, take_word,
};
use crate::oracle_unicode::is_word;

pub const REDACTED: &str = "[REDACTED]";

/// Oracle `redact_text(text)` rules, with no caller-supplied sensitive values.
/// Unicode word boundaries use the frozen Python 3.12 Unicode 15.0 tables.
/// The order is private-key blocks, assignments, authentication schemes, JWTs.
pub fn redact_text(text: &str) -> String {
    let text = private_keys(text);
    let text = inline(&text, assignment);
    let text = inline(&text, auth_scheme);
    jwts(&text)
}

fn marker(text: &str, prefix: &str) -> Option<(usize, usize)> {
    for (start, _) in text.match_indices(prefix) {
        let mut tail = &text[start + prefix.len()..];
        while let Some(after_space) = tail.strip_prefix(' ') {
            if let Some(rest) = after_space.strip_prefix("PRIVATE KEY-----") {
                return Some((start, text.len() - rest.len()));
            }
            let length = after_space
                .bytes()
                .take_while(|c| c.is_ascii_uppercase() || c.is_ascii_digit())
                .count();
            if length == 0 {
                break;
            }
            tail = &after_space[length..];
        }
    }
    None
}

fn private_keys(text: &str) -> String {
    let mut output = String::new();
    let mut cursor = 0;
    while let Some((start, begin_end)) = marker(&text[cursor..], "-----BEGIN") {
        let Some((_, end)) = marker(&text[cursor + begin_end..], "-----END") else {
            break;
        };
        output.push_str(&text[cursor..cursor + start]);
        output.push_str(REDACTED);
        cursor += begin_end + end;
    }
    output.push_str(&text[cursor..]);
    output
}

// A match retains text before `start`, then replaces through `end`.
// Auth schemes additionally replace their whitespace with a single space.
struct Replacement {
    start: usize,
    end: usize,
    space: bool,
}
fn inline(text: &str, find: fn(&str) -> Option<Replacement>) -> String {
    let mut output = String::new();
    let mut cursor = 0;
    let mut previous_word = false;
    for (offset, ch) in text.char_indices() {
        let word = is_word(ch);
        let boundary = word && !previous_word;
        previous_word = word;
        if offset < cursor || !boundary {
            continue;
        }
        if let Some(found) = find(&text[offset..]) {
            output.push_str(&text[cursor..offset + found.start]);
            if found.space {
                output.push(' ');
            }
            output.push_str(REDACTED);
            cursor = offset + found.end;
        }
    }
    output.push_str(&text[cursor..]);
    output
}

fn assignment(text: &str) -> Option<Replacement> {
    let (word, tail) = take_word(text);
    let tail = if is_inline_label(word, None) {
        tail
    } else {
        let (suffix, tail) = take_word(tail.strip_prefix('-')?);
        if !is_inline_label(word, Some(suffix)) {
            return None;
        }
        tail
    };
    let tail = tail
        .trim_start_matches(python_whitespace)
        .strip_prefix([':', '='])?
        .trim_start_matches(python_whitespace);
    let start = text.len() - tail.len();
    let end = assignment_value(tail)?;
    Some(Replacement {
        start,
        end: start + end,
        space: false,
    })
}
fn assignment_value(text: &str) -> Option<usize> {
    if let Some(quote @ ('"' | '\'')) = text.chars().next() {
        let tail = &text[1..];
        if let Some((end, ch)) = tail
            .char_indices()
            .find(|(_, c)| *c == quote || matches!(c, '\r' | '\n'))
            && ch == quote
        {
            return Some(end + 2);
        }
    }
    let (word, tail) = take_word(text);
    let spaced = tail.trim_start_matches(python_whitespace);
    if (matches_folded(word, b"bearer") || matches_folded(word, b"basic"))
        && spaced.len() < tail.len()
    {
        let count = value_length(spaced);
        if count > 0 {
            return Some(text.len() - spaced.len() + count);
        }
    }
    let count = value_length(text);
    (count > 0).then_some(count)
}
fn value_length(text: &str) -> usize {
    text.find(|c| python_whitespace(c) || matches!(c, ',' | ';' | ']' | '}'))
        .unwrap_or(text.len())
}
fn auth_scheme(text: &str) -> Option<Replacement> {
    let (word, tail) = take_word(text);
    if !matches_folded(word, b"bearer") && !matches_folded(word, b"basic") {
        return None;
    }
    let token = tail.trim_start_matches(python_whitespace);
    if token.len() == tail.len() {
        return None;
    }
    let length = token
        .find(|c| !is_auth_token_character(c))
        .unwrap_or(token.len());
    (length > 0).then_some(Replacement {
        start: word.len(),
        end: text.len() - token.len() + length,
        space: true,
    })
}
fn jwt_byte(byte: u8) -> bool {
    byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-')
}
fn segment(text: &str) -> usize {
    text.bytes().take_while(|b| jwt_byte(*b)).count()
}
fn jwts(text: &str) -> String {
    let mut output = String::new();
    let mut cursor = 0;
    for (start, _) in text.match_indices("eyJ") {
        if start < cursor || (start > 0 && jwt_byte(text.as_bytes()[start - 1])) {
            continue;
        }
        let mut end = start + 3;
        let mut valid = true;
        for _ in 0..2 {
            let length = segment(&text[end..]);
            end += length;
            if length == 0 || text.as_bytes().get(end) != Some(&b'.') {
                valid = false;
                break;
            }
            end += 1;
        }
        if !valid {
            continue;
        }
        let length = segment(&text[end..]);
        if length == 0 {
            continue;
        }
        output.push_str(&text[cursor..start]);
        output.push_str(REDACTED);
        cursor = end + length;
    }
    output.push_str(&text[cursor..]);
    output
}
