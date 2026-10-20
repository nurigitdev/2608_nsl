# NSL v0.1 Persistent Audit

- **Slice:** `0023`
- **상태:** Accepted; Slice 0030 Follow-up Closed
- **작성일:** 2026-08-20
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 12 Implemented

## 1. 목적

이 Slice는 Runtime의 감사 기록을 프로세스 메모리 밖에 보존하고, 재시작 후에도 실행별 감사 순서와 변조 여부를 검증할 수 있는 최소 영속 경계를 정의한다. Runtime Core는 기존 `AuditSink` 포트만 사용하며 파일 시스템은 별도 `JsonlAuditStore` 어댑터가 담당한다.

## 2. Part와 Requirement 결과

### Part A: Persistence Foundation and Execution Identity

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-AUD-001` | 모든 Skill 실행 결과 감사 | `IMPLEMENTED` |
| `NSL-AUD-002` | Skill ID/Version 기록 | `IMPLEMENTED` |
| `NSL-AUD-003` | NSO semantic hash 기록 | `IMPLEMENTED` |
| `NSL-AUD-004` | Runtime version 기록 | `IMPLEMENTED` |

### Part B: Data and Action Evidence

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-AUD-005` | Input/Context hash, classification, snapshot reference | `IMPLEMENTED` |
| `NSL-AUD-006` | Tool invocation input names, hash, classification, snapshot reference | `IMPLEMENTED` |
| `NSL-AUD-007` | Tool result snapshot 또는 reference | `IMPLEMENTED` (Slice 0030 follow-up) |
| `NSL-AUD-008` | Tool result hash | `IMPLEMENTED` |
| `NSL-AUD-009` | CHECK result | `IMPLEMENTED` |
| `NSL-AUD-010` | EMIT result 및 보호 snapshot evidence | `IMPLEMENTED` |

### Part C: Failure, Principal and Authorization Evidence

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-AUD-011` | Error phase, node location, cause | `IMPLEMENTED` |
| `NSL-AUD-014` | Principal 및 ALLOW/DENY decision reference | `IMPLEMENTED` |

기존 `NSL-AUD-012/013`과 `NSL-TST-019`는 `IMPLEMENTED` 상태를 유지하며 영속 JSONL에서도 보호 원문이 제외되고 secure snapshot reference/hash/classification만 남는지 회귀 검증했다.

## 3. Architecture Boundary

```text
RuntimeEngine
    -> AuditRecorder
        -> AuditSink port
            -> InMemoryAuditSink
            -> JsonlAuditStore
