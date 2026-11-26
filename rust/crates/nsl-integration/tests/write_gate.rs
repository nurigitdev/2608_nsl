#[path = "../../nsl-write/tests/guard_support/mod.rs"]
mod guard_support;
#[path = "../../nsl-write/tests/support/mod.rs"]
mod support;
mod write_gate_support;
use nsl_core::*;
use nsl_write::*;
use support::*;
fn rows(s: &str) -> impl Iterator<Item = (&str, ValueData)> {
    s.lines().skip(1).map(|s| {
        let (name, hex) = s.split_once('\t').unwrap();
        (name, parse(&decode(hex)))
    })
}
#[test]
fn r8_exit_gate_composes_native_checks_approval_policy_idempotency_and_durability() {
    let base = parse(include_bytes!("fixtures/write-gate-base.json"));
    let mut count = 0;
    let mut positives = 0;
    let mut required = std::collections::BTreeSet::new();
    for (name, d) in rows(include_str!("fixtures/write-gate-normal-oracle.tsv")) {
        let actual = write_gate_support::run(&d, &base);
        let case = get(field(&d, "config"), "case");
        if [
            "check-fail",
            "check-unknown",
            "approval-proof",
            "idempotency-conflict",
            "policy-deny",
            "precondition-binding-contract_hash",
        ]
        .contains(&case)
        {
            required.insert(case.to_string());
        }
        if get(&actual, "phase") == "guard" {
            assert_eq!(
                field(&actual, "reserve_calls"),
                &ValueData::Number(0.into())
            );
        }
        if matches!(get(&actual, "phase"), "guard" | "idempotency" | "builder") {
            assert_eq!(field(&actual, "stored"), &ValueData::Number(0.into()));
        }

        assert_eq!(actual, *field(&d, "observed"), "{name}");
        if get(&actual, "phase") == "provider" {
            positives += 1;
        } else {
            assert_eq!(
                field(&actual, "provider_calls"),
                &ValueData::Number(0.into()),
                "{name}"
            );
        }
        count += 1;
    }
    assert_eq!(count, 54);
    assert_eq!(positives, 6);
    assert_eq!(required.len(), 6);
    println!(
        "R8 exit PASS: {count} frozen component-composition scenarios, {positives} positive controls; no business provider I/O"
    );
}
#[test]
fn approved_current_principal_contract_and_reuse_exceptions_are_explicit() {
    let base = parse(include_bytes!("fixtures/write-gate-base.json"));
    let mut count = 0;
    for (name, d) in rows(include_str!("fixtures/write-gate-exceptions-oracle.tsv")) {
        let old = field(&d, "observed");
        assert_eq!(get(old, "phase"), "provider");
        assert_eq!(field(old, "provider_calls"), &ValueData::Number(1.into()));
        let native = write_gate_support::run(&d, &base);
        let case = get(field(&d, "config"), "case");
        assert_eq!(
            field(&native, "provider_calls"),
            &ValueData::Number(0.into()),
            "{name}"
        );
        assert_eq!(field(&native, "stored"), &ValueData::Number(0.into()));
        if matches!(case, "reused" | "pending") {
            assert_eq!(get(&native, "phase"), case);
            assert_eq!(
                field(&native, "reserve_calls"),
                &ValueData::Number(1.into())
            );
        } else {
            assert_eq!(get(&native, "phase"), "guard");
            assert_eq!(
                field(&native, "reserve_calls"),
                &ValueData::Number(0.into())
            );
            assert_eq!(field(&native, "policy_calls"), &ValueData::Number(0.into()));
            if case == "current-contract" {
                assert_eq!(
                    get(field(&native, "decision"), "reason_code"),
                    "WRITE_DECLARED_CONTRACT_MISMATCH"
                );
            }
        }
        count += 1;
    }
    assert_eq!(count, 5);
}
#[test]
fn every_declared_binding_and_semantic_identity_rejects_before_policy_or_mutation() {
    use guard_support as g;
    use std::sync::atomic::{AtomicUsize, Ordering};
    struct Policy(AtomicUsize);
    impl RuntimePolicyPort for Policy {
        fn authorize_write(
            &self,
            _: &WritePolicyRequest,
        ) -> Result<PolicyDecision, RuntimePolicyError> {
            self.0.fetch_add(1, Ordering::SeqCst);
            Err(RuntimePolicyError)
        }
    }
    for field in [
        "semantic",
        "missing",
        "duplicate",
        "tool",
        "version",
        "hash",
        "capability",
        "risk",
        "scope",
    ] {
        let mut s = g::Setup::new(g::baseline());
        match field {
            "semantic" => {
                s.skill.semantic_hash = format!("sha256:{}", "0".repeat(64)).parse().unwrap()
            }
            "missing" => s.skill.required_tools.clear(),
            "duplicate" => s
                .skill
                .required_tools
                .push(s.skill.required_tools[0].clone()),
            "tool" => s.skill.required_tools[0].tool_id = "OTHER.TOOL".into(),
            "version" => s.skill.required_tools[0].version = "2.0.0".into(),
            "hash" => {
                s.skill.required_tools[0].contract_hash =
                    format!("sha256:{}", "f".repeat(64)).parse().unwrap()
            }
            "capability" => s.skill.required_tools[0].kind = nsl_ir::RequiredToolKind::ReadV1_0,
            "risk" => {
                s.skill.required_tools[0].kind = nsl_ir::RequiredToolKind::V1_1 {
                    capability: ToolCapability::Write,
                    risk_level: RiskLevel::R4,
                }
            }
            "scope" => s.skill.required_tools[0].required_scope = "other:scope".into(),
            _ => unreachable!(),
        };
        let policy = Policy(AtomicUsize::new(0));
        let decision = WriteGuard::default()
            .authorize(s.request(), &policy, s.now)
            .unwrap();
        assert_eq!(
            decision.eligibility(),
            PreWriteEligibility::Blocked,
            "{field}"
        );
        assert_eq!(
            decision.reason_code(),
            Some(if field == "semantic" {
                "WRITE_SKILL_BINDING_MISMATCH"
            } else {
                "WRITE_DECLARED_CONTRACT_MISMATCH"
            })
        );
        assert_eq!(policy.0.load(Ordering::SeqCst), 0);
    }
}

