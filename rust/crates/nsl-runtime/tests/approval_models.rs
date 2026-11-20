mod approval_support;
use approval_support as h;
use nsl_core::*;
use nsl_runtime::*;

#[test]
fn request_evidence_fields_constructor_rejections_and_owned_summary_match_oracle() {
    let mut count = 0;
    for line in include_str!("fixtures/approval-models-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let observed = h::json(&h::outcome(h::model(&f)));
        let expected = h::decode(f[8]);
        if expected.starts_with("[\"error\",\"CredentialMaterialError\",") {
            assert_eq!(observed, "[\"error\",\"CredentialMaterialError\"]");
        } else {
            assert_eq!(observed, expected, "{} {} {} {}", f[0], f[1], f[2], f[3]);
        }
        count += 1;
    }
    assert_eq!(count, 536);
}
#[test]
fn original_float_outcomes_remain_frozen_and_rust_rejects_at_the_exact_nested_path() {
    let mut count = 0;
    for line in include_str!("fixtures/approval-summary-exceptions.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let input = h::summary(f[1]);
        let error = SafeApprovalSummary::new(&input, h::limits()).unwrap_err();
        assert_eq!(error.kind, ApprovalSummaryErrorKind::IntegerRequired);
        assert_eq!(
            error.path,
            if f[1].starts_with("nested-") {
                "display_safe_summary.nested.items[1].ratio"
            } else {
                "display_safe_summary.ratio"
            }
        );
        assert!(std::error::Error::source(&error).is_none());
        assert!(!error.to_string().contains("NSL-E"));
        let expected = h::decode(f[8]);
        let index = f[1].chars().last().unwrap().to_digit(10).unwrap();
        assert_eq!(
            expected.starts_with("[\"ok\","),
            index < 4,
            "finite numbers remain Oracle successes; NaN/Infinity remain original ValueErrors"
        );
        count += 1;
    }
    assert_eq!(count, 14);
}
#[test]
fn structural_bindings_and_exact_ttl_boundaries_match_the_authenticated_oracle_observations() {
    let mut count = 0;
    for line in include_str!("fixtures/approval-bindings-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let mut s = h::evidence_spec();
        s.decision = f[0].parse().unwrap();
        s.expires_at = match f[1] {
            "none" => None,
            "zero" => Some(s.decided_at),
            _ => s.expires_at,
        };
        let evidence = ApprovalEvidence::new(s).unwrap();
        let mut b = h::binding();
        let fields = if f[3] == "all" {
            vec![
                "tenant_id",
                "execution_id",
                "business_operation_id",
                "approval_id",
                "approval_request_id",
                "skill_semantic_hash",
                "payload_hash",
                "write_intent_hash",
            ]
        } else {
            vec![f[3]]
        };
        for field in fields {
            if field != "-" {
                h::set(
                    &mut b,
                    field,
                    &if matches!(field, "skill_semantic_hash" | "payload_hash") {
                        h::hash('c')
                    } else {
                        "other".into()
                    },
                );
            }
        }
        let b = ApprovalBindings::new(b).unwrap();
        let now = h::instant(match f[2] {
            "-1" => "2026-10-20T01:02:02.999999+00:00",
            "0" => "2026-10-20T01:02:03+00:00",
            "1" => "2026-10-20T01:02:03.000001+00:00",
            "3599999999" => "2026-10-20T02:02:02.999999+00:00",
            "3600000000" => "2026-10-20T02:02:03+00:00",
            _ => "2026-10-20T02:02:03.000001+00:00",
        });
        // Adapter observes only structural binding/time/decision semantics. Signature
        // authentication and resume claim consumption remain R7-S04 production work.
        let (validation, status, reason) = if let Err(e) = evidence.check_bindings(&b) {
            (
                match e {
                    ApprovalBindingMismatch::BusinessOperation => "BUSINESS_OPERATION_MISMATCH",
                    ApprovalBindingMismatch::ApprovalRequest => "APPROVAL_REQUEST_MISMATCH",
                    ApprovalBindingMismatch::WriteIntent => "INTENT_MISMATCH",
                    ApprovalBindingMismatch::Payload => "PAYLOAD_MISMATCH",
                    _ => "INVALID",
                },
                "PENDING",
                Some(e.reason_code()),
            )
        } else if evidence.is_expired_at(now) {
            ("EXPIRED", "EXPIRED", Some("APPROVAL_EXPIRED"))
        } else if evidence.metadata().decision == ApprovalDecision::Rejected {
            ("VALID", "REJECTED", Some("APPROVAL_REJECTED"))
        } else {
            ("VALID", "APPROVED", None)
        };
        let observed = h::arr([
            h::txt(validation),
            h::txt(status),
            reason.map_or(ValueData::Null, h::txt),
        ]);
        assert_eq!(
            h::json(&observed),
            h::decode(f[4]),
            "{}",
            line.split('\t').take(4).collect::<Vec<_>>().join("/")
        );
        count += 1;
    }
    assert_eq!(count, 360);
}
#[test]
fn utc_instants_normalize_and_reject_naive_and_nonzero_offsets_like_oracle() {
    let mut count = 0;
    for line in include_str!("fixtures/approval-time-oracle.tsv")
        .lines()
        .skip(1)
    {
        let f = line.split('\t').collect::<Vec<_>>();
        let text = h::decode(f[0]);
        let expected = h::decode(f[1]);
        match text.parse::<UtcInstant>() {
            Ok(t) => {
                assert_eq!(
                    h::json(&h::arr([h::txt("ok"), h::txt(t.to_string())])),
                    expected
                );
                assert_eq!(t.to_string().parse::<UtcInstant>().unwrap(), t);
            }
            Err(e) => {
                assert_eq!(e, UtcInstantError::NotUtc);
                assert!(expected.starts_with("[\"error\",\"ValueError\","));
            }
        }
        count += 1;
    }
    assert_eq!(count, 11);
}

