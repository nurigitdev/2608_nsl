#![forbid(unsafe_code)]

mod catalog;
mod contract;
mod error;
mod identity;
mod semantics;

pub use nsl_core::ToolCapability;

pub use catalog::{CatalogError, CatalogErrorKind, ToolContractCatalog};
pub use contract::{
    ContractCapability, MAX_TOOL_TIMEOUT_MS, ToolContract, ToolContractOptions, ToolContractSpec,
};
pub use error::ContractError;
pub use identity::ContractEncodingError;
pub use semantics::{
    Atomicity, CommitConfirmation, CommitSemantics, IdempotencySemantics, OperationKind,
    PostWriteVerification, WriteSemantics, WriteSemanticsSpec,
};

fn is_blank(text: &str) -> bool {
    // Python str.strip() also removes U+001C through U+001F.
    text.chars()
        .all(|ch| ch.is_whitespace() || matches!(ch, '\u{1c}'..='\u{1f}'))
}