#[test]
fn r8_inventory_binds_the_frozen_oracle_all_prior_slices_and_approved_differences() {
    use sha2::{Digest, Sha256};
    let manifest = parse(include_bytes!("fixtures/write-gate-manifest.json"));
    assert_eq!(get(&manifest, "gate_id"), "R8-EXIT");
    assert_eq!(
        get(&manifest, "oracle_commit"),
        "28a949ec96581cbdc52c8133527b23bb5b72ee0a"
    );
    let lock = include_bytes!("../../../../rust-transition/baseline/python-oracle-lock.json");
    assert_eq!(
        get(&manifest, "oracle_lock_sha256"),
        hex(&Sha256::digest(lock))
    );
    assert_eq!(
        field(&manifest, "approved_difference_ids"),
        &ValueData::Array(
            [
                "R8-S01-SNAPSHOT-METADATA",
                "R8-S03-CURRENT-PRINCIPAL-SCOPE",
                "R8-S04-RESERVATION-DISPOSITION",
                "R8-S05-DECLARED-CONTRACT-BINDING"
            ]
            .into_iter()
            .map(txt)
            .collect()
        )
    );
    let fixtures: [(&str, &[u8]); 24] = [
        (
            "rust/crates/nsl-write/tests/fixtures/idempotency-keys-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/idempotency-keys-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/idempotency-models-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/idempotency-models-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/idempotency-reservations-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/idempotency-reservations-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/idempotency-retention-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/idempotency-retention-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-admission-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-admission-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-goldens-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-goldens-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-guard-decisions-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-guard-decisions-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-guard-exceptions-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-guard-exceptions-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-guard-models-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-guard-models-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-guard-prewrite-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-guard-prewrite-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-metadata-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-metadata-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-models-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-models-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-negative-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-negative-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-requested-build-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-requested-build-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-requested-dispositions-oracle.tsv",
            include_bytes!(
                "../../nsl-write/tests/fixtures/write-requested-dispositions-oracle.tsv"
            ),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-requested-durability-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-requested-durability-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-requested-models-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-requested-models-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-requested-records-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-requested-records-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-write/tests/fixtures/write-values-oracle.tsv",
            include_bytes!("../../nsl-write/tests/fixtures/write-values-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-integration/tests/fixtures/write-gate-base.json",
            include_bytes!("fixtures/write-gate-base.json"),
        ),
        (
            "rust/crates/nsl-integration/tests/fixtures/write-gate.nso",
            include_bytes!("fixtures/write-gate.nso"),
        ),
        (
            "rust/crates/nsl-integration/tests/fixtures/write-gate-normal-oracle.tsv",
            include_bytes!("fixtures/write-gate-normal-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-integration/tests/fixtures/write-gate-exceptions-oracle.tsv",
            include_bytes!("fixtures/write-gate-exceptions-oracle.tsv"),
        ),
        (
            "rust/crates/nsl-runtime/tests/fixtures/approval-gate-manifest.json",
            include_bytes!("../../nsl-runtime/tests/fixtures/approval-gate-manifest.json"),
        ),
    ];
    assert_eq!(arr(field(&manifest, "fixtures")).len(), fixtures.len());
    for (r, (path, bytes)) in arr(field(&manifest, "fixtures")).iter().zip(fixtures) {
        assert_eq!(get(r, "path"), path);
        assert_eq!(get(r, "sha256"), hex(&Sha256::digest(bytes)));
        if path.ends_with(".tsv") {
            let text = std::str::from_utf8(bytes).unwrap();
            assert!(
                text.lines()
                    .next()
                    .unwrap()
                    .contains(get(&manifest, "oracle_commit"))
            );
            assert_eq!(
                field(r, "rows"),
                &ValueData::Number(((text.lines().count() - 1) as i64).into())
            );
        }
    }
    println!(
        "R8 inventory PASS: 19 prior R8 fixtures + four gate artifacts + R7 manifest, four approved difference groups"
    );
}
#[test]
fn contract_recompilation_requires_new_intent_approval_before_a_positive_handoff() {
    let base = parse(include_bytes!("fixtures/write-gate-base.json"));
    for (_, d) in rows(include_str!("fixtures/write-gate-normal-oracle.tsv")) {
        let case = get(field(&d, "config"), "case");
        if !case.starts_with("recompiled-") {
            continue;
        }
        let intent = field(&d, "intent");
        assert_ne!(
            get(intent, "skill_semantic_hash"),
            get(field(&base, "intent"), "skill_semantic_hash")
        );
        assert_ne!(
            get(intent, "write_intent_hash"),
            get(field(&base, "intent"), "write_intent_hash")
        );
        let actual = write_gate_support::run(&d, &base);
        if case == "recompiled-old-approval" {
            assert_eq!(
                get(field(&actual, "approval"), "validation"),
                "INTENT_MISMATCH"
            );
            assert_eq!(field(&actual, "stored"), &ValueData::Number(0.into()));
            assert_eq!(
                field(&actual, "provider_calls"),
                &ValueData::Number(0.into())
            );
        } else {
            assert_eq!(get(&actual, "phase"), "provider");
            assert_eq!(
                field(&actual, "provider_calls"),
                &ValueData::Number(1.into())
            );
        }
    }
}
