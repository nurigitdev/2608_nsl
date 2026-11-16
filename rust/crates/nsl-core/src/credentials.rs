use std::fmt;

use crate::{ValueData, oracle_unicode::is_word};

const CREDENTIAL_KEYS: &[&[u8]] = &[
    b"accesskey",
    b"accesstoken",
    b"apikey",
    b"authorization",
    b"clientsecret",
    b"cookie",
    b"credential",
    b"credentials",
    b"idtoken",
    b"password",
    b"passwd",
    b"privatekey",
    b"pwd",
    b"refreshtoken",
    b"secret",
    b"sessiontoken",
    b"token",
];

/// A rejected value. The error omits input text and field names.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CredentialMaterialError;

impl fmt::Display for CredentialMaterialError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("credential material is forbidden")
    }
}

impl std::error::Error for CredentialMaterialError {}

/// Screen a structured value at an explicitly chosen validation boundary.
pub fn ensure_no_credential_material(value: &ValueData) -> Result<(), CredentialMaterialError> {
    let mut pending = vec![value];
    while let Some(current) = pending.pop() {
        match current {
            ValueData::String(text) if contains_marker(text) => {
                return Err(CredentialMaterialError);
            }
            ValueData::Array(items) => pending.extend(items),
            ValueData::Object(fields) => {
                for (key, item) in fields {
                    let empty = matches!(item, ValueData::Null)
                        || matches!(item, ValueData::String(text) if text.is_empty());
                    if contains_marker(key) || (!empty && is_credential_key(key)) {
                        return Err(CredentialMaterialError);
                    }
                    pending.push(item);
                }
            }
            _ => {}
        }
    }
    Ok(())
}

fn is_credential_key(key: &str) -> bool {
    // Of the non-ASCII lowercase mappings in the Oracle, only these leave
    // ASCII letters after its normalization removes non-ASCII characters.
    let normalized = key.chars().filter_map(|character| match character {
        'K' => Some(b'k'),
        'İ' => Some(b'i'),
        c if c.is_ascii_alphanumeric() => Some(c.to_ascii_lowercase() as u8),
        _ => None,
    });
    folded_label(normalized).is_some_and(|(bytes, len)| CREDENTIAL_KEYS.contains(&&bytes[..len]))
}

fn contains_marker(text: &str) -> bool {
    has_private_key_block(text) || has_inline_marker(text) || has_jwt_marker(text)
}

fn has_inline_marker(text: &str) -> bool {
    let mut previous_is_word = false;
    for (offset, character) in text.char_indices() {
        let current_is_word = is_word(character);
        if current_is_word && !previous_is_word {
            let (word, tail) = take_word(&text[offset..]);
            let spaced_tail = tail.trim_start_matches(python_whitespace);
            if spaced_tail.len() < tail.len()
                && (matches_folded(word, b"bearer") || matches_folded(word, b"basic"))
                && spaced_tail.starts_with(is_auth_token_character)
            {
                return true;
            }
            if has_assignment_value(spaced_tail) && is_inline_label(word, None) {
                return true;
            }
            if let Some(after_dash) = tail.strip_prefix('-') {
                let (suffix, tail) = take_word(after_dash);
                if has_assignment_value(tail.trim_start_matches(python_whitespace))
                    && is_inline_label(word, Some(suffix))
                {
                    return true;
                }
            }
        }
        previous_is_word = current_is_word;
    }
    false
}

fn take_word(text: &str) -> (&str, &str) {
    text.split_at(
        text.find(|character| !is_word(character))
            .unwrap_or(text.len()),
    )
}

fn has_assignment_value(text: &str) -> bool {
    text.strip_prefix([':', '='])
        .map(|tail| tail.trim_start_matches(python_whitespace))
        .is_some_and(|tail| tail.starts_with(|c| !matches!(c, ',' | ';' | ']' | '}')))
}

fn python_whitespace(character: char) -> bool {
    character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
}

fn regex_ascii_fold(character: char) -> Option<u8> {
    match character {
        'İ' | 'ı' => Some(b'i'),
        'ſ' => Some(b's'),
        'K' => Some(b'k'),
        c if c.is_ascii() => Some(c.to_ascii_lowercase() as u8),
        _ => None,
    }
}

fn matches_folded(text: &str, expected: &[u8]) -> bool {
    text.chars()
        .map(regex_ascii_fold)
        .eq(expected.iter().copied().map(Some))
}

fn folded_label(bytes: impl Iterator<Item = u8>) -> Option<([u8; 13], usize)> {
    let mut label = [0; 13];
    let mut len = 0;
    for byte in bytes {
        *label.get_mut(len)? = byte;
        len += 1;
    }
    Some((label, len))
}

fn is_inline_label(word: &str, suffix: Option<&str>) -> bool {
    let chars = word.chars().chain(
        suffix
            .into_iter()
            .flat_map(|suffix| std::iter::once('_').chain(suffix.chars())),
    );
    let Some((label, len)) = folded_label(chars.map(|c| regex_ascii_fold(c).unwrap_or(0))) else {
        return false;
    };
    let label = &label[..len];
    CREDENTIAL_KEYS.contains(&label)
        || matches!(
            label,
            b"access_key"
                | b"access_token"
                | b"api_key"
                | b"client_secret"
                | b"id_token"
                | b"private_key"
                | b"refresh_token"
                | b"session_token"
        )
}

fn is_auth_token_character(character: char) -> bool {
    regex_ascii_fold(character)
        .is_some_and(|byte| byte.is_ascii_alphanumeric() || b"._~+/=-".contains(&byte))
}

fn has_jwt_marker(text: &str) -> bool {
    let token_byte = |byte: u8| byte.is_ascii_alphanumeric() || matches!(byte, b'_' | b'-');
    let take_segment = |text: &str| text.find(|c: char| !c.is_ascii() || !token_byte(c as u8));
    for (offset, _) in text.match_indices("eyJ") {
        if offset > 0 && token_byte(text.as_bytes()[offset - 1]) {
            continue;
        }
        let mut tail = &text[offset + 3..];
        for _ in 0..2 {
            tail = match take_segment(tail) {
                Some(end) if end > 0 => tail[end..].strip_prefix('.').unwrap_or(""),
                _ => "",
            };
        }
        if tail.bytes().next().is_some_and(token_byte) {
            return true;
        }
    }
    false
}

fn has_private_key_block(text: &str) -> bool {
    fn marker_end(text: &str, marker: &str) -> Option<usize> {
        text.match_indices(marker).find_map(|(offset, _)| {
            let start = offset + marker.len();
            let mut tail = &text[start..];
            while let Some(after_space) = tail.strip_prefix(' ') {
                if let Some(remainder) = after_space.strip_prefix("PRIVATE KEY-----") {
                    return Some(text.len() - remainder.len());
                }
                let word_len = after_space
                    .bytes()
                    .take_while(|b| b.is_ascii_uppercase() || b.is_ascii_digit())
                    .count();
                if word_len == 0 {
                    break;
                }
                tail = &after_space[word_len..];
            }
            None
        })
    }

    marker_end(text, "-----BEGIN").is_some_and(|end| marker_end(&text[end..], "-----END").is_some())
}
