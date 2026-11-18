use std::{cmp::Ordering, fmt};

use crate::{CurrencyCode, DecimalError, NslDecimal, Value};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum MoneyError {
    CurrencyMismatch,
    Decimal(DecimalError),
}

impl fmt::Display for MoneyError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::CurrencyMismatch => formatter.write_str("money currencies must match"),
            Self::Decimal(error) => error.fmt(formatter),
        }
    }
}

impl std::error::Error for MoneyError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::CurrencyMismatch => None,
            Self::Decimal(error) => Some(error),
        }
    }
}

/// An immutable finite Decimal amount and its currency.
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct Money {
    amount: NslDecimal,
    currency: CurrencyCode,
}

impl Money {
    pub fn new(amount: NslDecimal, currency: CurrencyCode) -> Self {
        Self { amount, currency }
    }

    pub fn amount(&self) -> &NslDecimal {
        &self.amount
    }

    pub fn currency(&self) -> &CurrencyCode {
        &self.currency
    }

    pub fn checked_add(&self, other: &Self) -> Result<Self, MoneyError> {
        self.require_same_currency(other)?;
        self.amount
            .checked_add(&other.amount)
            .map(|amount| Self::new(amount, self.currency.clone()))
            .map_err(MoneyError::Decimal)
    }

    pub fn checked_sub(&self, other: &Self) -> Result<Self, MoneyError> {
        self.require_same_currency(other)?;
        self.amount
            .checked_sub(&other.amount)
            .map(|amount| Self::new(amount, self.currency.clone()))
            .map_err(MoneyError::Decimal)
    }

    /// Compare same-currency NSL operands, including for semantic equality.
    pub fn checked_cmp(&self, other: &Self) -> Result<Ordering, MoneyError> {
        self.require_same_currency(other)?;
        Ok(self.amount.cmp(&other.amount))
    }

    fn require_same_currency(&self, other: &Self) -> Result<(), MoneyError> {
        if self.currency != other.currency {
            return Err(MoneyError::CurrencyMismatch);
        }
        Ok(())
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub enum MoneySumError {
    InvalidValue,
    CurrencyMismatch {
        expected: CurrencyCode,
        actual: CurrencyCode,
    },
    Decimal(DecimalError),
}
impl fmt::Display for MoneySumError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::InvalidValue => f.write_str("Money sum requires Money values"),
            Self::CurrencyMismatch { .. } => MoneyError::CurrencyMismatch.fmt(f),
            Self::Decimal(e) => e.fmt(f),
        }
    }
}
impl std::error::Error for MoneySumError {
    fn source(&self) -> Option<&(dyn std::error::Error + 'static)> {
        match self {
            Self::Decimal(e) => Some(e),
            _ => None,
        }
    }
}
/// Typed zero for an empty collection; validate and add each item in order so
/// the earliest invalid value, currency conflict or arithmetic error wins.
pub fn sum_money(values: &[Value], currency: &CurrencyCode) -> Result<Money, MoneySumError> {
    let mut total = Money::new("0".parse().expect("zero"), currency.clone());
    for value in values {
        let Value::Money(value) = value else {
            return Err(MoneySumError::InvalidValue);
        };
        total = total.checked_add(value).map_err(|e| match e {
            MoneyError::CurrencyMismatch => MoneySumError::CurrencyMismatch {
                expected: currency.clone(),
                actual: value.currency().clone(),
            },
            MoneyError::Decimal(e) => MoneySumError::Decimal(e),
        })?;
    }
    Ok(total)
}
