use std::{cmp::Ordering, fmt};

use crate::{CurrencyCode, DecimalError, NslDecimal};

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
