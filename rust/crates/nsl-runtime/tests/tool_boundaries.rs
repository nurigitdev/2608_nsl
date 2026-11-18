mod tool_support;
use nsl_contract::{
    ContractEncodingError, ToolContract, ToolContractCatalog, ToolContractOptions, ToolContractSpec,
};
use nsl_core::*;
use nsl_runtime::*;
use std::{
    collections::BTreeMap,
    error::Error,
    future::{Future, poll_fn},
    sync::Arc,
    task::Poll,
    time::Duration,
};
use tool_support::*;

#[test]
fn cancellation_is_sticky_idempotent_and_wakes_all_waiters() {
    fn send_sync<T: Send + Sync>() {}
    send_sync::<CancellationSignal>();
    send_sync::<MockToolExecutor>();
    send_sync::<ToolCallRequest>();
    let rt = runtime();
    rt.block_on(async {
        let signal = CancellationSignal::default();
        assert!(!signal.is_requested());
        let clone = signal.clone();
        let a = signal.cancelled();
        let b = clone.cancelled();
        let mut a = std::pin::pin!(a);
        let mut b = std::pin::pin!(b);
        poll_fn(|cx| {
            assert!(a.as_mut().poll(cx).is_pending());
            assert!(b.as_mut().poll(cx).is_pending());
            Poll::Ready(())
        })
        .await;
        signal.request();
        signal.request();
        assert!(clone.is_requested());
        a.await;
        b.await;
        signal.cancelled().await;
        // A cancellation between future construction and first poll is retained.
        let next = CancellationSignal::default();
        let wait = next.cancelled();
        next.request();
        wait.await;
        // Independent signals and late subscribers never inherit another call.
        assert!(!CancellationSignal::default().is_requested());
    });
}

#[test]
fn explicit_host_cancellation_awaits_cleanup_and_preserves_final_outcome() {
    let rt = runtime();
    rt.block_on(async {
        for mode in ["propagate", "suppress", "raise_error"] {
            let clock = Clock::default();
            let control = Arc::new(Control::default());
            let executor: Arc<dyn ToolExecutionPort> = Arc::new(Provider {
                mode: mode.into(),
                clock: clock.clone(),
                control: control.clone(),
                cleanup: true,
            });
            let executor = MeasuringToolExecutor {
                delegate: executor,
                clock,
                sink: TestSink::default(),
            };
            let request = request(&contract(ty("Int")));
            let signal = CancellationSignal::default();
            let execution = executor.execute(&request, signal.clone());
            let mut execution = std::pin::pin!(execution);
            poll_fn(|cx| {
                assert!(execution.as_mut().poll(cx).is_pending());
                Poll::Ready(())
            })
            .await;
            signal.request();
            poll_fn(|cx| {
                assert!(execution.as_mut().poll(cx).is_pending());
                Poll::Ready(())
            })
            .await;
            assert!(control.cancel_seen.is_requested());
            assert!(executor.sink.inner.measurements().is_empty());
            control.release.request();
            let result = execution.await;
            let outcome = match mode {
                "propagate" => {
                    assert_eq!(result, Err(ToolCallError::Cancelled));
                    ToolExecutionOutcome::Cancelled
                }
                "suppress" => {
                    assert_eq!(result.unwrap().value, value("seven"));
                    ToolExecutionOutcome::Returned
                }
                _ => {
                    assert_eq!(
                        result,
                        Err(ToolCallError::Provider("synthetic cleanup failure".into()))
                    );
                    ToolExecutionOutcome::Error
                }
            };
            let measurements = executor.sink.inner.measurements();
            assert_eq!(measurements.len(), 1);
            let m = &measurements[0];
            assert_eq!(m.outcome, outcome);
            assert_eq!(m.duration_ns, 3_000_000.into());
            assert_eq!(
                (
                    &m.execution_id,
                    &m.invocation_id,
                    &m.node_id,
                    &m.tool_id,
                    &m.tool_version,
                    &m.tenant_id
                ),
                (
                    &request.execution_id,
                    &request.invocation_id,
                    &request.node_id,
                    &request.tool_id,
                    &request.tool_version,
                    &request.principal.tenant_id
                )
            );
        }
    });
}

#[test]
fn completion_before_deadline_never_requests_cancellation_or_retries() {
    struct ReadyWhen(CancellationSignal);
    impl ToolExecutionPort for ReadyWhen {
        fn execute<'a>(
            &'a self,
            _r: &'a ToolCallRequest,
            _c: CancellationSignal,
        ) -> ToolExecutionFuture<'a> {
            Box::pin(async move {
                self.0.cancelled().await;
                Ok(result())
            })
        }
    }
    let rt = runtime();
    rt.block_on(async {
        let release = CancellationSignal::default();
        let executor = ReadyWhen(release.clone());
        let q = request(&contract(ty("Int")));
        let cancel = CancellationSignal::default();
        let execution = execute_with_timeout(&executor, &q, 1001, cancel.clone());
        let mut execution = std::pin::pin!(execution);
        poll_fn(|cx| {
            assert!(execution.as_mut().poll(cx).is_pending());
            Poll::Ready(())
        })
        .await;
        tokio::time::advance(Duration::from_millis(1000)).await;
        release.request();
        assert_eq!(execution.await.unwrap(), result());
        assert!(!cancel.is_requested());
    });
}

