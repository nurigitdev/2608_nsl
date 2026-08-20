# NSL v0.1 NeX-AE Integration Contract and Worker Boundary

- **Slice:** `0029`
- **상태:** Accepted
- **작성일:** 2026-08-20
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 11 Implemented, 2 Partial

## 1. 목적과 환경 제약

이 Slice는 NeX-AE 자체를 구현하지 않는다. 현재 저장소에 NeX-AE API, Job Queue, Worker host, SSE endpoint, LLM 설명 저장소가 없다는 환경 제약을 전제로 NSL이 소유해야 하는 Integration Contract와 Worker Boundary를 구현한다.

NSL Runtime은 계속 NeX-AE, Web Framework, Queue SDK와 독립적이다. NeX-AE는 추후 `nsl.api`의 안정적인 public contract를 사용해 같은 Worker process에 Runtime을 탑재하거나 별도 Runtime Service로 분리할 수 있다.

## 2. Part와 Requirement 결과

### Part A: Integration Contract

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-AE-001` | Skill ID와 exact version 기반 verified Skill resolution | `IMPLEMENTED` |
| `NSL-AE-002` | Typed structured input contract | `IMPLEMENTED` |
| `NSL-AE-003` | Input과 분리된 Runtime Context contract | `IMPLEMENTED` |
| `NSL-AE-007` | Versioned structured Runtime Result envelope | `IMPLEMENTED` |
| `NSL-AE-009` | Verified Principal과 Authorization Context | `IMPLEMENTED` |
| `NSL-AE-010` | Explicit Data Handling Policy | `IMPLEMENTED` |
| `NSL-AE-011` | USER, CONTEXT, DEFAULT Input provenance | `IMPLEMENTED` |

### Part B: Worker and Deployment Boundary

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-AE-005` | Framework-neutral Skill Execution Worker | `IMPLEMENTED` |
| `NSL-AE-006` | Ordered and safe Progress Event port | `IMPLEMENTED` |
| `NSL-ARC-008` | Same-process NeX-AE Worker deployment | `IMPLEMENTED` |
| `NSL-ARC-009` | Transport-neutral future service separation | `IMPLEMENTED` |

### Part C: External NeX-AE Boundary

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-AE-004` | API request thread 외부 async dispatch contract | `PARTIAL` |
| `NSL-AE-008` | Runtime Result와 LLM Explanation 분리 contract | `PARTIAL` |

`NSL-AE-004`와 `NSL-AE-008`은 NSL 측 port, record, contract fake와 architecture boundary까지 구현했다. 실제 NeX-AE API/Queue/Storage가 없으므로 종단 동작을 검증할 수 없어 `PARTIAL`로 유지한다. 기존 `NSL-AUD-007`의 `PARTIAL`도 변경하지 않는다.

## 3. Architecture Boundary

```text
NeX-AE API                         not implemented here
    |
    v
JobDispatcher port
    |
    v
canonical SkillExecutionJob bytes
    |
    v
NeX-AE Worker host                future host
    |
    v
nsl.api.SkillExecutionWorker
    |-- VerifiedPackageSkillResolver
    |-- RuntimeEngine
    |-- ToolExecutionPort
    |-- AuditSink / SnapshotStore
    `-- ProgressSink
    |
    v
RuntimeResultEnvelope
```

Dependency direction:

```text
NeX-AE -> nsl.api -> integration / dispatch / worker / outcome_records
                    -> RuntimeEngine and existing ports

RuntimeEngine -X-> NeX-AE / dispatch / outcome_records / Web / Queue SDK
```

## 4. Skill Resolution and Job Identity

`VerifiedPackageSkillResolver`는 `NspVerifier`를 통과한 `VerifiedNspPackage`만 catalog source로 허용한다. Worker는 다음 순서로 Skill identity를 확인한다.

1. `skill_id`와 exact `skill_version` resolve
2. Duplicate ID/version registration 거부
3. Package verification에서 유지된 publisher key ID와 package hash 보존
4. Job의 `expected_semantic_hash`와 resolved NSO semantic hash 비교
5. 일치한 `SkillObject`만 Runtime에 전달

미등록 Skill, version 불일치, 중복 identity, semantic hash mismatch는 Tool 호출과 Runtime 실행 전에 거부한다.

## 5. Skill Execution Job Contract

`SkillExecutionJob`은 다음 필드를 canonical JSON으로 전달한다.

```text
format / schema_version
execution_id
skill_id / skill_version / expected_semantic_hash
inputs
input_provenance
runtime_context
principal
data_policy
```

Contract 특성:

