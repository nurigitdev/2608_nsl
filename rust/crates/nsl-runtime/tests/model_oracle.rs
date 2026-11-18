mod support;
use nsl_core::{
    ApprovalStatus, CheckStatus, DataClassification, Value, ValueData, VerificationStatus,
};
use nsl_runtime::*;
use std::collections::BTreeMap;
use std::error::Error;
use support::*;
fn status(s: &str) -> ValidationSummaryStatus {
    match s {
        "ALL_PASS" => ValidationSummaryStatus::AllPass,
        "HAS_FAIL" => ValidationSummaryStatus::HasFail,
        "HAS_UNKNOWN" => ValidationSummaryStatus::HasUnknown,
        "HAS_FAIL_AND_UNKNOWN" => ValidationSummaryStatus::HasFailAndUnknown,
        "NO_CHECK" => ValidationSummaryStatus::NoCheck,
        _ => panic!("status"),
    }
}
fn outcome(value: Result<ValueData, ModelError>, expected: &str, message: &str, label: &str) {
    match value {
        Ok(data) => {
            assert_eq!(expected, "ok", "{label}");
            assert_eq!(json(&data), decode(message), "{label}");
        }
        Err(error) => {
            assert!(!error.to_string().is_empty());
            if matches!(error, ModelError::Invalid(_)) {
                assert!(error.source().is_none());
            }
            match error {
                ModelError::Invalid(s) => {
                    assert_eq!(expected, "ValueError", "{label}");
                    assert_eq!(s, decode(message), "{label}");
                }
                ModelError::Credential(_) => {
                    assert_eq!(expected, "CredentialMaterialError", "{label}");
                    assert!(error.source().is_some());
                }
            }
        }
    }
}
#[test]
fn execution_models_admission_validation_and_result_views_match_oracle() {
    let mut count = 0;
    for row in include_str!("fixtures/models-oracle.tsv")
        .lines()
        .filter(|r| !r.starts_with('#'))
    {
        let c = row.split('\t').collect::<Vec<_>>();
        let n = c.len();
        if c[0] == "result" {
            let r = result(
                c[1].parse().unwrap(),
                c[2].parse().unwrap(),
                c[3].parse().unwrap(),
            );
            assert_eq!(
                String::from_utf8(r.to_json(1000).unwrap()).unwrap(),
                decode(c[4]),
                "result {} {} {}",
                c[1],
                c[2],
                c[3]
            );
            assert_eq!(json(&r.semantic_view(1000).unwrap()), decode(c[5]));
            count += 1;
            continue;
        }
        let data = match c[0] {
            "identity" => ExecutionIdentity::new(decode(c[1]), opt(c[2])).map(|v| {
                ValueData::Array(vec![
                    text(v.execution_id()),
                    optional(v.business_operation_id()),
                ])
            }),
            "policy" => DataHandlingPolicy::new(
                DataClassification::Internal,
                c[1].parse().unwrap(),
                c[2].parse().unwrap(),
            )
            .map(|p| {
                ValueData::Array(vec![
                    text(p.max_trace_classification().as_str()),
                    ValueData::Number(p.snapshot_retention_days().clone()),
                    ValueData::Number(p.audit_retention_days().clone()),
                ])
            }),
            "principal" => {
                let mut p = principal();
                match c[1] {
                    "verified" => p.verification = PrincipalVerification::Verified,
                    "tenant" => p.tenant_id = " ".into(),
                    "subject" => p.subject_id = String::new(),
                    "actor" => p.actor_type = "OTHER".into(),
                    "auth" => p.auth_context_ref = String::new(),
                    "roles" => p.roles = [" ".into()].into(),
                    "scopes" => p.scopes = [String::new()].into(),
                    "behalf" => p.on_behalf_of = Some(" ".into()),
                    "credential" => p.auth_context_ref = "Bearer synthetic".into(),
                    _ => {}
                }
                match p.validate(c[2] == "1") {
                    Ok(()) => {
                        assert_eq!(c[n - 2], "ok");
                        assert_eq!(decode(c[n - 1]), "null");
                    }
                    Err(e) => {
                        assert_eq!(c[n - 2], "AuthorizationError");
                        assert_eq!(e.to_string(), decode(c[n - 1]));
                    }
                }
                count += 1;
                continue;
            }
            "request" => {
                let raw = decode(c[3]);
                let value = match raw.as_str() {
                    "null" => Value::Null,
                    "\"\"" => Value::String(String::new()),
                    "\"hello\"" => Value::String("hello".into()),
                    "\"synthetic\"" => Value::String("synthetic".into()),
                    "\"Bearer synthetic\"" => Value::String("Bearer synthetic".into()),
                    "{\"nested\":[\"safe\",\"secret=synthetic\"]}" => {
                        Value::Record(BTreeMap::from([(
                            "nested".into(),
                            Value::List(vec![
                                Value::String("safe".into()),
                                Value::String("secret=synthetic".into()),
                            ]),
                        )]))
                    }
                    "{\"token\":null}" => {
                        Value::Record(BTreeMap::from([("token".into(), Value::Null)]))
                    }
                    _ => panic!("request input {raw}"),
                };
                let fields = BTreeMap::from([(decode(c[2]), value)]);
                let (i, ctx) = if c[1] == "input" {
                    (fields, BTreeMap::new())
                } else {
                    (BTreeMap::new(), fields)
                };
                ExecutionRequest::new(
                    "exec-a".into(),
                    i,
                    ctx,
                    ExecutionSecurityContext {
                        principal: principal(),
                        data_policy: DataHandlingPolicy::default(),
                    },
                    None,
                )
                .map(|r| text(r.identity().execution_id()))
            }
            "request_identity" => ExecutionRequest::new(
                decode(c[1]),
                Default::default(),
                Default::default(),
                ExecutionSecurityContext {
                    principal: principal(),
                    data_policy: DataHandlingPolicy::default(),
                },
                opt(c[2]),
            )
            .map(|r| text(r.identity().execution_id())),
            "write_field" => {
                let mut spec = write_spec("idem-short", "not-a-payload-hash");
                let value = decode(c[2]);
                match c[1] {
                    "tool_id" => spec.tool_id = value,
                    "node_id" => spec.node_id = value,
                    "provider_transaction_id" => spec.provider_transaction_id = Some(value),
                    "business_reference" => spec.business_reference = Some(value),
                    _ => panic!(),
                }
                WriteSummary::new(spec).map(|v| v.to_data())
            }
            "checks" => {
                let checks = if c[1] == "-" {
                    vec![]
                } else {
                    c[1].chars()
                        .enumerate()
                        .map(|(i, s)| {
                            check(
                                &format!("C{i}"),
                                match s {
                                    'P' => CheckStatus::Pass,
                                    'F' => CheckStatus::Fail,
                                    'U' => CheckStatus::Unknown,
                                    _ => panic!(),
                                },
                            )
                        })
                        .collect()
                };
                ValidationSummary::from_checks(&checks).map(|v| v.to_data())
            }
            "summary" => {
                let fail = c[4].parse::<i64>().unwrap().max(0);
                let unknown = c[5].parse::<i64>().unwrap().max(0);
                ValidationSummary::new(ValidationSummarySpec {
                    status: status(c[1]),
                    total_count: c[2].parse().unwrap(),
                    pass_count: c[3].parse().unwrap(),
                    fail_count: c[4].parse().unwrap(),
                    unknown_count: c[5].parse().unwrap(),
                    failed_check_ids: vec!["F".into(); fail as usize],
                    unknown_check_ids: vec!["U".into(); unknown as usize],
                })
                .map(|v| v.to_data())
            }
            "ids" => {
                let names = decode(c[2])
                    .split('\0')
                    .map(String::from)
                    .collect::<Vec<_>>();
                let (st, total, fail, unknown, fids, uids) = match c[1] {
                    "failed" => (ValidationSummaryStatus::HasFail, 2, 2, 0, names, vec![]),
                    "unknown" => (ValidationSummaryStatus::HasUnknown, 2, 0, 2, vec![], names),
                    "overlap" => (
                        ValidationSummaryStatus::HasFailAndUnknown,
                        4,
                        2,
                        2,
                        names,
                        vec!["A".into(), "B".into()],
                    ),
                    _ => panic!(),
                };
                ValidationSummary::new(ValidationSummarySpec {
                    status: st,
                    total_count: total.into(),
                    pass_count: 0.into(),
                    fail_count: fail.into(),
                    unknown_count: unknown.into(),
                    failed_check_ids: fids,
                    unknown_check_ids: uids,
                })
                .map(|v| v.to_data())
            }
            "emit" => EmitRecord::new(vec![(decode(c[1]), envelope(7))]).map(|v| {
                ValueData::Array(vec![
                    Value::Record(v.values()).to_data(100).unwrap(),
                    ValueData::Object(
                        v.classifications()
                            .iter()
                            .map(|(k, v)| (k.clone(), text(v.as_str())))
                            .collect(),
                    ),
                ])
            }),
            "write" => {
                WriteSummary::new(write_spec(&decode(c[1]), &decode(c[2]))).map(|w| w.to_data())
            }
            "optional" => {
                let s = decode(c[2]);
                match c[1] {
                    "approval" => {
                        ApprovalSummary::new(ApprovalStatus::Approved, s, None).map(|v| v.to_data())
                    }
                    "approval_request" => {
                        ApprovalSummary::new(ApprovalStatus::Approved, "A".into(), Some(s))
                            .map(|v| v.to_data())
                    }
                    "reason" => {
                        VerificationSummary::new(VerificationStatus::Unknown, Some(s), None)
                            .map(|v| v.to_data())
                    }
                    "evidence" => {
                        VerificationSummary::new(VerificationStatus::Unknown, None, Some(s))
                            .map(|v| v.to_data())
                    }
                    "suspension_reason" => SuspensionInfo::new(s, 1.into(), "A".into(), "R".into())
                        .map(|v| v.to_data()),
                    "suspension_approval" => {
                        SuspensionInfo::new("WAIT".into(), 1.into(), s, "R".into())
                            .map(|v| v.to_data())
                    }
                    "suspension_request" => {
                        SuspensionInfo::new("WAIT".into(), 1.into(), "A".into(), s)
                            .map(|v| v.to_data())
                    }
                    _ => panic!(),
                }
            }
            "suspension" => {
                SuspensionInfo::new("WAIT".into(), c[1].parse().unwrap(), "A".into(), "R".into())
                    .map(|v| v.to_data())
            }
            _ => panic!("unknown fixture {}", c[0]),
        };
        outcome(data, c[n - 2], c[n - 1], row);
        count += 1;
    }
    assert_eq!(count, 436);
}
