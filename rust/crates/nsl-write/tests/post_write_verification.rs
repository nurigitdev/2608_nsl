mod guard_support;
mod invocation_support;
mod requested_support;
mod support;
mod verification_support;
use guard_support as g;
use nsl_core::*;
use nsl_write::*;
use requested_support as r;
use std::{
    future::Future,
    sync::atomic::Ordering,
    task::{Context, Poll, Waker},
};
use support::*;
use verification_support as v;
#[test]
fn verification_models_match_original_oracle_and_preserve_state_constraints() {
    let mut count = 0;
    for (name, d, e) in g::rows(include_str!("fixtures/verification-models-oracle.tsv")) {
        let value = field(&d, "value");
        let result = match get(&d, "kind") {
            "request" => v::request(value).and_then(|r| r.to_data()),
            "observation" => v::observation(value).map(|r| r.to_data()),
            "result" => v::result(value).map(|r| r.to_data()),
            _ => panic!(),
        };
        match result {
            Ok(value) => {
                assert_eq!(text(&arr(&e)[0]), "ok", "{name}");
                assert_eq!(value, arr(&e)[1], "{name}");
            }
            Err(error) => {
                assert_eq!(text(&arr(&e)[0]), "error", "{name}");
                assert_eq!(error.detail_code(), None);
                assert!(error.path.starts_with("$."));
                assert!(!format!("{error:?} {error}").contains("synthetic-only"));
            }
        }
        count += 1;
    }
    assert_eq!(count, 229);
}
#[test]
fn verification_request_canonical_bytes_and_sha256_bind_every_field_like_oracle() {
    let mut count = 0;
    let mut hashes = std::collections::BTreeSet::new();
    for (name, d, e) in g::rows(include_str!("fixtures/verification-hashes-oracle.tsv")) {
        let request = v::request(&d).unwrap();
        assert_eq!(v::hashed(&request).unwrap(), arr(&e)[1], "{name}");
        hashes.insert(request.request_hash().unwrap().to_string());
        count += 1;
    }
    assert_eq!(count, 27);
    assert!(hashes.len() >= 24);
}
#[test]
fn persisted_outcomes_drive_read_only_verification_and_match_all_ordinary_oracle_cases() {
    let mut count = 0;
    let mut no_read = 0;
    for (name, d) in v::rows(include_str!("fixtures/verification-normal-oracle.tsv")) {
        let (actual, _, calls) = v::observed(&d);
        assert_eq!(actual, *field(&d, "observed"), "{name}");
        assert!(calls <= 1);
        if calls == 0 {
            no_read += 1;
        }
        count += 1;
    }
    assert_eq!(count, 28);
    assert!(no_read > 0);
}
#[test]
fn approved_contract_and_zero_tolerance_freshness_differences_preserve_original_oracle() {
    let mut count = 0;
    for (name, d) in v::rows(include_str!("fixtures/verification-exceptions-oracle.tsv")) {
        let (actual, setup, calls) = v::observed(&d);
        let case = get(field(&d, "config"), "case");
        let oracle = field(&d, "observed");
        assert_eq!(text(&arr(field(oracle, "result"))[0]), "ok");
        assert_eq!(
            setup.persisted.invocation().outcome().commit_state(),
            CommitState::Committed
        );
        if case.starts_with("contract-") {
            assert_eq!(text(&arr(field(&actual, "result"))[0]), "error");
            assert_eq!(calls, 0, "{name}");
            let c = field(&d, "context");
            let keys = v::string_map(field(c, "keys_by_write_node"));
            let payloads = v::string_map(field(c, "payload_hashes_by_write_node"));
            let contract = v::contract(field(&d, "selected_contract"));
            let error = r::ready(PostWriteVerifier.verify(
                &setup.persisted,
                &contract,
                VerificationContext {
                    tenant_id: get(c, "tenant_id"),
                    business_operation_id: Some("business-0053"),
                    keys_by_write_node: &keys,
                    payload_hashes_by_write_node: &payloads,
                },
                None,
            ))
            .unwrap_err();
            assert_eq!(error.kind, VerificationErrorKind::Binding);
            assert_eq!(error.path, "$.contract_hash");
            assert_eq!(error.detail_code(), None);
        } else {
            assert_eq!(calls, 1);
            let result = &arr(field(&actual, "result"))[1];
            assert_eq!(get(result, "status"), "UNKNOWN");
            assert_eq!(get(result, "commit_state"), "COMMITTED");
            assert_eq!(get(result, "reason_code"), "VERIFICATION_OBSERVATION_STALE");
            assert_eq!(field(result, "evidence_ref"), &ValueData::Null);
            assert_eq!(field(result, "observed_at"), &ValueData::Null);
            assert_ne!(get(&arr(field(oracle, "result"))[1], "status"), "UNKNOWN");
            assert_eq!(field(&actual, "requests"), field(oracle, "requests"));
        }
        count += 1;
    }
    assert_eq!(count, 5);
}
#[test]
fn optional_schemas_keep_existing_external_type_rejection_at_every_depth() {
    let (_, d, _) = g::rows(include_str!("fixtures/verification-hashes-oracle.tsv"))
        .next()
        .unwrap();
    for depth in [0, 1, 3] {
        let mut schema = TypeRef::new(TypeKind::Optional(Box::new(
            TypeRef::new(TypeKind::Primitive("String".into())).unwrap(),
        )))
        .unwrap();
        for _ in 0..depth {
            schema = TypeRef::new(TypeKind::List(Box::new(schema))).unwrap();
        }
        let mut spec = v::request_spec(&d);
        spec.reference_schema = Some(schema);
        let request = VerificationRequest::new(spec).unwrap();
        let error = request.request_hash().unwrap_err();
        assert_eq!(
            error.kind,
            VerificationErrorKind::Type(TypeError::InternalOptional)
        );
        assert_eq!(error.path, "$.reference_schema");
    }
}
#[test]
fn cancelling_or_dropping_verification_never_reclassifies_or_repeats_the_committed_write() {
    struct Pending;
    impl PostWriteVerificationPort for Pending {
        fn read<'a>(&'a self, _: &'a VerificationRequest) -> VerificationFuture<'a> {
            Box::pin(std::future::pending())
        }
    }
    let (_, d) = v::rows(include_str!("fixtures/verification-normal-oracle.tsv"))
        .next()
        .unwrap();
    let setup = v::setup(&d);
    let c = field(&d, "context");
    let keys = v::string_map(field(c, "keys_by_write_node"));
    let payloads = v::string_map(field(c, "payload_hashes_by_write_node"));
    let contract = v::contract(field(&d, "selected_contract"));
    let before = invocation_support::outcome_data(setup.persisted.invocation().outcome());
    let journal = setup.journal.clone();
    let mut future = Box::pin(PostWriteVerifier.verify(
        &setup.persisted,
        &contract,
        VerificationContext {
            tenant_id: "tenant-a",
            business_operation_id: Some("business-0053"),
            keys_by_write_node: &keys,
            payload_hashes_by_write_node: &payloads,
        },
        Some(&Pending),
    ));
    assert!(matches!(
        future
            .as_mut()
            .poll(&mut Context::from_waker(Waker::noop())),
        Poll::Pending
    ));
    drop(future);
    assert_eq!(
        before,
        invocation_support::outcome_data(setup.persisted.invocation().outcome())
    );
    assert_eq!(journal, setup.journal);
    assert_eq!(setup.write_calls.load(Ordering::SeqCst), 1);
}