```

`runtime.py`와 `audit.py`는 `audit_persistence.py`를 import하지 않는다. 파일 접근은 Runtime Kernel 밖의 어댑터에만 있으며 이 의존 방향은 architecture test로 고정한다.

## 4. Audit Event Envelope

모든 이벤트는 다음 공통 식별 정보를 가진다.

- Audit schema version과 Runtime version
- Execution ID와 실행별 단조 증가 sequence
- Skill ID/Version과 NSO semantic hash
- Tenant ID, Subject ID, auth context reference
- Data Classification과 canonical payload
- Previous event hash와 current event hash

이벤트 hash는 current hash 자체를 제외한 canonical envelope 전체로 계산한다. 첫 이벤트의 previous hash는 `null`이며 이후 이벤트는 직전 event hash를 참조한다. 저장소를 다시 열 때 schema, event hash, sequence, hash chain을 전부 검증하며 손상·변조·순서 단절은 `AuditIntegrityError`로 거부한다.

## 5. Persistent Store Contract

`JsonlAuditStore`는 한 줄에 하나의 canonical storage envelope를 append하고 flush한다. Envelope는 deterministic audit event와 별도로 저장 시각 및 만료 시각을 보유한다. Runtime에서 append가 실패하면 recorder sequence와 chain tail을 진행시키지 않아 미기록 실행을 계속하지 않는다.

조회는 `tenant_id`와 `execution_id`를 모두 요구한다. 다른 Tenant에서 같은 Execution ID를 제시해도 이벤트를 반환하지 않는다. 현재 어댑터는 개발 및 단일 writer 배포를 위한 기준 구현이며 다중 writer transaction과 운영 DB 저장은 인프라 어댑터의 후속 범위다.

## 6. Protected Evidence

Input, Context, Tool Input은 원문 대신 hash와 classification을 기본 감사 증적으로 사용하고 `SnapshotStore`가 제공되면 reference를 함께 기록한다. Tool Result는 result hash와 result metadata를 항상 남기고 snapshot reference가 있으면 보존한다.

`CONFIDENTIAL` 또는 `RESTRICTED` EMIT은 일반 JSONL에 값을 기록하지 않는다. Audit payload에는 redacted marker, value hash, secure snapshot reference, snapshot hash, snapshot classification과 비민감 field metadata만 남는다.

## 7. Error and Authorization

실행 오류는 category 기반 phase와 현재 NSO node를 분리해 기록한다. Preflight처럼 node가 존재하지 않는 오류는 `node_id: null`을 명시한다. Cause에는 구조화된 error code와 detail code를 기록한다.

모든 이벤트 envelope는 Principal reference를 가지며 Skill과 Tool의 인가 이벤트는 decision reference를 기록한다. Default Deny가 발생하면 authorizer가 생성한 DENY decision reference를 예외 경계를 통해 보존한다. Principal 검증 자체가 실패해 인가 결정이 생성되지 않은 경우는 `NOT_EVALUATED`로 기록한다.

## 8. Slice 0030 완료 재검토

`NSL-AUD-007`은 `SnapshotStore`가 제공되거나 Tool adapter가 snapshot reference를 반환하는 경로에서 구현되었다. 보호 등급 Tool Result를 일반 audit에 원문으로 저장하지 않으며 result hash와 보호 reference를 사용한다.

Slice 0030은 `WorkerEvidencePolicy.SNAPSHOT_REQUIRED` production certification profile을 추가했다. 이 profile은 `SnapshotStore`가 없으면 Worker 구성을 fail closed하며, Runtime이 모든 Tool Result의 snapshot reference를 생성하고 Audit에 기록하는 통합 테스트를 통과한다. 이에 따라 `NSL-AUD-007`은 `IMPLEMENTED`로 승격되었다.

## 9. Boundary and Robustness

- 실행 결과: completed, authorization rejected, tool failed
- 저장 파일: missing, empty line, truncated JSON, tampered payload
- Chain: genesis, sequence gap, previous hash mismatch, restart reload
- Query: empty/non-string Tenant와 Execution ID, cross-Tenant isolation
- I/O: append/read failure와 recorder state rollback
- Evidence: Input/Context, Tool input/result, CHECK, EMIT
- Security: credential-bearing Principal reference, confidential amount/identifier non노출
- Error: Tool node location과 node 없는 preflight phase
- Authorization: Skill/Tool ALLOW 및 DENY decision reference

## 10. Traceability and Quality

Slice 0023 최초 반영 시 전체 Baseline은 `IMPLEMENTED 273`, `PARTIAL 7`, `PLANNED 45`였다. Slice 0030 follow-up 이후 `NSL-AUD-007`은 `IMPLEMENTED`다.

- Regression: `687 passed`
- Statement Coverage: `99.24%`
- Branch Coverage: `97.84%`
- Slice 시작값: Statement `99.20%`, Branch `97.73%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 11. Acceptance

- 12개 할당 Requirement에 개별 Verification과 Evidence가 연결됨
- 12개 Requirement가 최종 `IMPLEMENTED`; `NSL-AUD-007`은 Slice 0030 follow-up evidence 포함
- Runtime Kernel과 파일 저장 어댑터의 의존 방향 유지
- 재시작 후 event hash, sequence, hash chain 검증
- 일반 audit의 보호 원문 및 credential 비저장
- Error location/cause와 Principal/Authorization decision reference 기록
- 전체 Regression failure 0
- 기존 Statement/Branch Coverage 이상 유지
