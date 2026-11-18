mod tool_support;
use nsl_core::*;
use nsl_runtime::*;
use std::{
    future::{Future, poll_fn},
    sync::{
        Arc,
        atomic::{AtomicUsize, Ordering},
    },
    task::Poll,
    time::Duration,
};
use tool_support::*;

#[test]
fn frozen_timeout_cleanup_measurement_and_boundary_observations() {
    let mut count = 0;
    for line in include_str!("fixtures/timeouts-oracle.tsv")
        .lines()
        .filter(|l| !l.starts_with('#'))
    {
        let f: Vec<_> = line.split('\t').collect();
        let rt = runtime();
        let observed = rt.block_on(async {
            let clock = Clock::default();
            let control = Arc::new(Control::default());
            let q = request(&contract(ty("Int")));
            if f[0] == "timeout_bound" {
                struct Immediate(AtomicUsize);
                impl ToolExecutionPort for Immediate {
                    fn execute<'a>(
                        &'a self,
                        _q: &'a ToolCallRequest,
                        _c: CancellationSignal,
                    ) -> ToolExecutionFuture<'a> {
                        Box::pin(async move {
                            self.0.fetch_add(1, Ordering::SeqCst);
                            Ok(result())
                        })
                    }
                }
                let e = Immediate(AtomicUsize::new(0));
                return arr([
                    returned(
                        execute_with_timeout(
                            &e,
                            &q,
                            f[1].parse().unwrap(),
                            CancellationSignal::default(),
                        )
                        .await,
                    ),
                    n(e.0.load(Ordering::SeqCst)),
                ]);
            }
            let cleanup = f[0] == "cleanup";
            if !cleanup {
                clock.set(1_000_000);
            }
            let executor = MeasuringToolExecutor {
                delegate: Provider {
                    mode: f[1].into(),
                    clock: clock.clone(),
                    control: control.clone(),
                    cleanup,
                },
                clock,
                sink: TestSink {
                    fail: f[1] == "sink_error",
                    ..Default::default()
                },
            };
            let execution = execute_with_timeout(&executor, &q, 1, CancellationSignal::default());
            let mut execution = std::pin::pin!(execution);
            if cleanup {
                poll_fn(|cx| {
                    assert!(execution.as_mut().poll(cx).is_pending());
                    Poll::Ready(())
                })
                .await;
                tokio::time::advance(Duration::from_millis(1)).await;
                poll_fn(|cx| {
                    assert!(execution.as_mut().poll(cx).is_pending());
                    Poll::Ready(())
                })
                .await;
                assert!(control.cancel_seen.is_requested());
                assert!(executor.sink.inner.measurements().is_empty());
                // It remains pending arbitrarily beyond expiry until cleanup.
                tokio::time::advance(Duration::from_secs(60)).await;
                poll_fn(|cx| {
                    assert!(execution.as_mut().poll(cx).is_pending());
                    Poll::Ready(())
                })
                .await;
                control.release.request();
            }
            let outcome = returned(execution.await);
            if cleanup {
                arr([
                    ValueData::Bool(true),
                    arr(control.events.lock().unwrap().iter().map(s)),
                    outcome,
                    measurements(&executor.sink),
                ])
            } else {
                arr([outcome, measurements(&executor.sink)])
            }
        });
        assert_eq!(json(&observed), decode(f.last().unwrap()), "{line}");
        count += 1;
    }
    assert_eq!(count, 15);
}