#[test]
fn unencodable_contracts_fail_before_read_and_model_debugs_hide_business_metadata() {
    use nsl_contract::*;
    let (_, d) = v::rows(include_str!("fixtures/verification-normal-oracle.tsv"))
        .next()
        .unwrap();
    let setup = v::setup(&d);
    let c = v::contract(field(&d, "selected_contract"));
    let invalid = ToolContract::new(ToolContractSpec {
        tool_id: c.tool_id().into(),
        version: c.version().into(),
        capability: c.capability().as_str().into(),
        input_types: vec![(
            "detail".into(),
            TypeRef::new(TypeKind::Optional(Box::new(
                TypeRef::new(TypeKind::Primitive("String".into())).unwrap(),
            )))
            .unwrap(),
        )],
        output_type: c.output_type().clone(),
        required_scope: c.required_scope().into(),
        output_classification: c.output_classification(),
        options: ToolContractOptions {
            operation_kind: Some("CREATE".into()),
            risk_level: Some("R3".into()),
            write_semantics: c.write_semantics().cloned(),
            ..Default::default()
        },
    })
    .unwrap();
    let ctx = field(&d, "context");
    let keys = v::string_map(field(ctx, "keys_by_write_node"));
    let payloads = v::string_map(field(ctx, "payload_hashes_by_write_node"));
    let port = v::Read {
        config: field(&d, "config").clone(),
        requests: std::sync::Mutex::new(vec![]),
    };
    let error = r::ready(PostWriteVerifier.verify(
        &setup.persisted,
        &invalid,
        VerificationContext {
            tenant_id: "tenant-a",
            business_operation_id: Some("business-0053"),
            keys_by_write_node: &keys,
            payload_hashes_by_write_node: &payloads,
        },
        Some(&port),
    ))
    .unwrap_err();
    assert_eq!(
        error.kind,
        VerificationErrorKind::ContractEncoding(ContractEncodingError::Type(
            TypeError::InternalOptional
        ))
    );
    assert_eq!(error.detail_code(), None);
    assert!(port.requests.lock().unwrap().is_empty());
    let (_, request_data, _) = g::rows(include_str!("fixtures/verification-hashes-oracle.tsv"))
        .next()
        .unwrap();
    let mut spec = v::request_spec(&request_data);
    spec.business_reference = "private-business-reference".into();
    assert!(!format!("{spec:?}").contains("private"));
    let request = VerificationRequest::new(spec).unwrap();
    assert!(!format!("{request:?}").contains("private"));
    let spec = VerificationObservationSpec {
        request_hash: request.request_hash().unwrap().to_string(),
        business_reference: "private-business-reference".into(),
        matched: true,
        evidence_ref: "private-evidence".into(),
        observed_at: "2026-10-20T03:00:02Z".parse().unwrap(),
        reason_code: None,
    };
    assert!(!format!("{spec:?}").contains("private"));
    let observation = VerificationObservation::new(spec).unwrap();
    assert!(!format!("{observation:?}").contains("private"));
    let spec = VerificationResultSpec {
        identity: request.metadata().identity.clone(),
        commit_state: CommitState::Committed,
        status: VerificationStatus::Verified,
        business_reference: "private-business-reference".into(),
        verification_tool_id: Some("PRIVATE.READ".into()),
        reason_code: None,
        evidence_ref: Some("private-evidence".into()),
        observed_at: Some(observation.metadata().observed_at),
    };
    assert!(!format!("{spec:?}").contains("private"));
    let result = VerificationResult::new(spec).unwrap();
    assert!(!format!("{result:?}").contains("private"));
    assert_eq!(result.metadata().commit_state, CommitState::Committed);
    assert_eq!(
        result.metadata().business_reference,
        "private-business-reference"
    );
    // Contract metadata can be a valid owned model while its method identity
    // violates the request's credential rule. Reject before invoking READ.
    let mut credential = d.clone();
    for key in ["original_contract", "selected_contract"] {
        let ValueData::Object(root) = &mut credential else {
            panic!()
        };
        let ValueData::Object(contract) = root.get_mut(key).unwrap() else {
            panic!()
        };
        let ValueData::Object(write) = contract.get_mut("write_semantics").unwrap() else {
            panic!()
        };
        let ValueData::Object(verification) = write.get_mut("verification").unwrap() else {
            panic!()
        };
        verification.insert("tool_id".into(), txt("Bearer synthetic-only"));
    }
    let setup = v::setup(&credential);
    let contract = v::contract(field(&credential, "selected_contract"));
    let error = r::ready(PostWriteVerifier.verify(
        &setup.persisted,
        &contract,
        VerificationContext {
            tenant_id: "tenant-a",
            business_operation_id: Some("business-0053"),
            keys_by_write_node: &keys,
            payload_hashes_by_write_node: &payloads,
        },
        Some(&port),
    ))
    .unwrap_err();
    assert_eq!(error.kind, VerificationErrorKind::Model);
    assert_eq!(error.path, "$.verification_tool_id");
    assert_eq!(error.detail_code(), None);
    assert!(port.requests.lock().unwrap().is_empty());
    assert!(!format!("{error:?} {error}").contains("synthetic-only"));
}
