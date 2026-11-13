use std::{cmp::Ordering, fmt, str::FromStr};

use bigdecimal::{BigDecimal, Pow, RoundingMode, Signed, Zero, num_bigint::BigInt};

const PRECISION: i64 = 28;
const MAX_ADJUSTED_EXPONENT: i64 = 999_999;
const MIN_EXPONENT: i64 = -999_999 - (PRECISION - 1);

// Limits of the Oracle's 64-bit Python Decimal constructor, before arithmetic.
const CONSTRUCTOR_MAX_ADJUSTED_EXPONENT: i64 = 999_999_999_999_999_999;
const CONSTRUCTOR_MIN_EXPONENT: i64 = -1_999_999_999_999_999_997;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum DecimalError {
    InvalidText,
    DivisionByZero,
    Overflow,
    EncodingLimit,
}

impl fmt::Display for DecimalError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(match self {
            Self::InvalidText => "expected finite decimal text",
            Self::DivisionByZero => "decimal division by zero",
            Self::Overflow => "decimal result exceeds the arithmetic exponent range",
            Self::EncodingLimit => "decimal fixed-point encoding exceeds the byte budget",
        })
    }
}

impl std::error::Error for DecimalError {}

/// Finite base-10 value. Parsing and comparison preserve full input precision.
#[derive(Clone, Debug)]
pub struct NslDecimal {
    value: BigDecimal,
    negative_zero: bool,
}

impl NslDecimal {
    fn from_parts(coefficient: BigInt, exponent: i64, negative: bool) -> Self {
        Self {
            negative_zero: negative && coefficient.is_zero(),
            value: BigDecimal::new(coefficient, -exponent),
        }
    }

    fn is_negative(&self) -> bool {
        self.negative_zero || self.value.is_negative()
    }

    /// Fixed-point output with the Oracle's scale and signed zero preserved.
    /// `max_bytes` bounds this token's allocation before exponent expansion.
    pub fn to_fixed_point(&self, max_bytes: usize) -> Result<String, DecimalError> {
        let (coefficient, scale) = self.value.as_bigint_and_scale();
        let digits = coefficient.magnitude().to_str_radix(10);
        let sign_bytes = usize::from(self.is_negative());
        let digit_bytes = digits.len();
        let length = if scale > 0 {
            (digit_bytes as u64).max(scale as u64 + 1) + 1
        } else if coefficient.is_zero() {
            1
        } else {
            digit_bytes as u64 + scale.unsigned_abs()
        } + sign_bytes as u64;
        let length = usize::try_from(length).map_err(|_| DecimalError::EncodingLimit)?;
        if length > max_bytes {
            return Err(DecimalError::EncodingLimit);
        }

        let mut text = String::new();
        text.try_reserve_exact(length)
            .map_err(|_| DecimalError::EncodingLimit)?;
        if self.is_negative() {
            text.push('-');
        }
        if scale <= 0 {
            text.push_str(&digits);
            text.extend(std::iter::repeat_n('0', length - text.len()));
        } else if scale as usize >= digit_bytes {
            text.push_str("0.");
            text.extend(std::iter::repeat_n('0', scale as usize - digit_bytes));
            text.push_str(&digits);
        } else {
            let (whole, fraction) = digits.split_at(digit_bytes - scale as usize);
            text.push_str(whole);
            text.push('.');
            text.push_str(fraction);
        }
        Ok(text)
    }

    pub fn checked_add(&self, other: &Self) -> Result<Self, DecimalError> {
        self.add_or_subtract(other, false)
    }

    pub fn checked_sub(&self, other: &Self) -> Result<Self, DecimalError> {
        self.add_or_subtract(other, true)
    }

