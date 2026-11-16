include!("unicode_digits.rs");

pub(crate) const MAX_INTEGER_DIGITS: usize = 4_300;

pub(crate) fn decimal_digit(character: char) -> Option<u8> {
    if character.is_ascii_digit() {
        return Some(character as u8 - b'0');
    }
    let code = u32::from(character);
    let previous = DECIMAL_ZEROES.partition_point(|&zero| zero <= code);
    let zero = *DECIMAL_ZEROES.get(previous.checked_sub(1)?)?;
    let digit = code - zero;
    (digit < 10).then_some(digit as u8)
}

pub(super) fn is_digit(character: char) -> bool {
    if character.is_ascii() {
        return character.is_ascii_digit();
    }
    decimal_digit(character).is_some()
        || OTHER_DIGIT_BOUNDARIES.partition_point(|&edge| edge <= u32::from(character)) % 2 != 0
}
