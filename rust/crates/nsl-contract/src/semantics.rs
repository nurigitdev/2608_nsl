use std::{fmt, str::FromStr};

use nsl_core::TypeRef;

use crate::{ContractError, is_blank};

macro_rules! contract_enum {
    ($name:ident, $error:ident, { $($variant:ident => $wire:literal),+ $(,)? }) => {
        #[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
        pub enum $name {
            $($variant),+
        }

        impl $name {
            pub const fn as_str(self) -> &'static str {
                match self {
                    $(Self::$variant => $wire),+
                }
            }
        }

        impl FromStr for $name {
            type Err = ContractError;

            fn from_str(text: &str) -> Result<Self, Self::Err> {
                match text {
                    $($wire => Ok(Self::$variant),)+
                    _ => Err(ContractError::$error),
                }
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
                f.write_str(self.as_str())
            }
        }
    };
}

contract_enum!(OperationKind, InvalidOperationKind, {
    Create => "CREATE",
    CreateDraft => "CREATE_DRAFT",
    Update => "UPDATE",
});
contract_enum!(Atomicity, InvalidAtomicity, { Atomic => "ATOMIC" });
contract_enum!(CommitConfirmation, InvalidCommitConfirmation, { Explicit => "EXPLICIT" });

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct IdempotencySemantics {
    scope: String,
    retention: String,
    duplicate_behavior: String,
    payload_conflict_behavior: String,
}

impl IdempotencySemantics {
    pub fn new(
        required: bool,
        scope: impl Into<String>,
        retention: impl Into<String>,
        duplicate_behavior: impl Into<String>,
        payload_conflict_behavior: impl Into<String>,
    ) -> Result<Self, ContractError> {
        if !required {
            return Err(ContractError::IdempotencyRequired);
        }
        let semantics = Self {
            scope: scope.into(),
            retention: retention.into(),
            duplicate_behavior: duplicate_behavior.into(),
            payload_conflict_behavior: payload_conflict_behavior.into(),
        };
        for (value, error) in [
            (&semantics.scope, ContractError::BlankIdempotencyScope),
            (
                &semantics.retention,
                ContractError::BlankIdempotencyRetention,
            ),
            (
                &semantics.duplicate_behavior,
                ContractError::BlankDuplicateBehavior,
            ),
            (
                &semantics.payload_conflict_behavior,
                ContractError::BlankPayloadConflictBehavior,
            ),
        ] {
            if is_blank(value) {
                return Err(error);
            }
        }
        Ok(semantics)
    }

    pub const fn required(&self) -> bool {
        true
    }

    pub fn scope(&self) -> &str {
        &self.scope
    }

    pub fn retention(&self) -> &str {
        &self.retention
    }

    pub fn duplicate_behavior(&self) -> &str {
        &self.duplicate_behavior
    }

    pub fn payload_conflict_behavior(&self) -> &str {
        &self.payload_conflict_behavior
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct CommitSemantics {
    confirmation: CommitConfirmation,
}

impl CommitSemantics {
    pub const fn new(confirmation: CommitConfirmation) -> Self {
        Self { confirmation }
    }

    pub const fn confirmation(self) -> CommitConfirmation {
        self.confirmation
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct PostWriteVerification {
    tool_id: Option<String>,
    reference_schema: Option<TypeRef>,
    consistency_hint: Option<String>,
}

impl PostWriteVerification {
    pub fn new(
        tool_id: Option<String>,
        reference_schema: Option<TypeRef>,
        consistency_hint: Option<String>,
    ) -> Result<Self, ContractError> {
        if tool_id.as_deref().is_some_and(is_blank) {
            return Err(ContractError::BlankVerificationToolId);
        }
        if consistency_hint.as_deref().is_some_and(is_blank) {
            return Err(ContractError::BlankVerificationConsistencyHint);
        }
        if tool_id.is_none() && reference_schema.is_none() && consistency_hint.is_none() {
            return Err(ContractError::MissingVerificationHint);
        }
        Ok(Self {
            tool_id,
            reference_schema,
            consistency_hint,
        })
    }

    pub fn tool_id(&self) -> Option<&str> {
        self.tool_id.as_deref()
    }

    pub fn reference_schema(&self) -> Option<&TypeRef> {
        self.reference_schema.as_ref()
    }

    pub fn consistency_hint(&self) -> Option<&str> {
        self.consistency_hint.as_deref()
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteSemanticsSpec {
    pub side_effect: bool,
    pub atomicity: String,
    pub idempotency: Option<IdempotencySemantics>,
    pub commit: Option<CommitSemantics>,
    pub approval_required: bool,
    pub preconditions_supported: bool,
    pub verification: Option<PostWriteVerification>,
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct WriteSemantics {
    atomicity: Atomicity,
    idempotency: IdempotencySemantics,
    commit: CommitSemantics,
    verification: Option<PostWriteVerification>,
}

impl WriteSemantics {
    pub fn new(spec: WriteSemanticsSpec) -> Result<Self, ContractError> {
        if !spec.side_effect {
            return Err(ContractError::SideEffectRequired);
        }
        let atomicity = spec.atomicity.parse()?;
        let idempotency = spec.idempotency.ok_or(ContractError::MissingIdempotency)?;
        let commit = spec.commit.ok_or(ContractError::MissingCommit)?;
        if !spec.approval_required {
            return Err(ContractError::ApprovalRequired);
        }
        if !spec.preconditions_supported {
            return Err(ContractError::PreconditionsRequired);
        }
        Ok(Self {
            atomicity,
            idempotency,
            commit,
            verification: spec.verification,
        })
    }

    pub const fn side_effect(&self) -> bool {
        true
    }

    pub const fn atomicity(&self) -> Atomicity {
        self.atomicity
    }

    pub const fn idempotency(&self) -> &IdempotencySemantics {
        &self.idempotency
    }

    pub const fn commit(&self) -> CommitSemantics {
        self.commit
    }

    pub const fn approval_required(&self) -> bool {
        true
    }

    pub const fn preconditions_supported(&self) -> bool {
        true
    }

    pub const fn verification(&self) -> Option<&PostWriteVerification> {
        self.verification.as_ref()
    }
}
