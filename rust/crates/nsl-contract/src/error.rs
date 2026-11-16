use std::fmt;

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum ContractError {
    InvalidVersion,
    BlankCapability,
    BlankRisk,
    BlankInputName,
    DuplicateInputName,
    InvalidTimeout,
    InvalidRiskLevel,
    InvalidOperationKind,
    MissingWriteOperation,
    MissingWriteRiskLevel,
    MissingWriteSemantics,
    WriteFieldsRequireWriteCapability,
    IdempotencyRequired,
    BlankIdempotencyScope,
    BlankIdempotencyRetention,
    BlankDuplicateBehavior,
    BlankPayloadConflictBehavior,
    InvalidCommitConfirmation,
    BlankVerificationToolId,
    BlankVerificationConsistencyHint,
    MissingVerificationHint,
    SideEffectRequired,
    InvalidAtomicity,
    MissingIdempotency,
    MissingCommit,
    ApprovalRequired,
    PreconditionsRequired,
}

impl fmt::Display for ContractError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        use ContractError::*;
        f.write_str(match self {
            InvalidVersion => "tool version must use canonical major.minor.patch form",
            BlankCapability => "tool capability must be a non-empty string",
            BlankRisk => "tool risk must be a non-empty string",
            BlankInputName => "tool input names must be non-empty strings",
            DuplicateInputName => "tool input names must be unique",
            InvalidTimeout => "tool timeout_ms must be between 1 and 2147483647",
            InvalidRiskLevel => "tool risk_level must be one of: R0, R1, R2, R3, R4, R5",
            InvalidOperationKind => {
                "WRITE operation_kind must be one of: CREATE, CREATE_DRAFT, UPDATE"
            }
            MissingWriteOperation => "WRITE tool must declare operation_kind",
            MissingWriteRiskLevel => "WRITE tool must declare risk_level",
            MissingWriteSemantics => "WRITE tool must declare write_semantics",
            WriteFieldsRequireWriteCapability => {
                "operation_kind and write_semantics require capability=WRITE"
            }
            IdempotencyRequired => "WRITE idempotency.required must be true",
            BlankIdempotencyScope => "idempotency.scope must be a non-empty string",
            BlankIdempotencyRetention => "idempotency.retention must be a non-empty string",
            BlankDuplicateBehavior => "idempotency.duplicate_behavior must be a non-empty string",
            BlankPayloadConflictBehavior => {
                "idempotency.payload_conflict_behavior must be a non-empty string"
            }
            InvalidCommitConfirmation => "commit.confirmation must be one of: EXPLICIT",
            BlankVerificationToolId => "verification.tool_id must be a non-empty string",
            BlankVerificationConsistencyHint => {
                "verification.consistency_hint must be a non-empty string"
            }
            MissingVerificationHint => "verification must declare at least one hint",
            SideEffectRequired => "WRITE side_effect must be true",
            InvalidAtomicity => "WRITE atomicity must be one of: ATOMIC",
            MissingIdempotency => "WRITE idempotency semantics must be declared",
            MissingCommit => "WRITE commit semantics must be declared",
            ApprovalRequired => "WRITE approval.required must be true",
            PreconditionsRequired => "WRITE preconditions.supported must be true",
        })
    }
}

impl std::error::Error for ContractError {}
