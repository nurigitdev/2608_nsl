# NSL v0.1 CLI Local Execution Profile and Scenario Runner

- **Slice:** `0031`
- **상태:** Accepted
- **작성일:** 2026-08-20
- **Extension Baseline:** `requirements/nsl_cli_local_execution_extension.json`
- **완료 범위:** 15 Implemented, 0 Partial

## 1. 목적

NeX-AE 구현체가 없는 현재 저장소에서 `.ns`를 실제 업무 입력과 Mock Tool로
컴파일하고 실행하며 실패 사례까지 반복 검증할 수 있는 local execution
profile을 제공한다. 이 Slice는 NSL 문법이나 Immutable IR에 새로운 의미를
추가하지 않고 기존 Compiler, NSO Trust Boundary, Runtime과 process isolation을
CLI orchestration 계층에서 조합한다.

```text
Local Profile / CLI Override
  -> Source compile or verified NSO load
  -> Principal, input, context, tool contract validation
  -> Dry-run or isolated Runtime execution
  -> Structured result, redacted audit, separate timing
  -> Scenario expectation evaluation
```

## 2. Structural Review

Compiler와 Runtime Core는 filesystem과 CLI state를 소유하지 않는 기존 경계를
유지한다. 공통 실행 요청 검증은 `RuntimeEngine.validate_execution_request`로
추출해 dry-run과 live run이 같은 input/context binding 규칙을 사용한다.

| Module | 책임 |
|---|---|
| `nsl.cli_profile` | Strict profile schema, contained path resolution, override merge |
| `nsl.cli_runtime` | CLI config에서 기존 Runtime request 구성 |
| `nsl.cli_evidence` | Canonical result와 redacted audit의 atomic write |
| `nsl.cli_isolation` | Bounded child wire와 process-isolated execution adapter |
| `nsl.cli_scenarios` | Strict suite load, independent invocation, semantic assertion |

`nsl.runtime`은 CLI, profile, scenario, multiprocessing module을 import하지 않는다.
Process boundary에는 검증된 NSO, canonical Tool Catalog, Principal, 값과 Mock
Fixture만 전달하며 frame은 크기 제한과 credential 검사를 통과해야 한다.

## 3. Part A: Profile and Example

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-CLX-001` | Version `1.0`, 1 MiB 이하 strict profile | `IMPLEMENTED` |
| `NSL-CLX-002` | Relative canonical file과 profile root containment | `IMPLEMENTED` |
| `NSL-CLX-003` | 명시적인 CLI option의 deterministic override | `IMPLEMENTED` |
| `NSL-CLX-004` | `check`, `compile`, `run` profile 지원 | `IMPLEMENTED` |
| `NSL-CLX-005` | 실행 가능한 `PROJECT_BUDGET_CHECK` profile | `IMPLEMENTED` |

Profile은 `program`, `principal`을 필수로 하고 `tool_contracts`, `input`,
`context`, `fixture`, `execution.isolate`, `execution.timeout_ms`를 선택적으로
가진다. 알 수 없는 field, credential material, 절대 경로, root escape, symlink
escape, 파일이 아닌 target은 거부한다.

```powershell
python -m nsl check --profile examples\project_budget_check.profile.json
python -m nsl compile --profile examples\project_budget_check.profile.json -o test\project_budget_check.nso
python -m nsl run --profile examples\project_budget_check.profile.json
```

## 4. Part B: Dry-run, Evidence and Isolation

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-CLX-006` | Tool 호출 없는 execution request 검증 | `IMPLEMENTED` |
| `NSL-CLX-007` | 결정적이고 credential-safe한 dry-run report | `IMPLEMENTED` |
| `NSL-CLX-008` | Canonical result와 redacted audit atomic write | `IMPLEMENTED` |
| `NSL-CLX-009` | Load, Runtime, Tool, overhead timing 분리 | `IMPLEMENTED` |
| `NSL-CLX-010` | Timeout이 있는 child-process isolation | `IMPLEMENTED` |

Dry-run은 source/NSO, semantic identity, Principal, input/context type, required
scope, Tool Contract, Mock Fixture와 resource limit을 검증하지만
`ToolExecutionPort.execute`와 Skill body를 실행하지 않는다. Report에는 절대
경로, credential, Principal identity를 포함하지 않는다.