    fn add_or_subtract(&self, other: &Self, subtract: bool) -> Result<Self, DecimalError> {
        let (left, left_scale) = self.value.as_bigint_and_scale();
        let (right, right_scale) = other.value.as_bigint_and_scale();
        let right_negative = other.is_negative() ^ subtract;
        let negative_zero = self.is_negative() && right_negative;
        let preferred = -left_scale.max(right_scale);
        let right = if subtract {
            -right.as_ref()
        } else {
            right.into_owned()
        };
        let (high, high_exp, low, low_exp) = if left_scale <= right_scale {
            (left.as_ref(), -left_scale, &right, -right_scale)
        } else {
            (&right, -right_scale, left.as_ref(), -left_scale)
        };

        if high.is_zero() || low.is_zero() {
            let (nonzero, exponent) = if high.is_zero() {
                (low, low_exp)
            } else {
                (high, high_exp)
            };
            if nonzero.is_zero() {
                return Self::rounded(BigInt::ZERO, preferred, negative_zero);
            }
            // Zeros only affect the preferred scale. At most PRECISION digits can survive.
            let target = preferred.max(exponent - PRECISION);
            return Self::rounded(nonzero * power_of_ten(exponent - target), target, false);
        }

        // Keep one guard position beyond any possible rounding tie. Any nonzero
        // operand below that position contributes only its sign to rounding.
        let guard = high_exp + (-1).min(digit_count(high) - PRECISION - 2);
        let (low, exponent) = if low_exp + digit_count(low) - 1 < guard {
            (BigInt::from(if low.is_negative() { -1 } else { 1 }), guard)
        } else {
            (low.clone(), low_exp)
        };
        Self::rounded(
            high * power_of_ten(high_exp - exponent) + low,
            exponent,
            false,
        )
    }

    pub fn checked_mul(&self, other: &Self) -> Result<Self, DecimalError> {
        let (left, left_scale) = self.value.as_bigint_and_scale();
        let (right, right_scale) = other.value.as_bigint_and_scale();
        Self::rounded(
            left.as_ref() * right.as_ref(),
            -left_scale - right_scale,
            self.is_negative() ^ other.is_negative(),
        )
    }

    pub fn checked_div(&self, other: &Self) -> Result<Self, DecimalError> {
        let (left, left_scale) = self.value.as_bigint_and_scale();
        let (right, right_scale) = other.value.as_bigint_and_scale();
        if right.is_zero() {
            return Err(DecimalError::DivisionByZero);
        }
        let negative = self.is_negative() ^ other.is_negative();
        let preferred = right_scale - left_scale;
        if left.is_zero() {
            return Self::rounded(BigInt::ZERO, preferred, negative);
        }

        // Compute an integer quotient with at least one digit beyond the precision.
        let shift = digit_count(&right) - digit_count(&left) + PRECISION + 1;
        let (numerator, denominator) = if shift >= 0 {
            (left.abs() * power_of_ten(shift), right.abs())
        } else {
            (left.abs(), right.abs() * power_of_ten(-shift))
        };
        let mut exponent = preferred - shift;
        let mut quotient = &numerator / &denominator;
        if (&numerator % &denominator).is_zero() {
            while exponent < preferred && (&quotient % 10_u8).is_zero() {
                quotient /= 10_u8;
                exponent += 1;
            }
        } else if (&quotient % 5_u8).is_zero() {
            // Retain a sticky digit so a truncated quotient cannot create a false tie.
            quotient += 1_u8;
        }
        if negative {
            quotient = -quotient;
        }
        Self::rounded(quotient, exponent, negative)
    }

    fn rounded(
        coefficient: BigInt,
        exponent: i64,
        negative_zero: bool,
    ) -> Result<Self, DecimalError> {
        if coefficient.is_zero() {
            return Ok(Self::from_parts(
                coefficient,
                exponent.clamp(MIN_EXPONENT, MAX_ADJUSTED_EXPONENT),
                negative_zero,
            ));
        }
        let negative = coefficient.is_negative();
        let adjusted = exponent + digit_count(&coefficient) - 1;
        if adjusted > MAX_ADJUSTED_EXPONENT {
            return Err(DecimalError::Overflow);
        }
        let target = (adjusted - (PRECISION - 1)).max(MIN_EXPONENT);
        if exponent >= target {
            return Ok(Self::from_parts(coefficient, exponent, false));
        }

        let (mut coefficient, _) = BigDecimal::new(coefficient, -exponent)
            .with_scale_round(-target, RoundingMode::HalfEven)
            .into_bigint_and_scale();
        let mut exponent = target;
        if digit_count(&coefficient) > PRECISION {
            coefficient /= 10_u8;
            exponent += 1;
            if exponent + PRECISION - 1 > MAX_ADJUSTED_EXPONENT {
                return Err(DecimalError::Overflow);
            }
        }
        Ok(Self::from_parts(coefficient, exponent, negative))
    }
}

impl FromStr for NslDecimal {
    type Err = DecimalError;

