mod support;
use nsl_core::{CheckStatus, ValueData};
use nsl_runtime::*;
use support::*;
#[test]
fn namespace_immutability_scope_reuse_and_completeness_match_oracle() {
    let mut count = 0;
    for row in include_str!("fixtures/contexts-oracle.tsv")
        .lines()
        .filter(|r| !r.starts_with('#'))
    {
        let c = row.split('\t').collect::<Vec<_>>();
        let clock = Clock::default();
        let mut ctx = ExecutionContext::with_clock(skill(), request(), &clock);
        let mut observations = Vec::new();
        for action in c[1].split(';') {
            let a = action.split(':').collect::<Vec<_>>();
            let value = match a[0] {
                "bind" => ctx
                    .bind(
                        match a[1] {
                            "i" => BindingNamespace::Input,
                            "c" => BindingNamespace::Context,
                            "v" => BindingNamespace::Variable,
                            "k" => BindingNamespace::Check,
                            _ => panic!(),
                        },
                        a[2].into(),
                        envelope(a[3].parse().unwrap()),
                    )
                    .map(|_| None),
                "resolve" => ctx.resolve(a[1]).map(|v| {
                    let nsl_core::Value::Int(n) = v.value() else {
                        panic!()
                    };
                    Some(n.as_str().to_owned())
                }),
                "push" => {
                    ctx.push_frame();
                    Ok(None)
                }
                "pop" => ctx.pop_frame().map(|_| None),
                "observe" => {
                    ctx.observe_completeness(a[1].parse().unwrap());
                    Ok(None)
                }
                "content" => {
                    if matches!(a[1], "check" | "both") {
                        ctx.checks.push(check("C", CheckStatus::Pass));
                    }
                    if matches!(a[1], "output" | "both") {
                        ctx.outputs.push(EmitRecord::new(vec![]).unwrap());
                    }
                    Ok(None)
                }
                "result" => Ok(Some(
                    ctx.result_completeness(a[1].parse().unwrap())
                        .as_str()
                        .to_owned(),
                )),
                _ => panic!(),
            };
            let (outcome, message) = match value {
                Ok(s) => ("ok", s),
                Err(e) => (
                    if matches!(e, BindingError::Immutable(_)) {
                        "ImmutableBindingError"
                    } else {
                        "RuntimeError"
                    },
                    Some(e.to_string()),
                ),
            };
            observations.push(ValueData::Array(vec![
                text(outcome),
                optional(message.as_deref()),
                ValueData::Number((ctx.frame_count() as i64).into()),
                text(ctx.data_completeness().as_str()),
            ]));
        }
        assert_eq!(
            json(&ValueData::Array(observations)),
            decode(c[2]),
            "context {}",
            c[0]
        );
        count += 1;
    }
    assert_eq!(count, 31);
}
