mod support;
use nsl_core::{Integer, ValueData};
use nsl_ir::ResourceLimits;
use nsl_runtime::*;
use support::*;
#[test]
fn nanosecond_deadlines_ceil_timeouts_counters_and_meter_mutation_match_oracle() {
    let mut count = 0;
    for row in include_str!("fixtures/guards-oracle.tsv")
        .lines()
        .filter(|r| !r.starts_with('#'))
    {
        let c = row.split('\t').collect::<Vec<_>>();
        let ns = |s: &str| {
            s.split(',')
                .map(|n| n.parse::<Integer>().unwrap())
                .collect::<Vec<_>>()
        };
        let l = ns(c[1]);
        let u = ns(c[2]);
        let clock = Clock::at(c[3].parse().unwrap());
        let mut guard = ResourceGuard::with_usage(
            ResourceLimits {
                tool_calls: l[0].clone(),
                loop_iterations: l[1].clone(),
                emitted_rows: l[2].clone(),
                collection_size: l[3].clone(),
                duration_ms: l[4].clone(),
            },
            ResourceUsage {
                tool_calls: u[0].clone(),
                loop_iterations: u[1].clone(),
                emitted_rows: u[2].clone(),
                max_collection_size_seen: u[3].clone(),
            },
            &clock,
        );
        let mut observations = Vec::new();
        for action in c[4].split(';') {
            let (kind, n) = action.split_once(':').unwrap();
            let number = n.parse().unwrap();
            let result = match kind {
                "time" => {
                    clock.set(number);
                    Ok(None)
                }
                "remaining" => guard
                    .remaining_duration_ms()
                    .map(|v| Some(v.as_str().to_owned())),
                "deadline" => guard.check_deadline().map(|_| None),
                "tool" => guard.before_tool_call().map(|_| None),
                "loop" => guard.before_loop_iteration().map(|_| None),
                "emit" => guard.before_emit().map(|_| None),
                "record" => {
                    guard.record_emit();
                    Ok(None)
                }
                "observe" => guard.observe_collection(number).map(|_| None),
                "collection" => guard.check_collection(&number).map(|_| None),
                _ => panic!(),
            };
            let (outcome, message) = match result {
                Ok(s) => ("ok", s),
                Err(e) => ("LimitExceeded", Some(e.to_string())),
            };
            let usage = guard.usage();
            observations.push(ValueData::Array(vec![
                text(outcome),
                optional(message.as_deref()),
                ValueData::Array(vec![
                    ValueData::Number(usage.tool_calls),
                    ValueData::Number(usage.loop_iterations),
                    ValueData::Number(usage.emitted_rows),
                    ValueData::Number(usage.max_collection_size_seen),
                ]),
            ]));
        }
        assert_eq!(
            json(&ValueData::Array(observations)),
            decode(c[5]),
            "{}",
            c[0]
        );
        count += 1;
    }
    assert_eq!(count, 48);
}
