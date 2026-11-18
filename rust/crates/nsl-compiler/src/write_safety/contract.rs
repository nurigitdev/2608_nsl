use nsl_contract::{Atomicity, OperationKind, ToolContract};
use nsl_core::{RiskLevel, ToolCapability};

use super::WriteSafetyDiagnosticKind;

// Copy just the policy metadata. Keeping the decision independent of model
// construction also permits differential fault injection without weakening the
// public validated ToolContract representation.
pub(super) struct ContractSafety {
    capability: Option<ToolCapability>,
    operation: Option<OperationKind>,
    atomicity: Option<Atomicity>,
    idempotency_required: Option<bool>,
    risk: Option<RiskLevel>,
}

impl From<&ToolContract> for ContractSafety {
    fn from(contract: &ToolContract) -> Self {
        Self {
            capability: contract.capability().known(),
            operation: contract.operation_kind(),
            atomicity: contract
                .write_semantics()
                .map(|semantics| semantics.atomicity()),
            idempotency_required: contract
                .write_semantics()
                .map(|semantics| semantics.idempotency().required()),
            risk: contract.risk_level(),
        }
    }
}

impl ContractSafety {
    pub(super) fn violation(&self) -> Option<WriteSafetyDiagnosticKind> {
        use WriteSafetyDiagnosticKind::*;
        if self.capability != Some(ToolCapability::Write) {
            return Some(WriteCapability);
        }
        if self.operation.is_none() {
            return Some(UnsupportedOperation);
        }
        if self.atomicity != Some(Atomicity::Atomic) {
            return Some(AtomicityRequired);
        }
        if self.idempotency_required != Some(true) {
            return Some(IdempotencyRequired);
        }
        if self.risk.is_none() {
            return Some(RiskMetadataRequired);
        }
        None
    }
}

#[cfg(test)]
#[path = "../../tests/write_safety_support/policy.rs"]
mod tests;
