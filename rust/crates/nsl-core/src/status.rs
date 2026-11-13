use std::{fmt, str::FromStr};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct UnknownStatus(&'static str);

impl fmt::Display for UnknownStatus {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(formatter, "unknown {} value", self.0)
    }
}

impl std::error::Error for UnknownStatus {}

macro_rules! status_enum {
    ($name:ident { $($variant:ident => $wire:literal),+ $(,)? }) => {
        #[derive(Clone, Copy, Debug, Eq, Hash, PartialEq)]
        pub enum $name {
            $($variant),+
        }

        impl $name {
            pub const ALL: &'static [Self] = &[$(Self::$variant),+];

            pub const fn as_str(self) -> &'static str {
                match self {
                    $(Self::$variant => $wire),+
                }
            }
        }

        impl FromStr for $name {
            type Err = UnknownStatus;

            fn from_str(value: &str) -> Result<Self, Self::Err> {
                match value {
                    $($wire => Ok(Self::$variant),)+
                    _ => Err(UnknownStatus(stringify!($name))),
                }
            }
        }

        impl fmt::Display for $name {
            fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
                formatter.write_str(self.as_str())
            }
        }
    };
}

status_enum!(CheckStatus {
    Pass => "PASS",
    Fail => "FAIL",
    Unknown => "UNKNOWN",
});

status_enum!(CommitState {
    Committed => "COMMITTED",
    NotCommitted => "NOT_COMMITTED",
    UnknownCommitState => "UNKNOWN_COMMIT_STATE",
});

status_enum!(VerificationStatus {
    NotRequested => "NOT_REQUESTED",
    Verified => "VERIFIED",
    Failed => "FAILED",
    Unknown => "UNKNOWN",
});

status_enum!(Presence {
    Present => "PRESENT",
    Empty => "EMPTY",
});

status_enum!(Completeness {
    Complete => "COMPLETE",
    Partial => "PARTIAL",
    Unknown => "UNKNOWN",
});

status_enum!(ExecutionStatus {
    Created => "CREATED",
    Running => "RUNNING",
    Completed => "COMPLETED",
    Failed => "FAILED",
    ToolError => "TOOL_ERROR",
    ValidationError => "VALIDATION_ERROR",
    LimitExceeded => "LIMIT_EXCEEDED",
    Cancelled => "CANCELLED",
    WaitingForApproval => "WAITING_FOR_APPROVAL",
    ReconciliationRequired => "RECONCILIATION_REQUIRED",
});

status_enum!(ToolCapability {
    Read => "READ",
    Write => "WRITE",
    Transaction => "TRANSACTION",
});

status_enum!(RiskLevel {
    R0 => "R0",
    R1 => "R1",
    R2 => "R2",
    R3 => "R3",
    R4 => "R4",
    R5 => "R5",
});

status_enum!(ApprovalStatus {
    NotRequested => "NOT_REQUESTED",
    Pending => "PENDING",
    Approved => "APPROVED",
    Rejected => "REJECTED",
    Expired => "EXPIRED",
});

status_enum!(ApprovalDecision {
    Approved => "APPROVED",
    Rejected => "REJECTED",
});

status_enum!(PreWriteEligibility {
    Allowed => "ALLOWED",
    Blocked => "BLOCKED",
    RequiresApproval => "REQUIRES_APPROVAL",
});

status_enum!(DataClassification {
    Public => "PUBLIC",
    Internal => "INTERNAL",
    Confidential => "CONFIDENTIAL",
    Restricted => "RESTRICTED",
});
