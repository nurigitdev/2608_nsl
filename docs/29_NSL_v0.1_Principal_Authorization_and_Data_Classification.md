# NSL v0.1 Principal, Authorization and Data Classification

- **Slice:** `0022`
- **상태:** Accepted
- **작성일:** 2026-08-20
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 3 Requirements

## 1. 목적

이 Slice는 Production 실행의 Principal 신뢰 경계와 Credential 비저장 원칙, Error/Diagnostic Redaction을 고정한다. 기존 Default Deny Authorization과 네 단계 Data Classification 구현도 새 경계에서 회귀 검증한다.

## 2. Requirement 범위

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-SEC-012` | Production에서 검증된 Tenant/Subject Principal 요구 | `IMPLEMENTED` |
| `NSL-SEC-016` | `.nso`, ExecutionContext, Trace에 Credential 원문 비저장 | `IMPLEMENTED` |
| `NSL-SEC-021` | Error와 Diagnostic의 Credential 및 민감값 Redaction | `IMPLEMENTED` |

Trace assignment에는 기존 구현된 `NSL-SEC-014/015/017`도 같은 그룹에 포함되어 있다. 이 세 Requirement는 Slice 0001의 `IMPLEMENTED` 상태를 유지하고 이번 Slice에서 회귀 검증만 수행했다. 따라서 Slice 0022에서 새로 상태가 변경되는 Requirement는 위 3개다.

## 3. Principal 경계

`ExecutionPrincipal`은 `UNVERIFIED` 또는 `VERIFIED` 상태를 명시한다. Runtime 기본 환경은 `PRODUCTION`이며 다음 순서로 실행을 통제한다.

```text
ExecutionRequest
    -> Principal type/structure validation
    -> Production verification validation
    -> Skill authorization
    -> ExecutionContext creation
    -> Tool authorization
```

Principal이 누락되거나 미검증 또는 형식 오류이면 ExecutionContext와 Tool provider를 생성·호출하기 전에 `NSL-E5201` 구조화 결과로 거부한다. `DEVELOPMENT`는 명시적으로 선택할 때만 구조가 유효한 미검증 Principal을 허용한다.

## 4. Credential 비저장

`data_protection.py`는 Credential key, Authorization scheme, JWT, Private Key 형식을 공통 탐지한다.

- `ExecutionRequest.inputs`와 `runtime_context` 생성 경계에서 원문을 거부한다.
- `NsoCodec.encode/decode` 양쪽에서 `.nso` Credential material을 거부한다.
- `ExecutionContext`와 `ToolCallRequest`에는 Credential 저장 필드를 두지 않는다.
- 일반 Audit payload는 Data Classification 검사 전에 Credential을 방어적으로 Redact한다.
- `auth_context_ref`와 `authorization_decision_ref` 같은 opaque reference만 전달한다.

## 5. Error와 Diagnostic Redaction

공통 Redactor는 다음 외부 노출 및 기록 경계에 적용된다.

- Compiler/Lexer/Parser/Semantic `Diagnostic` message, snippet, logical path
- Runtime과 Tool failure의 `ExecutionResult.error`
- `EXECUTION_FAILED` 및 Tool failure Audit payload
- 보호된 debug trace의 Credential 및 실행 중 수집된 고등급 분류값

Runtime은 현재 ExecutionContext의 `CONFIDENTIAL` 및 `RESTRICTED` `ValueEnvelope`를 canonical encoding한 뒤 문자열과 식별 가능한 scalar 값을 수집한다. Provider 오류가 해당 값을 포함해도 Result, Audit, debug trace에는 `[REDACTED]`로 기록한다.

## 6. Authorization과 Classification 재검증

- Skill과 Tool은 별도 Authorization Decision을 사용한다.
- 빈 action, 빈 Required Scope, 잘못된 Scope 형식은 Default Deny로 거부한다.
- Tool Scope 부족은 provider 호출 전에 거부한다.
- Data Classification은 `PUBLIC`, `INTERNAL`, `CONFIDENTIAL`, `RESTRICTED`의 폐쇄된 네 단계다.
- 기존 `NSL-SEC-014`, `NSL-SEC-015`, `NSL-SEC-017`, `NSL-TST-018`은 `IMPLEMENTED`를 유지한다.

## 7. Boundary와 Robustness

- Principal: missing, unverified, malformed, verified, Development override
- Credential key: exact key, normalized key, 유사하지만 비민감한 key
- Credential value: Bearer/Basic, key-value, JWT, Private Key, nested collection
- Request: inputs/context mapping type과 nested credential
- NSO: encode 시 생성 객체와 decode 시 변조 payload
- Diagnostic: message/snippet/path
- Runtime: Tool controlled failure와 unexpected debug trace
- Authorization: empty action, empty/non-frozenset/malformed Required Scope

## 8. Traceability 결과

Slice 0022의 3개 Requirement는 모두 `IMPLEMENTED`이며 `PARTIAL`은 없다. 전체 Baseline 상태는 `IMPLEMENTED 262`, `PARTIAL 18`, `PLANNED 45`다.

## 9. 품질 결과

각 Requirement 변경 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 문서 반영 전 구현 결과는 다음과 같다.

- Regression: `663 passed`
- Statement Coverage: `99.20%`
- Branch Coverage: `97.73%`
- Slice 시작값: Statement `99.16%`, Branch `97.51%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 10. Acceptance

- 3개 Slice Requirement 모두 전용 Verification과 Evidence를 가짐
- Slice 0022 범위의 미완료 Requirement 없음
- Production Principal 검증이 Context 및 Provider 실행보다 먼저 수행됨
- Credential 원문이 `.nso`, ExecutionContext, 일반 Trace에 저장되지 않음
- Error, Diagnostic, Audit, debug trace에서 Credential 및 고등급 분류값이 Redact됨
- 기존 Default Deny Authorization과 4단계 Data Classification 회귀 통과
- 전체 Regression failure 0
- 기존 Statement/Branch Coverage 이상 유지