`--result-out`은 semantic result JSON을, `--audit-out`은 redacted Audit JSONL을
exclusive temporary file과 atomic replace로 기록한다. `--timing`은 관측 정보만
추가하며 result, Audit, Replay semantic identity를 변경하지 않는다.

터미널 stdout/stderr의 JSON은 사람이 검토하기 쉽도록 key 정렬과 2-space
indent를 적용한다. 이 표시 형식 변경은 canonical evidence file, NSO, replay,
isolation wire의 byte-level contract에는 적용하지 않는다.

Isolation 기본값은 profile에서 정하며 CLI의 `--isolate` 또는 `--no-isolate`가
override한다. Timeout, crash, malformed/oversized frame은 parent process를
종료시키지 않고 검증된 CLI execution error로 변환한다. 보호되지 않은
standalone replay export는 isolated run과 조합할 수 없다.

## 5. Part C: Scenario Runner

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-CLX-011` | Version `1.0`, 8 MiB, 최대 1,000 case strict suite | `IMPLEMENTED` |
| `NSL-CLX-012` | Case별 fresh Runtime과 stable execution ID | `IMPLEMENTED` |
| `NSL-CLX-013` | Status, completeness, checks, outputs, error, resources assertion | `IMPLEMENTED` |
| `NSL-CLX-014` | 결정적 summary와 mismatch exit code `7` | `IMPLEMENTED` |
| `NSL-CLX-015` | 경계 보호와 예산 점검 acceptance matrix | `IMPLEMENTED` |

각 case는 같은 profile을 기반으로 Principal, input, context, fixture, isolation과
timeout을 override할 수 있다. 실행은 중첩된 fresh `nsl run` invocation으로
분리되고 execution ID는 `scenario-<case-id>`다. 선언된 expectation만 비교하며
case 순서와 mismatch 순서는 suite 순서를 보존한다.

```powershell
python -m nsl test --suite examples\project_budget_check.scenarios.json
```

Repository suite의 7개 case는 다음을 고정한다.

| Case | 기대 결과 |
|---|---|
| `success` | Budget CHECK `PASS`와 complete output |
| `budget-overrun` | CHECK `FAIL`, 음수 remaining |
| `tool-fixture-missing` | `TOOL_ERROR`, `NSL-E4101` |
| `authorization-denied` | Tool 호출 전 `NSL-E5201` |
| `invalid-input` | Runtime binding `NSL-E8001` |
| `resource-limit` | Collection limit `NSL-E6001` |
| `deterministic-repeat` | 동일 fixture의 반복 semantic 결과 |

## 6. Security and Failure Rules

- Profile과 suite에서 endpoint, token, password 등 credential material 금지
- 모든 참조 파일은 선언 파일 root 아래 canonical regular file이어야 함
- Child request/response는 canonical JSON, bounded bytes, schema/type 검증 적용
- Dry-run은 Tool provider와 Skill execution을 호출하지 않음
- Audit evidence는 기존 classification redaction을 그대로 적용
- Scenario expectation mismatch와 Runtime failure를 서로 다른 exit code로 구분
- Internal exception text, absolute local path, Principal identity를 공개 결과에서 제외

## 7. Traceability and Quality

15개 Extension Requirement는 325개 SRS와 분리된
`requirements/nsl_cli_local_execution_extension.json`에 동결한다. Fingerprint는
Requirement ID, Priority, Part, Title을 순서대로 canonicalize해 계산한다.
`tools/run_quality.py`는 SRS gate 직후 이 baseline의 연속 ID, Part 배정,
`IMPLEMENTED|PARTIAL` 상태, Verification ID와 실제 Evidence symbol을 검사한다.

Slice 완료 시점 검증 결과:

```text
Regression: 1220 passed
Statement Coverage: 99.56%
Branch Coverage: 98.76%
CLI Extension Traceability: 15/15 IMPLEMENTED
SRS Certification: CONDITIONAL (기존 3개 gap 유지)
```

## 8. Acceptance

- Part A, B, C의 15개 Requirement가 모두 `IMPLEMENTED`
- Slice 내부 `PARTIAL` 없음
- 실제 `PROJECT_BUDGET_CHECK` profile compile/run 성공
- 성공과 5개 negative class, deterministic repeat scenario 통과
- Tool을 호출하지 않는 dry-run과 bounded process isolation 검증
- result/audit/timing이 semantic result 경계를 침범하지 않음
- Extension traceability가 Mandatory Quality Gate에 포함됨
- 전체 regression과 statement/branch coverage 현재값 유지
