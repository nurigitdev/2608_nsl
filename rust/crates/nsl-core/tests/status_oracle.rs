use nsl_core::{
    ApprovalDecision, ApprovalStatus, CheckStatus, CommitState, Completeness, DataClassification,
    ExecutionStatus, PreWriteEligibility, Presence, RiskLevel, ToolCapability, VerificationStatus,
};

const ORACLE_VALUES: &str =
    include_str!("../../../../compatibility/v0_2_1/values/core-status-enums/enum-values.json");

macro_rules! wire_values {
    ($status:ty) => {{
        let mut values = Vec::new();
        for &status in <$status>::ALL {
            let value = status.as_str();
            assert_eq!(value.parse::<$status>().unwrap(), status);
            assert_eq!(status.to_string(), value);
            assert!(value.to_ascii_lowercase().parse::<$status>().is_err());
            assert!(format!(" {value}").parse::<$status>().is_err());
            values.push(value);
        }
        assert!("".parse::<$status>().is_err());
        assert!("UNRECOGNIZED".parse::<$status>().is_err());
        values.sort_unstable();
        values
    }};
}

#[test]
fn status_wire_values_match_frozen_corpus() {
    let enums = [
        ("ApprovalStatus", wire_values!(ApprovalStatus)),
        ("CheckStatus", wire_values!(CheckStatus)),
        ("CommitState", wire_values!(CommitState)),
        ("Completeness", wire_values!(Completeness)),
        ("DataClassification", wire_values!(DataClassification)),
        ("ExecutionStatus", wire_values!(ExecutionStatus)),
        ("Presence", wire_values!(Presence)),
        ("RiskLevel", wire_values!(RiskLevel)),
        ("VerificationStatus", wire_values!(VerificationStatus)),
    ];
    let entries = enums.map(|(name, values)| {
        let values = values
            .iter()
            .map(|value| format!("    \"{value}\""))
            .collect::<Vec<_>>();
        format!("  \"{name}\": [\n{}\n  ]", values.join(",\n"))
    });
    assert_eq!(format!("{{\n{}\n}}\n", entries.join(",\n")), ORACLE_VALUES);
}

#[test]
fn additional_wire_values_match_frozen_python_core() {
    // These enum values come from nsl/core.py at the locked Oracle commit 28a949e.
    assert_eq!(wire_values!(ApprovalDecision), ["APPROVED", "REJECTED"]);
    assert_eq!(
        wire_values!(PreWriteEligibility),
        ["ALLOWED", "BLOCKED", "REQUIRES_APPROVAL"]
    );
    assert_eq!(
        wire_values!(ToolCapability),
        ["READ", "TRANSACTION", "WRITE"]
    );
}
