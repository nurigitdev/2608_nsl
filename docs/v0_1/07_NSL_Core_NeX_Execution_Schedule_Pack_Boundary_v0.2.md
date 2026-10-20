# NSL Core / NeX Execution / Schedule / Pack Boundary v0.2

- **상태:** Slice 0002 Rebaseline
- **작성일:** 2026-08-18
- **근거:** `FINANCE.PROJECT_BUDGET_CHECK` Vertical Slice

## 1. 결정

NSL은 Agent 전체를 표현하는 언어가 아니라 한 번의 업무 Skill을 검증 가능하게 실행하는 Deterministic Execution Kernel로 유지한다.

직무 Agent는 다음 구성의 제품 단위로 정의한다.

```text
Role Agent
  = Skill Pack
  + Optional Schedule Binding
  + Policy
  + Customer Tool Bindings
  + NSL Runtime
```

## 2. Target Architecture

```text
User Chat -> NeX-AE -> Intent / Slot Resolution -> Skill Catalog / Policy
                                                              |
Schedule Registry -> Schedule Runner --------------------------+
                                                              v
                                                 Skill Execution Worker
                                                              |
                                                              v
                                                         NSL Runtime
                                                              |
                                                              v
                                                 Canonical Tool Gateway
                                                              |
                                                              v
                                               Customer MCP / Business System
```

## 3. NeX-AE 책임

NeX-AE는 자연어와 대화 상태를 다음 `ExecutionIntent`로 정규화한다.

```text
ExecutionIntent
  intent_id
  candidate_skill_id
  skill_version_constraint
  extracted_inputs
    value
    source              USER | CONTEXT | DEFAULT
    confidence
  missing_inputs
  principal_ref
  requested_action
  confirmation_state
```

NeX-AE 책임:

- Intent와 Skill 후보 검색
- 날짜, 기간, 조직 등 Input Slot 추출
- 누락/모호 Input에 대한 사용자 확인
- 자연어 설명과 Runtime Result 분리

NeX-AE가 하지 않는 일:

- 고객 MCP Endpoint 직접 선택
- NSL 업무 규칙을 LLM으로 대신 평가
- Runtime Permission 우회
- Tool 결과를 임의로 COMPLETE 또는 PASS로 승격

## 4. Skill Catalog / Policy 책임

Catalog는 다음 Metadata를 기준으로 Skill을 Resolve한다.

```text
Skill ID / Version
Intent description / example utterance
Input / Context / Output Schema
Risk / Effect
Required Scope
Required Canonical Tool Contract
Supported Runtime / Semantics Profile
Confirmation Policy
Publisher / Signature / Certification
```

Policy 계층은 Skill 실행과 Tool 호출을 Default Deny로 평가하고 Authorization Decision Reference를 생성한다.

## 5. NeX Schedule 책임

Schedule은 정해진 시각에 하나의 등록된 Skill을 정해진 횟수만큼 실행한다.

```text
schedule_id
skill_id / version
start_at / timezone
interval / repeat_count
static input bindings
service principal reference
overlap / misfire policy
enabled / cancelled
```

Schedule은 NSL Source 내부 Statement로 구현하지 않는다. 각 예약 발생은 일반 `SkillExecutionRequest`로 변환되며 NSL Runtime은 Schedule Store나 Runner에 의존하지 않는다.

초기 Schedule에서 지원하지 않는 기능:

- 여러 Skill의 연결과 결과 전달
- 분기, 병렬 실행, Join
- 승인, 대기, 보상
- Dynamic Planning과 Agent 위임
- WRITE와 자동 Retry

## 6. NSL Core 책임

NSL Core:

- Source parsing과 static validation
- Typed, canonical, bounded `.nso`
- Immutable binding
- Canonical Tool invocation
- Strict PASS / FAIL / UNKNOWN
- Resource guard
- Structured output
- Audit / provenance / replay

NSL Core에 포함하지 않는 것:

- LLM
- Scheduler와 Event Subscription
- Durable approval state
- Customer Credential
- Customer-specific MCP URL
- Pack entitlement와 billing

## 7. Canonical Tool Gateway 책임

Tool Gateway는 Canonical Tool Contract를 고객 환경의 MCP/업무시스템에 Binding한다.

책임:

- Tenant, Principal, Scope 기반 Binding Resolution
- Credential injection
- Input/output mapping과 schema validation
- Timeout, pagination, completeness 판정
- Stable ordering
- Contract/Binding version Audit

NSL Source와 `.nso`에는 Credential, Endpoint, 고객별 Tool Name을 포함하지 않는다.

## 8. Pack 책임

`.nsp` 또는 향후 Role Pack Manifest는 다음을 포함한다.

```text
publisher
pack_id / version
skills
policies
tool requirements
runtime compatibility
intent metadata
schedule templates
tests / certification
signature
license / entitlement
deprecation / revocation
```

Source 비공개 배포가 필요하면 `.nso`를 배포하되, 인증 Test와 Tool Binding Conformance Fixture는 Pack에 포함한다.

## 9. Vertical Slice에서 확인된 Contract

`PROJECT_BUDGET_CHECK` 구현으로 다음 경계가 검증되었다.

1. Compiler와 Runtime은 동일 immutable IR을 사용한다.
2. Live와 Replay는 동일 Runtime Code Path와 Tool Port를 사용한다.
3. Tool Scope가 없으면 Provider 호출 전에 거부된다.
4. Tool Failure에는 Value를 생성하지 않는다.
5. PARTIAL Data는 CHECK PASS가 되지 않는다.
6. Audit에는 CONFIDENTIAL 원문 대신 Hash와 Snapshot Reference가 남는다.
7. Replay Snapshot은 별도 Scope와 Tenant 격리를 적용한다.

## 10. 다음 구현 순서

1. Vertical Slice Parser를 전체 NSL v0.1 EBNF로 확장
2. `.nso` JSON Schema와 Conformance Fixture 확정
3. Production Audit/Snapshot Port 구현
4. Skill Catalog와 `ExecutionIntent` Contract 구현
5. Customer Tool Binding Conformance Test 구현
6. 단일 Skill Schedule Contract와 Runner 구현
7. Workflow Language와 WRITE/APPROVAL은 NSL v0.1 이후 별도 검토
