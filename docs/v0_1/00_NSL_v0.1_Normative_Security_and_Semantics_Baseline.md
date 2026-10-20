# NSL v0.1 Normative Security and Semantics Baseline

- **상태:** Normative Baseline
- **버전:** v0.1
- **작성일:** 2026-08-18
- **적용 대상:** NSL Compiler, Runtime, Tool Gateway, Audit/Replay, NeX-AE Integration

## 1. 목적

본 문서는 여러 NSL 상세설계 문서에 걸쳐 사용되는 보안, 데이터 상태, 실행 경계의 단일 기준을 정의한다.

상세설계와 본 문서가 충돌할 경우 본 문서의 정의를 우선한다. 각 상세설계는 이후 개정에서 본 문서와 일치하도록 수정한다.

## 2. 문서 우선순위

구현과 Conformance Test는 다음 순서로 해석한다.

1. 본 Normative Baseline
2. NSL SRS의 `MUST` 요구사항
3. `.nso` IR Schema와 Semantics Profile
4. Compiler/Runtime Detailed Design
5. 예시 Source와 설명용 의사코드

동일 계층에서는 더 높은 문서 버전을 우선한다. 폐기된 정의는 문서에 `SUPERSEDED`를 표시한다.

## 3. 시스템 경계

NSL Core는 한 번의 제한된 Skill 실행을 담당하는 Deterministic Execution Kernel이다.

```text
User Chat -> NeX-AE -> Intent Contract -> Skill Catalog / Policy
                                                   |
Schedule Registry -> Schedule Runner --------------+
                                                   v
                                      Skill Execution Worker
                                                   |
                                                   v
                                             NSL Runtime
  -> Canonical Tool Gateway
  -> MCP / Business System
```

NSL v0.1 Core 책임:

- Typed and bounded Skill 실행
- Canonical READ Tool 호출
- Deterministic expression과 validation
- Structured result
- Audit, provenance, replay

NeX Schedule 책임:

- 하나의 등록된 Skill에 대한 시작 시각, Timezone, 주기, 반복 횟수 관리
- 각 예약 발생을 독립적인 Skill Execution Request로 변환
- Overlap/Misfire 정책, 비활성화, 취소, 실행 횟수 관리
- 명시적인 Service Principal과 실행별 Authorization 재검증

NeX Pack 책임:

- Skill/Policy/Tool requirement와 선택적 Schedule Template의 배포 단위
- Publisher, signature, compatibility, entitlement
- Skill discovery와 intent routing metadata

Schedule은 NSL Syntax나 `.nso`에 포함하지 않는다. Workflow Language, Multi-Skill Orchestration, WRITE, APPROVAL, watch, invoke_skill은 NSL v0.1과 현재 Schedule Extension 범위 밖이다.

## 4. Data State 단일 정의

Presence와 Completeness는 서로 다른 축이다.

```text
Presence     = PRESENT | EMPTY
Completeness = COMPLETE | PARTIAL | UNKNOWN
```

규칙:

- 정상적인 0건 조회는 `EMPTY + COMPLETE`이다.
- 일부 Page만 확보한 결과는 값에 따라 `PRESENT|EMPTY + PARTIAL`이다.
- 전체성 판단 근거가 없는 결과는 `PRESENT|EMPTY + UNKNOWN`이다.
- Tool Failure는 Presence 또는 Completeness가 아니다.
- Tool Failure에는 `ValueEnvelope`을 생성하지 않는다.
- `COMPLETE + TRUE`만 CHECK PASS가 될 수 있다.
- PARTIAL 또는 UNKNOWN은 NSL-0.1-STRICT에서 CHECK UNKNOWN이다.

`Completeness.EMPTY` 정의는 폐기한다.

## 5. Execution Principal

Production 실행은 다음 Principal을 반드시 가져야 한다.