#[test]
fn result_hash_limits_are_typed_and_mock_count_survives_encoding_failure() {
    for size in [4299, 4300, 4301] {
        let value = Value::Int("9".repeat(size).parse().unwrap());
        let result = tool_result_hash(&value, 0);
        assert_eq!(result.is_ok(), size <= 4300);
        if let Err(error) = result {
            assert!(matches!(error, ResultEncodingError::Json(_)));
        }
    }
    let v = Value::Decimal("1.2300".parse().unwrap());
    for budget in [0, 1, 5, 6, 7] {
        assert_eq!(tool_result_hash(&v, budget).is_ok(), budget >= 6);
    }
    let huge = Value::Decimal("1e1000000".parse().unwrap());
    assert_eq!(
        tool_result_hash(&huge, 128),
        Err(ResultEncodingError::Decimal(DecimalError::EncodingLimit))
    );
    let rt = runtime();
    let c = contract(ty("Decimal"));
    let q = request(&c);
    let handler: FixtureHandler = Arc::new(move |_| Ok(huge.clone().into()));
    let executor = MockToolExecutor::new(
        ToolContractCatalog::new([c]).unwrap(),
        BTreeMap::from([("TEST.ECHO".into(), handler)]),
        128,
    );
    for calls in 1..=2 {
        assert_eq!(
            rt.block_on(executor.execute(&q, CancellationSignal::default())),
            Err(ToolCallError::Encoding(ResultEncodingError::Decimal(
                DecimalError::EncodingLimit
            )))
        );
        assert_eq!(executor.call_count(), calls.into());
    }
    assert_eq!(q.arguments["a"].value(), &value("seven"));
    // A separately checked result hash has the same typed decimal budget error.
    let mut r = result();
    r.value = v;
    assert_eq!(
        ToolContractValidator.validate_result_hash(&r, 5),
        Err(ToolCallError::Encoding(ResultEncodingError::Decimal(
            DecimalError::EncodingLimit
        )))
    );
}

#[test]
fn invalid_internal_optional_contract_fails_before_provider_and_preserves_safe_error() {
    let c = ToolContract::new(ToolContractSpec {
        tool_id: "TEST.ECHO".into(),
        version: "1.0.0".into(),
        capability: "READ".into(),
        input_types: vec![("x".into(), ty("Optional"))],
        output_type: ty("Int"),
        required_scope: "read".into(),
        output_classification: DataClassification::Internal,
        options: ToolContractOptions::default(),
    })
    .unwrap();
    let q = request(&contract(ty("Int")));
    let error = ToolContractValidator.validate_request(&q, &c).unwrap_err();
    assert_eq!(
        error,
        ToolCallError::ContractEncoding(ContractEncodingError::Type(TypeError::InternalOptional))
    );
    assert!(ty("Optional").accepts(&Value::Null));
    assert!(!tool_value_conforms_to_type(&Value::Null, &ty("Optional")));
    assert!(tool_value_conforms_to_type(
        &Value::List(vec![]),
        &ty("ListOptional")
    ));
    assert!(!tool_value_conforms_to_type(
        &Value::List(vec![Value::Null]),
        &ty("ListOptional")
    ));
    for e in [
        error,
        ToolCallError::Encoding(ResultEncodingError::Decimal(DecimalError::EncodingLimit)),
        ToolCallError::Cancelled,
        ToolCallError::Provider("password=synthetic".into()),
        ToolCallError::ProviderTimeout,
        ToolCallError::Measurement("secret=synthetic".into()),
        ToolExecutionError {
            code: "SAFE".into(),
            message: "safe".into(),
        }
        .into(),
    ] {
        assert!(!e.to_string().contains("synthetic"));
        assert!(e.source().is_none());
    }
    let e = ToolExecutionError {
        code: "SAFE".into(),
        message: "safe".into(),
    };
    assert_eq!(e.to_string(), "safe");
    assert!(e.source().is_none());
    let dynamic_code = ["PROVIDER", "CUSTOM"].join("_");
    let e = ToolExecutionError::new(dynamic_code.clone(), "safe provider detail".into());
    assert_eq!(e.code, dynamic_code);
}

#[test]
fn sink_failure_overrides_provider_error_and_is_not_relabelled_as_timeout() {
    let rt = runtime();
    rt.block_on(async {
        for mode in ["error", "cancelled", "provider_timeout"] {
            let executor = MeasuringToolExecutor {
                delegate: Provider {
                    mode: mode.into(),
                    clock: Clock::default(),
                    control: Arc::default(),
                    cleanup: false,
                },
                clock: Clock::default(),
                sink: TestSink {
                    fail: true,
                    ..Default::default()
                },
            };
            let q = request(&contract(ty("Int")));
            assert_eq!(
                execute_with_timeout(&executor, &q, 1, CancellationSignal::default()).await,
                Err(ToolCallError::Measurement("synthetic sink failure".into()))
            );
            let measurements = executor.sink.inner.measurements();
            assert_eq!(measurements.len(), 1);
            assert_eq!(
                measurements[0].outcome,
                if mode == "cancelled" {
                    ToolExecutionOutcome::Cancelled
                } else {
                    ToolExecutionOutcome::Error
                }
            );
        }
        let mut q = request(&contract(ty("Int")));
        q.principal.tenant_id = " ".into();
        let executor = MeasuringToolExecutor {
            delegate: Provider {
                mode: "ok".into(),
                clock: Clock::default(),
                control: Arc::default(),
                cleanup: false,
            },
            clock: Clock::default(),
            sink: TestSink::default(),
        };
        assert_eq!(
            executor.execute(&q, CancellationSignal::default()).await,
            Err(ToolCallError::Measurement(
                "tool measurement identifiers must be non-empty".into()
            ))
        );
        assert!(executor.sink.inner.measurements().is_empty());
        NullToolMeasurementSink
            .record(ToolExecutionMeasurement {
                execution_id: "e".into(),
                invocation_id: "i".into(),
                node_id: "n".into(),
                tool_id: "t".into(),
                tool_version: "v".into(),
                tenant_id: "tenant".into(),
                outcome: ToolExecutionOutcome::Returned,
                duration_ns: 0.into(),
            })
            .unwrap();
    });
}