- Input과 Runtime Context를 별도 namespace로 유지
- Decimal, Date, DateTime, Money typed wire value 지원
- Unknown typed tag와 typed extra field 거부
- Input마다 `USER`, `CONTEXT`, `DEFAULT` 중 정확히 하나의 provenance 요구
- Production 수준의 `VERIFIED` Principal과 `auth_context_ref` 요구
- raw credential material 거부
- Data Handling Policy의 classification과 retention을 필수 명시
- Canonical JSON, duplicate field, non-finite number, UTF-8, schema drift 검증
- Value depth 32, node 10,000, string 1 MiB, document 8 MiB 제한

## 6. Worker Execution Boundary

`SkillExecutionWorker.execute()`는 다음 순서만 수행한다.

1. Validated `SkillExecutionJob` 확인
2. `STARTED` progress 발행
3. Verified Skill resolve 및 semantic identity 확인
4. `SKILL_RESOLVED`, `RUNNING` progress 발행
5. 기존 `ExecutionRequest` 생성
6. 주입된 Runtime, Tool, Audit, Snapshot port로 실행
7. Runtime status를 보존한 terminal progress 발행
8. `RuntimeResultEnvelope` 반환

Resolver 또는 progress delivery 실패는 구조화된 `WorkerBoundaryError`로 반환한다. Runtime failure는 `COMPLETED`로 승격하지 않고 terminal `FAILED` progress와 원래 Runtime status를 유지한다. Progress에는 Input, Context, Tool argument/result 같은 업무 데이터가 포함되지 않는다.

## 7. In-process and Service Deployment

`nsl.api`는 NeX-AE Worker가 내부 module을 직접 import하지 않도록 public facade를 제공한다. 같은 Python process에서는 Queue/Web dependency 없이 `SkillExecutionWorker`를 생성하고 실행할 수 있다.

향후 별도 service로 분리할 때에도 동일 Job/Progress/Result contract를 사용한다. Job과 Progress는 strict canonical bytes round trip을 지원하며 Runtime Result도 canonical bytes로 반환한다. Integration 계층은 FastAPI, HTTP client, socket, database, Queue SDK를 import하지 않는다.

## 8. External Boundary and PARTIAL Closure

### NSL-AE-004

현재 완료:

- Async `JobDispatcher` port
- Runtime을 호출하지 않고 canonical Job을 보관하는 contract fake
- Versioned `JobDispatchReceipt`
- Dispatch module의 Runtime/Worker dependency 금지

`IMPLEMENTED` 승격 조건:

- 실제 NeX-AE API가 요청 thread에서 Runtime을 호출하지 않는 통합 테스트
- 실제 Queue submit과 Worker consume 검증
- API 응답의 job/execution ID 상관관계 검증
- timeout, duplicate submission, queue failure acceptance test

### NSL-AE-008

현재 완료:

- Hash-protected `RuntimeResultRecord`
- Runtime result hash에 결속된 `LlmExplanationRecord`
- 서로 다른 `RuntimeResultStore`, `LlmExplanationStore` port
- Explanation record에 Runtime status 필드가 없는 구조
- Runtime Kernel의 LLM record dependency 금지

`IMPLEMENTED` 승격 조건:

- 실제 NeX-AE 저장소에서 물리적 또는 논리적 분리 검증
- LLM 재생성 시 Runtime Result bytes/hash 불변 검증
- tenant, retention, classification 정책을 적용한 조회 권한 테스트
- Explanation update가 status, CHECK, completeness를 변경하지 못하는 종단 테스트

## 9. Test Strategy

- Boundary: empty, wrong type, overlength, duplicate, missing/extra field
- Robustness: cyclic data, unknown typed tag, malformed UTF-8/JSON, noncanonical JSON
- Worst case: depth 32, node 10,000, string 1 MiB, document 8 MiB
- Security: raw credential, unverified principal, unknown Skill, semantic mismatch
- Worker: successful mock execution, authorization failure, dependency failure
- Progress: exact sequence, terminal lock, false-complete prevention, sink failure
- Deployment: same-process facade, transport round trip, framework independence
- Separation: Runtime/LLM record hash binding, wrong store type, duplicate record

## 10. Traceability and Quality

Slice 반영 후 전체 Baseline은 `IMPLEMENTED 315`, `PARTIAL 3`, `PLANNED 7`이다.

- Regression: `1089 passed`
- Statement Coverage: `99.51%` (시작값 `99.45%`)
- Branch Coverage: `98.60%` (시작값 `98.42%`)
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 11. Acceptance

- 13개 Requirement에 개별 verification과 evidence 연결
- 11개 Requirement `IMPLEMENTED`, 외부 종단 의존 2개 `PARTIAL`
- Verified Package에서만 Skill ID/version resolve
- Canonical, bounded, credential-safe Job contract
- Principal, Authorization Context, Data Policy, Input provenance 명시
- Framework-neutral Worker와 progress port
- Same-process 실행 및 future service separation 지원
- Runtime Result와 LLM Explanation contract 분리
- Requirement별 전체 regression과 coverage baseline 유지
