# NSL v0.1 Performance, Reliability, and SRS Certification

- **Slice:** `0030`
- **상태:** Accepted with Conditional SRS Certification
- **작성일:** 2026-08-20
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **Negative Acceptance Baseline:** `requirements/nsl_v0_1_negative_acceptance.json`
- **완료 범위:** 6 Implemented, 0 Partial

## 1. 목적

Slice 0030은 NSL v0.1 SRS 구현 단계의 종료 게이트다. Language와 Runtime 의미론을 변경하지 않고 성능을 관측 가능하게 만들고, Runtime crash를 NeX-AE API process에서 격리하며, 325개 요구사항과 Mandatory Negative Acceptance를 machine-readable evidence로 최종 감사한다.

현재 저장소에는 실제 NeX-AE API, Queue, Storage가 없다. 따라서 전체 SRS 상태는 미구현을 숨긴 완전 인증이 아니라 남은 외부 통합 및 Extension gap을 명시한 `CONDITIONAL` 인증이다.

## 2. Structural Review

기존 Compiler, Parser, NSO codec, Runtime과 Tool port는 이미 분리되어 있어 선행 리팩터링은 필요하지 않았다. 다음 세 경계를 추가했다.

```text
nsl.performance
  -> Parser / Compiler / NsoCodec public API
  -> ExecutionResult / ToolExecutionMeasurement read-only evidence

nsl.process_isolation
  -> trusted bytes-to-bytes target
  -> spawned child process
  -> bounded bytes result frame

tools.srs_certification
  -> validated traceability baseline
  -> SRS Section 40 negative acceptance manifest
  -> machine-readable certification report
```

Runtime kernel은 multiprocessing, filesystem, certification tooling에 의존하지 않는다. Performance timing도 ExecutionResult, semantic view, deterministic Audit chain에 포함하지 않는다.

## 3. Part A: Performance

| Requirement | Verification | 결과 |
|---|---|---|
| `NSL-PERF-001` | 64 KiB 이하 Source parse p95와 100 ms budget | `IMPLEMENTED` |
| `NSL-PERF-002` | Source Compile median과 NSO Load median 비교 | `IMPLEMENTED` |
| `NSL-PERF-003` | total, tool, runtime overhead 분리 | `IMPLEMENTED` |
| `NSL-PERF-004` | invocation Tool timing과 Runtime timing 분리 | `IMPLEMENTED` |
| `NSL-PERF-005` | bounded foreach 999/1000/1001 실행 | `IMPLEMENTED` |

### Benchmark model

`BenchmarkSummary`는 nanosecond integer sample의 minimum, median, p95, maximum을 제공한다. iteration은 1 이상 10,000 이하이고 warmup은 0 이상 iteration 이하로 제한한다. clock 역행은 음수 시간을 노출하지 않도록 0으로 제한한다.

일반 regression은 측정 모델과 acceptance rule을 결정적으로 검증한다. 실제 wall-clock acceptance는 coverage instrumentation의 영향을 배제하기 위해 pytest coverage가 끝난 뒤 `tools.performance_acceptance`를 별도 Python process로 실행한다.

```text
Small Source Parsing
  source size <= 64 KiB
  p95 <= 100 ms

NSO Loading
  median(NSO decode) < median(Source compile)
  iterations = 25
  warmups = 5
```

Runtime timing은 다음 관계를 유지한다.

```text
runtime_overhead_ns = max(0, total_duration_ns - tool_duration_ns)
tool_duration_ns = sum(same execution_id ToolExecutionMeasurement)
```

Tool timing에는 Execution, Invocation, Node, Tool, Tenant identity와 outcome, duration만 포함한다. argument, result, credential은 포함하지 않는다.

### 1,000-loop acceptance

Tool 호출이 없는 bounded foreach Skill을 사용한다. 999와 1,000개 입력은 각각 정확한 loop/emit count로 완료되고, collection limit을 한 단계 초과한 1,001개 입력은 loop 진입 전에 `LIMIT_EXCEEDED`와 `NSL-E6001`을 반환한다.

## 4. Part B: Runtime Process Isolation

| Requirement | Verification | 결과 |
|---|---|---|
| `NSL-REL-002` | spawned child의 success, error, crash, timeout | `IMPLEMENTED` |

`ProcessIsolatedRuntime`은 신뢰된 bytes-to-bytes Runtime entrypoint를 `spawn` process에서 실행한다. Input과 Result는 각각 최대 8 MiB이며 timeout은 1 ms 이상으로 명시해야 한다.

```text
COMPLETED       valid bytes result and exit code 0
TARGET_ERROR    caught target exception; message is not exported
CRASHED         child exits without a valid frame and non-zero exit code
TIMED_OUT       only the child is terminated
PROTOCOL_ERROR  missing, oversized, or malformed result frame
```