#[test]
fn summary_budgets_owned_snapshots_and_number_paths_cover_exact_boundaries() {
    use ApprovalSummaryInput as I;
    let input = h::map([(
        "x",
        I::Array(vec![I::Number("1".into()), I::Text("가😀".into())]),
    )]);
    for axis in 0..3 {
        let maximum = [2, 4, 9][axis];
        for limit in [0, 1, maximum - 1, maximum, maximum + 1] {
            let mut limits = h::limits();
            match axis {
                0 => limits.max_depth = limit,
                1 => limits.max_nodes = limit,
                _ => limits.max_text_bytes = limit,
            }
            let result = SafeApprovalSummary::new(&input, limits);
            if limit < maximum {
                let e = result.unwrap_err();
                assert_eq!(
                    e.kind,
                    [
                        ApprovalSummaryErrorKind::Depth,
                        ApprovalSummaryErrorKind::Nodes,
                        ApprovalSummaryErrorKind::TextBytes
                    ][axis]
                );
                assert!(!e.to_string().is_empty());
                assert!(std::error::Error::source(&e).is_none());
            } else {
                assert_eq!(h::json(result.unwrap().data()), "{\"x\":[1,\"가😀\"]}");
            }
        }
    }
    assert!(
        SafeApprovalSummary::new(
            &h::map([]),
            ApprovalSummaryLimits {
                max_depth: 0,
                max_nodes: 1,
                max_text_bytes: 0
            }
        )
        .is_ok()
    );
    let mut source = h::summary("all");
    let frozen = SafeApprovalSummary::new(&source, h::limits()).unwrap();
    let previous = frozen.data().clone();
    if let I::Object(fields) = &mut source {
        fields.clear();
    }
    assert_eq!(frozen.data(), &previous);
    assert_eq!(format!("{frozen:?}"), "SafeApprovalSummary { .. }");
    let normalized = SafeApprovalSummary::new(
        &h::map([
            ("n", I::Number("-0".into())),
            ("ratio", I::Text("0.5".into())),
        ]),
        h::limits(),
    )
    .unwrap();
    assert_eq!(h::json(normalized.data()), "{\"n\":0,\"ratio\":\"0.5\"}");
    for token in [
        "0.5", "1.0", "-0.0", "1e0", "NaN", "Infinity", "", "01", "+1",
    ] {
        let e = SafeApprovalSummary::new(
            &h::map([("a", I::Array(vec![I::Null, I::Number(token.into())]))]),
            h::limits(),
        )
        .unwrap_err();
        assert_eq!(e.path, "display_safe_summary.a[1]");
        assert_eq!(e.kind, ApprovalSummaryErrorKind::IntegerRequired);
    }
    for recipe in [
        "not-mapping",
        "blank-key",
        "credential-key",
        "credential-text",
    ] {
        let e = SafeApprovalSummary::new(&h::summary(recipe), h::limits()).unwrap_err();
        assert!(!e.to_string().is_empty());
    }
}
#[test]
fn approval_identity_getters_decisions_expiry_and_error_surfaces_do_not_grant_authority() {
    let mut request = h::request_spec("all").unwrap();
    let original = request.display_safe_summary.data().clone();
    let model = ApprovalRequest::new(request.clone()).unwrap();
    request.message.clear();
    assert_eq!(model.metadata().message, "Review draft");
    assert_eq!(model.metadata().display_safe_summary.data(), &original);
    let bindings = model.bindings();
    assert_eq!(bindings.tenant_id(), "tenant-a");
    assert_eq!(bindings.execution_id(), "exec-a");
    assert_eq!(bindings.business_operation_id().as_str(), "business-a");
    assert_eq!(bindings.approval_id(), "APPROVE");
    assert_eq!(bindings.approval_request_id().as_str(), "request-a");
    assert_eq!(bindings.skill_semantic_hash().as_str(), h::hash('a'));
    assert_eq!(bindings.payload_hash().as_str(), h::hash('b'));
    assert_eq!(bindings.write_intent_hash(), "write-intent-a");
    assert!(!format!("{model:?}").contains("Review draft"));
    let evidence = ApprovalEvidence::new(h::evidence_spec()).unwrap();
    assert_eq!(evidence.bindings(), bindings);
    assert_eq!(format!("{evidence:?}"), "ApprovalEvidence { .. }");
    for (s, expired) in [
        ("2026-10-20T02:02:02.999999+00:00", false),
        ("2026-10-20T02:02:03+00:00", true),
        ("2026-10-20T02:02:03.000001+00:00", true),
    ] {
        let now = h::instant(s);
        assert_eq!(evidence.is_expired_at(now), expired);
        assert_eq!(model.is_expired_at(now), expired);
    }
    let mut no_expiry = h::request_spec("empty").unwrap();
    no_expiry.expires_at = None;
    assert!(
        !ApprovalRequest::new(no_expiry)
            .unwrap()
            .is_expired_at(h::instant("9999-12-31T23:59:59.999999+00:00"))
    );
    for mismatch in [
        ApprovalBindingMismatch::BusinessOperation,
        ApprovalBindingMismatch::ApprovalRequest,
        ApprovalBindingMismatch::Payload,
        ApprovalBindingMismatch::WriteIntent,
        ApprovalBindingMismatch::Identity,
    ] {
        assert_eq!(mismatch.to_string(), mismatch.reason_code());
        assert!(std::error::Error::source(&mismatch).is_none());
    }
    for e in [
        ApprovalModelError::Credential(CredentialMaterialError),
        ApprovalModelError::Hash {
            field: "payload_hash".into(),
        },
        ApprovalModelError::Required {
            field: "tenant_id".into(),
        },
        ApprovalModelError::ExpiryBeforeDecision,
    ] {
        assert!(!e.to_string().is_empty());
        assert!(std::error::Error::source(&e).is_none());
    }
}
#[test]
fn bounded_generated_nested_summaries_reject_the_last_float_without_mutating_source() {
    use ApprovalSummaryInput as I;
    for length in 0..128 {
        let input = h::map([(
            "items",
            I::Array(
                (0..length)
                    .map(|i| {
                        if i + 1 == length {
                            I::Number("0.5".into())
                        } else {
                            I::Number(i.to_string())
                        }
                    })
                    .collect(),
            ),
        )]);
        let result = SafeApprovalSummary::new(&input, h::limits());
        if length == 0 {
            assert_eq!(h::json(result.unwrap().data()), "{\"items\":[]}");
        } else {
            assert_eq!(
                result.unwrap_err().path,
                format!("display_safe_summary.items[{}]", length - 1)
            );
        }
    }
    let mut deep = I::Number("0".into());
    for _ in 0..129 {
        deep = I::Array(vec![deep]);
    }
    let e = SafeApprovalSummary::new(&h::map([("deep", deep)]), h::limits()).unwrap_err();
    assert_eq!(e.kind, ApprovalSummaryErrorKind::Depth);
}
