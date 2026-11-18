mod tool_support;
use nsl_contract::ToolContractCatalog;
use nsl_core::*;
use nsl_runtime::*;
use std::{collections::BTreeMap, sync::Arc};
use tool_support::*;

#[test]
fn frozen_tool_contract_validation_mock_and_measurement() {
    let rt = runtime();
    let mut count = 0;
    for line in include_str!("fixtures/tools-oracle.tsv")
        .lines()
        .filter(|l| !l.starts_with('#'))
    {
        let f: Vec<_> = line.split('\t').collect();
        let c = contract(ty("Int"));
        let mut q = request(&c);
        let mut r = result();
        let observed = match f[0] {
            "conform" => ValueData::Bool(tool_value_conforms_to_type(&value(f[2]), &ty(f[1]))),
            "hash" => s(tool_result_hash(&value(f[1]), 1000).unwrap().to_string()),
            "request" => {
                match f[1] {
                    "hash" | "hash_and_names" => q.contract_hash = "wrong".into(),
                    _ => {}
                }
                match f[1] {
                    "hash_and_names" | "missing" => q.arguments.clear(),
                    "extra" => {
                        q.arguments
                            .insert("extra".into(), env(Value::Int(1.into()), ty("Int")));
                    }
                    "renamed" => {
                        let v = q.arguments.remove("a").unwrap();
                        q.arguments.insert("other".into(), v);
                    }
                    "type" => {
                        q.arguments
                            .insert("a".into(), env(value("seven"), ty("String")));
                    }
                    "value" => {
                        q.arguments
                            .insert("a".into(), env(Value::Bool(true), ty("Int")));
                    }
                    "both_values" => {
                        for k in ["a", "z"] {
                            q.arguments
                                .insert(k.into(), env(Value::Bool(true), ty("Int")));
                        }
                    }
                    "identity" => {
                        q.tool_id = "OTHER".into();
                        q.tool_version = "9.0.0".into();
                    }
                    "empty_auth" => q.authorization_decision_ref.clear(),
                    _ => {}
                }
                observe(ToolContractValidator.validate_request(&q, &c))
            }
            "structure" => {
                match f[1] {
                    "invocation" | "identity_and_snapshot" => r.invocation_id = "other".into(),
                    "tool" => r.tool_id = "OTHER".into(),
                    "version" => r.tool_version = "2.0.0".into(),
                    _ => {}
                }
                match f[1] {
                    "snapshot_empty" | "identity_and_snapshot" => r.snapshot_ref = Some("".into()),
                    "snapshot_blank" => r.snapshot_ref = Some(" ".into()),
                    "snapshot_value" => r.snapshot_ref = Some("snapshot:a".into()),
                    _ => {}
                }
                observe(
                    ToolContractValidator
                        .validate_result_structure((f[1] != "none").then_some(&r), &q)
                        .map(|_| ())
                        .map_err(Into::into),
                )
            }
            "result" | "result_hash" => {
                match f[1] {
                    "type" | "metadata_and_value" => r.type_info = ty("String"),
                    "class" => r.classification = DataClassification::Restricted,
                    "presence" => r.presence = Presence::Empty,
                    "completeness" => r.completeness = Completeness::Partial,
                    "hash" => r.result_hash = format!("sha256:{}", "0".repeat(64)),
                    "hash_shape" => r.result_hash.clear(),
                    _ => {}
                }
                if ["value", "metadata_and_value"].contains(&f[1]) {
                    r.value = Value::Bool(true);
                }
                observe(if f[0] == "result_hash" {
                    ToolContractValidator.validate_result_hash(&r, 100)
                } else {
                    ToolContractValidator
                        .validate_result(&r, &c)
                        .map_err(Into::into)
                })
            }
            "provenance" => {
                let v = r.to_value("trace:a".into());
                arr([
                    v.value().to_data(100).unwrap(),
                    s(v.presence().as_str()),
                    s(v.completeness().as_str()),
                    s(v.classification().as_str()),
                    arr(v.provenance_refs().iter().map(s)),
                ])
            }
            "mock" => {
                let variant = f[1];
                let c = contract(ty(match variant {
                    "empty_list" => "ListInt",
                    "empty_record" => "EmptyRecord",
                    _ => "Int",
                }));
                let mut q = request(&c);
                if variant == "version" {
                    q.tool_version = "2.0.0".into();
                }
                if variant == "bad_request" {
                    q.contract_hash = "wrong".into();
                }
                let v = value(match variant {
                    "empty_list" => "list_empty",
                    "empty_record" => "record_empty",
                    "wrong_output" => "true",
                    _ => "seven",
                });
                let completeness = match variant {
                    "partial" => Completeness::Partial,
                    "unknown" => Completeness::Unknown,
                    _ => Completeness::Complete,
                };
                let fail = variant == "handler_error";
                let handler: FixtureHandler = Arc::new(move |args| {
                    assert_eq!(
                        args,
                        &BTreeMap::from([
                            ("a".into(), value("seven")),
                            ("z".into(), Value::Int(9.into()))
                        ])
                    );
                    if fail {
                        return Err(ToolCallError::Provider("synthetic handler failure".into()));
                    }
                    Ok(MockToolResult {
                        value: v.clone(),
                        completeness,
                    })
                });
                let catalog = ToolContractCatalog::new(if variant == "missing_contract" {
                    vec![]
                } else {
                    vec![c]
                })
                .unwrap();
                let e = MockToolExecutor::new(
                    catalog,
                    if variant == "missing_handler" {
                        BTreeMap::new()
                    } else {
                        BTreeMap::from([("TEST.ECHO".into(), handler)])
                    },
                    100,
                );
                arr((0..if variant == "repeat" { 2 } else { 1 }).map(|_| {
                    let r = rt.block_on(e.execute(&q, CancellationSignal::default()));
                    match r {
                        Ok(r) => arr([
                            s("ok"),
                            r.value.to_data(100).unwrap(),
                            s(r.presence.as_str()),
                            s(r.completeness.as_str()),
                            s(r.classification.as_str()),
                            s(r.result_hash),
                            r.snapshot_ref.map_or(ValueData::Null, s),
                            n(e.call_count().as_str()),
                        ]),
                        Err(e2) => {
                            let ValueData::Array(mut a) = error(e2) else {
                                panic!()
                            };
                            a.push(n(e.call_count().as_str()));
                            arr(a)
                        }
                    }
                }))
            }
            "measurement_id" | "measurement_duration" => {
                let mut m = ToolExecutionMeasurement {
                    execution_id: q.execution_id,
                    invocation_id: q.invocation_id,
                    node_id: q.node_id,
                    tool_id: q.tool_id,
                    tool_version: q.tool_version,
                    tenant_id: q.principal.tenant_id,
                    outcome: ToolExecutionOutcome::Returned,
                    duration_ns: 0.into(),
                };
                if f[0] == "measurement_id" {
                    let v = decode(f[2]);
                    match f[1] {
                        "execution_id" => m.execution_id = v,
                        "invocation_id" => m.invocation_id = v,
                        "node_id" => m.node_id = v,
                        "tool_id" => m.tool_id = v,
                        "tool_version" => m.tool_version = v,
                        "tenant_id" => m.tenant_id = v,
                        _ => panic!(),
                    };
                    observe(m.validate())
                } else {
                    m.duration_ns = f[1].parse().unwrap();
                    m.outcome = match f[2] {
                        "RETURNED" => ToolExecutionOutcome::Returned,
                        "ERROR" => ToolExecutionOutcome::Error,
                        "CANCELLED" => ToolExecutionOutcome::Cancelled,
                        _ => panic!(),
                    };
                    m.validate()
                        .map_or_else(error, |()| arr([s("ok"), n(m.duration_ms().as_str())]))
                }
            }
            _ => panic!("{line}"),
        };
        assert_eq!(json(&observed), decode(f.last().unwrap()), "{line}");
        count += 1;
    }
    assert_eq!(count, 642);
}