Native-style `os._exit(23)` crash와 timeout을 실제 child process에서 발생시켜 parent PID와 pytest process가 계속 동작함을 검증한다. Spawn failure, EOF, pipe error, invalid frame은 deterministic fake process로 모든 parent-side branch를 검증한다.

## 5. Production Evidence Profile

Slice 0023에서 이관된 `NSL-AUD-007`도 최종 감사에서 재검토했다. `WorkerEvidencePolicy.SNAPSHOT_REQUIRED`는 `SnapshotStore` 없는 Worker 구성을 거부한다. 이 profile에서 모든 Tool Result는 provider reference가 없더라도 Runtime이 보호 snapshot을 생성하고 `TOOL_COMPLETED.snapshot_ref`에 기록한다.

따라서 `NSL-AUD-007`은 `PARTIAL`에서 `IMPLEMENTED`로 승격했다. 개발 및 저수준 단위 테스트를 위한 기본 Worker policy는 backward-compatible한 `OPTIONAL`이며 production certification은 명시적으로 `SNAPSHOT_REQUIRED`를 선택해야 한다.

## 6. Part C: Negative Acceptance

SRS Section 40의 10개 case를 `requirements/nsl_v0_1_negative_acceptance.json`에 동결했다.

| Case | 거부 또는 보장 |
|---|---|
| `AC-N01` | Syntax Error 실행 금지 |
| `AC-N02` | Undeclared Tool compile 거부 |
| `AC-N03` | WRITE Tool compile 거부 |
| `AC-N04` | Unbounded foreach compile 거부 |
| `AC-N05` | Currency mismatch 실패 |
| `AC-N06` | Tool failure를 empty로 변환 금지 |
| `AC-N07` | Partial result의 PASS 생성 금지 |
| `AC-N08` | Budget exceeded는 CHECK FAIL |
| `AC-N09` | Resource 초과는 LIMIT_EXCEEDED |
| `AC-N10` | Replay semantic result 동일성 |

Certification gate는 SRS heading의 ID와 title, manifest 순서, evidence file, 실제 Python AST function definition을 대조한다. 테스트 데이터에 symbol 문자열만 넣어 evidence를 위조하는 경우도 거부한다.

## 7. 325 Requirement Audit

최종 상태는 다음과 같다.

| Status | Count |
|---|---:|
| `IMPLEMENTED` | 322 |
| `PARTIAL` | 2 |
| `PLANNED` | 1 |
| Total | 325 |

Certification status는 `CONDITIONAL`이며 gap은 정확히 세 개다.

| Requirement | 상태 | 사유와 완료 조건 |
|---|---|---|
| `NSL-AE-004` | `PARTIAL` | 실제 NeX-AE API request thread, Queue submit/consume 종단 검증 필요 |
| `NSL-AE-008` | `PARTIAL` | 실제 NeX-AE Runtime Result와 LLM Explanation 저장소 분리 검증 필요 |
| `NSL-SEC-013` | `PLANNED` | Slice 0032 Schedule Runner의 Service Principal 검증 대상 |

NSL Core, Compiler, Runtime, Tool, Audit, Replay, Package, CLI와 현재 저장소에서 검증 가능한 Integration contract에는 계획 상태가 남아 있지 않다.

## 8. Quality Gate

`tools/run_quality.py`는 다음을 단일 명령으로 수행한다.

1. 325 Requirement traceability와 fingerprint 검증
2. pytest unit/regression 및 branch coverage 실행
3. Statement 95%, Branch 90% threshold 검증
4. coverage가 없는 clean subprocess performance acceptance
5. 10개 Negative Acceptance manifest와 325개 SRS certification report 생성

Artifact는 Git에서 제외된 `test/coverage.json`, `test/coverage.xml`, `test/srs_certification.json`에 생성된다.

Slice 완료 시점 검증 결과:

```text
Regression: 1193 passed
Statement Coverage: 99.54%
Branch Coverage: 98.71%
Performance Gate: PASSED
Negative Acceptance: 10/10
SRS Certification: CONDITIONAL
```

## 9. Acceptance

- Slice 0030 할당 6개 Requirement가 모두 `IMPLEMENTED`
- Slice 0030 내부 `PARTIAL` 없음
- Runtime crash가 parent process를 종료하지 않음
- 1,000 bounded loop와 max+1 거부 검증
- Tool timing과 Runtime overhead가 분리됨
- `NSL-AUD-007` production evidence profile 완료
- 325개 Requirement 누락·중복 없이 감사됨
- 남은 3개 gap이 machine-readable report에 명시됨
- 전체 regression, coverage, performance, certification gate 통과