```text
ExecutionPrincipal
  tenant_id
  subject_id
  actor_type        USER | SERVICE
  roles
  scopes
  on_behalf_of      optional
  auth_context_ref
```

규칙:

- `tenant_id`와 `subject_id`는 Production에서 필수다.
- 예약/이벤트 실행은 명시적인 SERVICE Principal을 사용한다.
- 사용자 대신 실행하면 `on_behalf_of`와 위임 범위를 기록한다.
- 인증 Token과 Credential 원문은 ExecutionContext, Trace, `.nso`에 저장하지 않는다.
- Runtime은 인증을 직접 수행하지 않지만 검증 완료된 Principal과 Authorization Decision을 요구한다.

## 6. Authorization

Skill 실행과 각 Tool 호출은 별도의 Authorization Decision을 갖는다.

```text
AuthorizationDecision
  decision_id
  policy_id
  policy_version
  effect            ALLOW | DENY
  granted_scopes
  decided_at
```

규칙:

- 기본 정책은 Deny다.
- Skill Required Scope와 Tool Required Scope가 모두 충족되어야 한다.
- Tool Binding Resolution은 Tenant뿐 아니라 Principal과 granted scope를 고려한다.
- Tool 호출 Audit에는 원본 Credential 대신 `decision_id`를 기록한다.
- Replay는 외부 Tool을 호출하지 않지만 Replay 데이터 열람 권한을 별도로 검사한다.

## 7. Data Protection

업무 데이터는 최소 다음 분류를 지원한다.

```text
PUBLIC
INTERNAL
CONFIDENTIAL
RESTRICTED
```

`ValueEnvelope`, Tool Contract Field, Input/Output Schema는 Data Classification을 선언하거나 상위 기본값을 상속할 수 있어야 한다.

Production 규칙:

- Trace와 일반 Audit Event에 CONFIDENTIAL/RESTRICTED 원문을 기록하지 않는다.
- Tool Result와 Replay 원문은 암호화된 Snapshot Store에 저장한다.
- Audit에는 Snapshot Reference, Hash, Classification을 기록한다.
- Snapshot Store는 Tenant 격리, 접근 통제, 보존기간, 삭제 정책을 제공해야 한다.
- Error message와 Diagnostic에서 Credential 및 민감값을 Redact한다.
- Hash는 무결성 검증 수단이며 접근 통제를 대체하지 않는다.

Development의 In-memory Snapshot Store는 Test 전용이며 Production 구현으로 간주하지 않는다.

## 8. Audit / Replay

Audit Event는 다음을 식별할 수 있어야 한다.

- execution, skill version, semantic hash
- Principal과 Authorization Decision Reference
- Tool invocation과 Contract/Binding version
- Value/Snapshot hash와 classification
- Presence, Completeness, CHECK result
- Runtime/Semantics profile version

Replay Bundle은 Input, Context, Tool Result 원문을 직접 포함하지 않고 Secure Snapshot Reference를 사용한다. Replay 결과 비교에서는 새 Execution ID와 Trace 시각을 제외한다.

## 9. PROJECT_BUDGET_CHECK Vertical Slice Acceptance

Vertical Slice는 다음을 모두 만족해야 한다.

1. `.ns` Source를 Compiler가 Typed `SkillObject`로 변환한다.
2. Canonical `.nso` encode/decode 후 Semantic Hash가 유지된다.
3. Production 형식의 ExecutionPrincipal이 없으면 실행을 거부한다.
4. Required Scope가 없으면 Tool 호출 전에 실행을 거부한다.
5. Mock Tool 결과로 Budget CHECK가 PASS한다.
6. Audit에는 민감한 Budget/Expense 원문이 노출되지 않는다.
7. Replay는 실제 Mock/MCP Provider를 호출하지 않는다.
8. Replay Output, CHECK, Status가 원 실행과 동일하다.
9. Tool Failure 또는 Partial Data에서 False PASS가 발생하지 않는다.