    fn from_str(text: &str) -> Result<Self, Self::Err> {
        let text: String = text
            .trim_matches(|character: char| {
                character.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&character)
            })
            .chars()
            .filter(|&character| character != '_')
            .map(decimal_digit_to_ascii)
            .collect();
        let (mantissa, exponent) = match text.split_once(['e', 'E']) {
            Some((mantissa, exponent)) => (
                mantissa,
                exponent
                    .parse::<i64>()
                    .map_err(|_| DecimalError::InvalidText)?,
            ),
            None => (text.as_str(), 0),
        };
        let negative = mantissa.starts_with('-');
        let unsigned = mantissa.strip_prefix(['+', '-']).unwrap_or(mantissa);
        let (whole, fraction) = unsigned.split_once('.').unwrap_or((unsigned, ""));
        if !whole
            .bytes()
            .chain(fraction.bytes())
            .all(|byte| byte.is_ascii_digit())
        {
            return Err(DecimalError::InvalidText);
        }
        let digits = [whole, fraction].concat();
        let mut coefficient =
            BigInt::parse_bytes(digits.as_bytes(), 10).ok_or(DecimalError::InvalidText)?;
        let exponent = exponent
            .checked_sub(fraction.len() as i64)
            .ok_or(DecimalError::InvalidText)?;
        let significant_digits = digits.trim_start_matches('0').len().max(1) as i64;
        if exponent < CONSTRUCTOR_MIN_EXPONENT
            || exponent > CONSTRUCTOR_MAX_ADJUSTED_EXPONENT - (significant_digits - 1)
        {
            return Err(DecimalError::InvalidText);
        }
        if negative {
            coefficient = -coefficient;
        }
        Ok(Self::from_parts(coefficient, exponent, negative))
    }
}

impl Ord for NslDecimal {
    fn cmp(&self, other: &Self) -> Ordering {
        let (left, left_scale) = self.value.as_bigint_and_scale();
        let (right, right_scale) = other.value.as_bigint_and_scale();
        let signs = left.sign().cmp(&right.sign());
        if signs != Ordering::Equal || left.is_zero() {
            return signs;
        }
        let left_digits = left.magnitude().to_str_radix(10);
        let right_digits = right.magnitude().to_str_radix(10);
        let magnitude = (left_digits.len() as i64 - left_scale)
            .cmp(&(right_digits.len() as i64 - right_scale))
            .then_with(|| {
                left_digits
                    .trim_end_matches('0')
                    .cmp(right_digits.trim_end_matches('0'))
            });
        if left.is_negative() {
            magnitude.reverse()
        } else {
            magnitude
        }
    }
}

impl PartialOrd for NslDecimal {
    fn partial_cmp(&self, other: &Self) -> Option<Ordering> {
        Some(self.cmp(other))
    }
}

impl PartialEq for NslDecimal {
    fn eq(&self, other: &Self) -> bool {
        self.cmp(other) == Ordering::Equal
    }
}

impl Eq for NslDecimal {}

fn digit_count(value: &BigInt) -> i64 {
    value.magnitude().to_str_radix(10).len() as i64
}

fn power_of_ten(exponent: i64) -> BigInt {
    debug_assert!(exponent >= 0);
    Pow::pow(BigInt::from(10_u8), exponent as u64)
}

fn decimal_digit_to_ascii(character: char) -> char {
    // Unicode 15.0 Decimal_Number runs from the Oracle's Python 3.12 tables.
    const ZEROES: &[u32] = &[
        0x660, 0x6f0, 0x7c0, 0x966, 0x9e6, 0xa66, 0xae6, 0xb66, 0xbe6, 0xc66, 0xce6, 0xd66, 0xde6,
        0xe50, 0xed0, 0xf20, 0x1040, 0x1090, 0x17e0, 0x1810, 0x1946, 0x19d0, 0x1a80, 0x1a90,
        0x1b50, 0x1bb0, 0x1c40, 0x1c50, 0xa620, 0xa8d0, 0xa900, 0xa9d0, 0xa9f0, 0xaa50, 0xabf0,
        0xff10, 0x104a0, 0x10d30, 0x11066, 0x110f0, 0x11136, 0x111d0, 0x112f0, 0x11450, 0x114d0,
        0x11650, 0x116c0, 0x11730, 0x118e0, 0x11950, 0x11c50, 0x11d50, 0x11da0, 0x11f50, 0x16a60,
        0x16ac0, 0x16b50, 0x1d7ce, 0x1d7d8, 0x1d7e2, 0x1d7ec, 0x1d7f6, 0x1e140, 0x1e2f0, 0x1e4f0,
        0x1e950, 0x1fbf0,
    ];
    if character.is_ascii() {
        return character;
    }
    let code = u32::from(character);
    let upper = ZEROES.partition_point(|&zero| zero <= code);
    if upper > 0 && code - ZEROES[upper - 1] < 10 {
        return char::from(b'0' + (code - ZEROES[upper - 1]) as u8);
    }
    character
}
