mod guard_support;
mod invocation_support;
mod requested_support;
mod support;
use guard_support as g;
use invocation_support::*;
use nsl_core::*;
use nsl_write::*;
use requested_support as r;
use std::{
    future::Future,
    sync::{Arc, Mutex},
    time::Duration,
};
use support::*;
use tokio::sync::Notify;

#[derive(Default)]
struct State {
    events: Vec<String>,
    calls: i64,
    mutations: i64,
    cleanups: i64,
}
struct FaultProvider {
    config: ValueData,
    expected: ValueData,
    state: Mutex<State>,
    started: Notify,
    cleanup_started: Notify,
    cleanup_gate: Option<Arc<Notify>>,
}
impl FaultProvider {
    fn new(config: ValueData, expected: ValueData) -> Self {
        Self {
            config,
            expected,
            state: Mutex::new(State {
                events: vec!["WRITE_REQUESTED".into()],
                ..Default::default()
            }),
            started: Notify::new(),
            cleanup_started: Notify::new(),
            cleanup_gate: None,
        }
    }
    fn finished(&self) -> Result<WriteExecutionOutcome, WriteProviderError> {
        match get(&self.config, "mode") {
            "committed" | "not-committed" | "unknown" => {
                Ok(parse_outcome(field(&self.expected, "outcome")))
            }
            "propagate" | "cancelled-error" => Err(WriteProviderError::Cancelled),
            "timeout-error" => Err(WriteProviderError::Timeout),
            "connection-error" => Err(WriteProviderError::ConnectionLost),
            "exception-error" | "cleanup-error" => Err(WriteProviderError::Exception),
            "malformed" => Err(WriteProviderError::MalformedAcknowledgement),
            _ => panic!(),
        }
    }
}
impl WriteExecutionPort for FaultProvider {
    fn execute<'a>(
        &'a self,
        request: &'a WriteCallRequest,
        cancellation: WriteCancellationSignal,
    ) -> WriteExecutionFuture<'a> {
        Box::pin(async move {
            assert_eq!(request.metadata().tenant_id, "tenant-a");
            {
                let mut s = self.state.lock().unwrap();
                s.calls += 1;
                s.events.push("PROVIDER_WRITE".into());
                if g::yes(&self.config, "mutate") {
                    s.mutations += 1;
                    s.events.push("POSSIBLE_MUTATION".into());
                }
            }
            self.started.notify_one();
            if get(&self.config, "trigger") == "direct"
                || g::opt(&self.config, "immediate") == Some(&ValueData::Bool(true))
            {
                tokio::task::yield_now().await;
                return self.finished();
            }
            cancellation.cancelled().await;
            self.state
                .lock()
                .unwrap()
                .events
                .extend(["CANCEL_RECEIVED".into(), "CLEANUP_STARTED".into()]);
            self.cleanup_started.notify_one();
            if let Some(gate) = &self.cleanup_gate {
                gate.notified().await;
            }
            let ValueData::Number(turns) = field(&self.config, "cleanup_turns") else {
                panic!()
            };
            for _ in 0..turns.as_str().parse::<usize>().unwrap() {
                tokio::task::yield_now().await;
            }
            {
                let mut s = self.state.lock().unwrap();
                s.cleanups += 1;
                s.events.push("CLEANUP_COMPLETED".into());
            }
            self.finished()
        })
    }
}
fn run(future: impl Future<Output = ()>) {
    tokio::runtime::Builder::new_current_thread()
        .enable_time()
        .start_paused(true)
        .build()
        .unwrap()
        .block_on(future);
}
fn permit() -> DurableWriteRequest {
    let setup = r::Setup::new(r::baseline());
    let evidence = r::Evidence::default();
    let mut flag = false;
    let permit = r::ready(WriteRequestedCoordinator::new(&evidence).persist(
        setup.created(),
        &r::allowed(),
        r::context(&mut flag),
        r::now(),
    ))
    .unwrap();
    assert!(flag);
    assert_eq!(evidence.state.lock().unwrap().records.len(), 1);
    permit
}
fn example(trigger: &str, mode: &str) -> (ValueData, ValueData) {
    g::rows(include_str!("fixtures/write-faults-oracle.tsv"))
        .map(|(_, c, e)| (c, e))
        .find(|(c, _)| {
            get(c, "trigger") == trigger && get(c, "mode") == mode && g::yes(c, "mutate")
        })
        .unwrap()
}
#[test]
fn asynchronous_faults_and_suppression_match_frozen_oracle_without_business_retry() {
    run(async {
        let mut count = 0;
        let mut unknown = 0;
        let mut definite = 0;
        for (name, config, expected) in g::rows(include_str!("fixtures/write-faults-oracle.tsv")) {
            let provider = Arc::new(FaultProvider::new(config.clone(), expected.clone()));
            let signal = WriteCancellationSignal::default();
            let supplied = signal.clone();
            let port = provider.clone();
            let durable = permit();
            let task = tokio::spawn(async move {
                let coordinator = WriteInvocationCoordinator::new(&*port);
                if get(&port.config, "trigger") == "timeout" {
                    let ValueData::Number(ms) = field(&port.config, "timeout_ms") else {
                        panic!()
                    };
                    coordinator
                        .invoke_with_timeout(durable, ms.as_str().parse().unwrap(), supplied)
                        .await
                } else {
                    coordinator.invoke(durable, supplied).await
                }
            });
            if get(&config, "trigger") == "cancel" {
                provider.started.notified().await;
                signal.request();
            }
            let result = task.await.unwrap();
            let s = provider.state.lock().unwrap();
            let actual = object([
                ("provider_calls", ValueData::Number(s.calls.into())),
                ("possible_mutations", ValueData::Number(s.mutations.into())),
                ("cleanup_completed", ValueData::Number(s.cleanups.into())),
                (
                    "automatic_write_retries",
                    ValueData::Number((s.calls - 1).max(0).into()),
                ),
                ("flag", ValueData::Bool(true)),
                ("outcome", outcome_data(result.outcome())),
                (
                    "automatic_retry_allowed",
                    ValueData::Bool(result.automatic_retry_allowed()),
                ),
                (
                    "reconciliation_required",
                    ValueData::Bool(result.reconciliation_required()),
                ),
                (
                    "recovery_status",
                    result
                        .recovery_status()
                        .map(|v| txt(v.as_str()))
                        .unwrap_or(ValueData::Null),
                ),
                (
                    "events",
                    ValueData::Array(s.events.iter().map(txt).collect()),
                ),
            ]);
            let ValueData::Object(mut projected) = expected.clone() else {
                panic!()
            };
            assert_eq!(
                projected.remove("oracle_outcome_records"),
                Some(ValueData::Number(1.into()))
            );
            let oracle_status = projected.remove("oracle_final_status").unwrap();
            assert_eq!(actual, ValueData::Object(projected), "{name}");
            assert!(s.calls <= 1);
            assert!(!result.automatic_retry_allowed());
            assert_eq!(
                result.outcome().identity(),
                &WriteOutcomeIdentity::from_request(result.request())
            );
            if result.reconciliation_required() {
                unknown += 1;
                assert_eq!(oracle_status, txt("RECONCILIATION_REQUIRED"));
                assert_eq!(
                    result.recovery_status(),
                    Some(ExecutionStatus::ReconciliationRequired)
                );
                assert_eq!(
                    result.outcome().commit_state(),
                    CommitState::UnknownCommitState
                );
            } else {
                definite += 1;
                assert_eq!(result.recovery_status(), None);
            }
            if get(&config, "mode") == "cleanup-error" {
                assert_eq!(result.provider_error(), Some(WriteProviderError::Exception));
            }
            if get(&config, "mode") == "propagate" {
                assert_eq!(result.provider_error(), Some(WriteProviderError::Cancelled));
            }
            let is_timeout = get(&config, "trigger") == "timeout";
            let nonpositive = is_timeout
                && matches!(field(&config,"timeout_ms"),ValueData::Number(n) if n.as_str().starts_with('-') || n.as_str()=="0");
            let immediate = g::opt(&config, "immediate") == Some(&ValueData::Bool(true));
            assert_eq!(
                result.deadline_elapsed(),
                is_timeout && (!immediate || nonpositive),
                "{name}"
            );
            if get(&config, "trigger") == "direct" {
                assert!(!signal.is_requested());
            }
            count += 1;
        }
        assert_eq!(count, 81);
        assert!(unknown > 0 && definite > 0);
    });
}
#[test]
fn cancellation_and_deadline_wait_for_cleanup_without_a_second_deadline() {
    run(async {
        for trigger in ["cancel", "timeout"] {
            for mode in ["committed", "cleanup-error"] {
                let (config, expected) = example(trigger, mode);
                let gate = Arc::new(Notify::new());
                let mut provider = FaultProvider::new(config, expected);
                provider.cleanup_gate = Some(gate.clone());
                let provider = Arc::new(provider);
                let signal = WriteCancellationSignal::default();
                let supplied = signal.clone();
                let port = provider.clone();
                let durable = permit();
                let task = tokio::spawn(async move {
                    let coordinator = WriteInvocationCoordinator::new(&*port);
                    if trigger == "timeout" {
                        coordinator.invoke_with_timeout(durable, 1, supplied).await
                    } else {
                        coordinator.invoke(durable, supplied).await
                    }
                });
                provider.started.notified().await;
                if trigger == "cancel" {
                    signal.request();
                } else {
                    tokio::time::advance(Duration::from_millis(1)).await;
                }
                provider.cleanup_started.notified().await;
                tokio::time::advance(Duration::from_secs(60)).await;
                tokio::task::yield_now().await;
                assert!(!task.is_finished());
                assert_eq!(provider.state.lock().unwrap().cleanups, 0);
                gate.notify_one();
                let result = task.await.unwrap();
                assert_eq!(provider.state.lock().unwrap().calls, 1);
                assert_eq!(provider.state.lock().unwrap().cleanups, 1);
                assert!(!result.automatic_retry_allowed());
                if mode == "committed" {
                    assert_eq!(result.outcome().commit_state(), CommitState::Committed);
                    assert_eq!(result.provider_error(), None);
                    assert_eq!(result.recovery_status(), None);
                } else {
                    assert_eq!(result.provider_error(), Some(WriteProviderError::Exception));
                    assert_eq!(
                        result.recovery_status(),
                        Some(ExecutionStatus::ReconciliationRequired)
                    );
                }
            }
        }
    });
}
#[test]
fn sticky_cancellation_wakes_existing_and_late_waiters_without_cross_signal_effects() {
    run(async {
        for n in [0, 1, 3] {
            let signal = WriteCancellationSignal::default();
            let other = WriteCancellationSignal::default();
            assert!(!signal.is_requested());
            let mut tasks = Vec::new();
            for _ in 0..n {
                let s = signal.clone();
                tasks.push(tokio::spawn(async move {
                    s.cancelled().await;
                }));
            }
            tokio::task::yield_now().await;
            signal.request();
            signal.request();
            assert!(signal.is_requested());
            for task in tasks {
                task.await.unwrap();
            }
            signal.cancelled().await;
            assert!(!other.is_requested());
        }
    });
}
#[test]
fn concurrent_invocations_keep_cancellation_cleanup_and_call_counts_isolated() {
    run(async {
        let (c, e) = example("cancel", "propagate");
        let first = Arc::new(FaultProvider::new(c.clone(), e.clone()));
        let second = Arc::new(FaultProvider::new(c, e));
        let one = WriteCancellationSignal::default();
        let two = WriteCancellationSignal::default();
        let mut tasks = Vec::new();
        for (port, signal) in [(first.clone(), one.clone()), (second.clone(), two.clone())] {
            let durable = permit();
            tasks.push(tokio::spawn(async move {
                WriteInvocationCoordinator::new(&*port)
                    .invoke(durable, signal)
                    .await
            }));
        }
        first.started.notified().await;
        second.started.notified().await;
        one.request();
        let result = tasks.remove(0).await.unwrap();
        assert_eq!(
            result.recovery_status(),
            Some(ExecutionStatus::ReconciliationRequired)
        );
        assert!(!tasks[0].is_finished());
        assert!(!two.is_requested());
        assert_eq!(second.state.lock().unwrap().cleanups, 0);
        two.request();
        let result = tasks.remove(0).await.unwrap();
        assert!(!result.automatic_retry_allowed());
        assert_eq!(
            result.recovery_status(),
            Some(ExecutionStatus::ReconciliationRequired)
        );
        for port in [first, second] {
            let s = port.state.lock().unwrap();
            assert_eq!(s.calls, 1);
            assert_eq!(s.mutations, 1);
            assert_eq!(s.cleanups, 1);
        }
    });
}
