# NSL v0.1 Protected Snapshot and Replay

- **Slice:** `0024`
- **상태:** Accepted
- **작성일:** 2026-08-20
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 6 Implemented, 0 Partial

## 1. 목적

이 Slice는 보호 등급 실행 증거를 평문으로 저장하지 않고, 이전 실행을 기록된 입력과 Tool 결과만으로 재현한 뒤 Original Result와 자동 비교하는 최소 Replay 경계를 완성한다. Runtime과 Replay Core는 `SnapshotStore` 포트만 의존하고 암호화 및 영속 저장은 별도 어댑터가 담당한다.

## 2. Part와 Requirement 결과

### Part A: Protected Snapshot and Lifecycle

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-SEC-018` | CONFIDENTIAL/RESTRICTED Replay Snapshot 암호화 저장 | `IMPLEMENTED` |
| `NSL-SEC-020` | Audit/Replay Data 보존기간 및 삭제 정책 | `IMPLEMENTED` |

### Part B: Replay and Comparison

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-RPL-001` | 이전 실행 Replay | `IMPLEMENTED` |
| `NSL-RPL-005` | Replay Result와 Original Result 자동 비교 | `IMPLEMENTED` |
| `NSL-RPL-006` | 구조화된 Difference 보고 | `IMPLEMENTED` |
| `NSL-RPL-007` | Runtime Version 차이 기록 | `IMPLEMENTED` |

기존 `NSL-RPL-002..004`, `NSL-SEC-019/022`, `NSL-TST-010/020`도 보호 저장소를 사용하는 종단 간 시나리오로 재검증했다.

## 3. Architecture Boundary

```text
Runtime / Replay Core
    -> SnapshotStore port
        -> InMemorySnapshotStore
        -> ProtectedSnapshotStore
            -> SnapshotProtectionPort
                -> KMS/Vault adapter
```

`ProtectedSnapshotStore`는 암호 알고리즘이나 키를 직접 소유하지 않는다. `SnapshotProtectionPort`가 반환한 opaque ciphertext, algorithm ID와 key ID만 저장하며, 보호 등급 값은 포트가 없거나 sealing에 실패하면 저장을 거부한다. Runtime과 Replay 모듈이 구체 저장소를 import하지 않는 의존 방향은 architecture test로 고정한다.

## 4. Protected Snapshot Contract

- `PUBLIC`과 `INTERNAL`은 격리된 deep copy로 저장한다.
- `CONFIDENTIAL`과 `RESTRICTED`는 plaintext slot을 비우고 sealed blob만 저장한다.
- 조회는 snapshot ID, tenant ID, read scope, classification과 reference hash를 모두 검증한다.
- 조회와 삭제는 scope 확인 전에 검증된 Execution Principal을 요구한다.
- 복호화 후 canonical value hash가 reference와 다르면 무결성 오류로 거부한다.
- 만료 경계는 `now >= expires_at`이며 만료된 값은 조회할 수 없다.
- 명시 삭제와 만료 일괄 삭제는 `nsl:snapshot:delete` scope를 요구한다.

## 5. Replay Contract

`ReplayBundle`은 원래 실행 ID, semantic hash, Runtime version, Input/Context reference, 순서가 고정된 Tool call reference와 Original Result reference를 가진다. 생성 시 `SkillObject`, 원본 `ExecutionRequest`, `ExecutionResult`의 identity와 policy/store 일치 여부를 검증한다. Input/Context는 Skill에 선언된 값만 투영하고 선언된 최고 분류를 적용하며, Runtime audit에서 발견한 미선언 데이터는 fail-safe로 `RESTRICTED` 처리한다.

Replay는 실 Tool provider를 호출하지 않고 `ReplayToolExecutor`만 사용하며, 기록된 호출의 누락·추가·순서·인자 차이를 즉시 거부한다.

Replay 완료 후 Original/Current semantic result view를 자동 비교한다. 불일치는 JSON Pointer 형태의 path와 original/current 값을 가진 deterministic difference 목록으로 반환하며, 보고서에는 원래 Runtime version, 현재 Runtime version과 version changed 여부가 함께 기록된다.

## 6. Retention and Deletion

Snapshot reference에는 만료 시각이 포함된다. Audit event 자체는 결정성을 유지하기 위해 wall-clock 시간을 포함하지 않고, `JsonlAuditStore`의 storage envelope가 저장 시각과 만료 시각을 보유한다. Audit 삭제는 실행 단위로 처리해 남은 event hash chain이 끊어지지 않도록 하며, 한 실행의 모든 이벤트가 만료된 경우에만 purge한다.

## 7. Partial 재검토

Slice 0024에 할당된 6개 Requirement는 모두 완료되어 Slice 내부 `PARTIAL`은 없다.

Slice 0023의 `NSL-AUD-007`은 protected snapshot 경로에서는 만족한다. 그러나 일반 Production 실행에서 `SnapshotStore`가 여전히 선택 사항이므로 모든 Tool Result에 snapshot 또는 reference가 존재한다고 보장할 수 없다. 이 요구사항은 `PARTIAL`을 유지하고 Production profile과 SRS certification을 다루는 Slice 0030으로 이관한다.

## 8. Boundary and Robustness

- Classification: 네 등급과 보호/비보호 경계
- Protection: seal/unseal 실패, 빈 ciphertext, `NONE` algorithm, 빈 key ID
- Identity: 잘못된 tenant, scope, classification, reference hash
- Lifecycle: 만료 직전, 정확한 만료 시각, 만료 후, 개별 삭제, 일괄 purge
- Replay: 빈/잘못된 execution ID, semantic hash mismatch, 미사용·초과 Tool call
- Comparison: 동일 결과, scalar/type/list/map 차이, 누락 항목, escaped path
- Version: 동일/상이한 Runtime version과 잘못된 version 입력
- Audit: malformed storage envelope, retention interval, rewrite failure

## 9. Traceability and Quality

Slice 반영 후 전체 Baseline은 `IMPLEMENTED 279`, `PARTIAL 1`, `PLANNED 45`다.

- Regression: `737 passed`
- Statement Coverage: `99.31%` (시작값 `99.24%`)
- Branch Coverage: `98.10%` (시작값 `97.84%`)
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 10. Acceptance

- 6개 할당 Requirement에 개별 Verification과 Evidence 연결
- 보호 등급 snapshot의 평문 비저장 및 fail-closed sealing
- Tenant/scope/reference 무결성 및 retention/delete 정책 적용
- 기록된 Tool 결과만 사용하는 이전 실행 Replay
- Original/Replay 자동 비교, 구조화 Difference와 Runtime version 차이 보고
- Runtime/Replay Core와 보호 저장 어댑터의 의존 방향 유지
- 기존 Replay 보안 및 결정성 요구사항 회귀 검증
