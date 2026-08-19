# NSL v0.1 READ Execution Semantics

- **Slice:** `0018`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **신규 완료 범위:** 7 Requirements

## 1. 목적

이 Slice는 Compiler가 생성한 READ부터 Runtime의 Tool 호출, Result 검증, Audit까지 하나의 fail-closed 실행 경계로 고정한다. Slice 0001에서 이미 완료한 `NSL-READ-005`를 유지하면서 나머지 7개 READ Requirement를 완료한다.

## 2. 내부 Part

| Part | 범위 | Requirement |
|---|---|---|
| A. Registration and Parameters | Registered Tool, parameter name/type | `NSL-READ-001..002` |
| B. Structured Result | Envelope, output schema, error preservation | `NSL-READ-003..005` |
| C. Operational Integrity | Timeout, Audit, result hash | `NSL-READ-006..008` |

## 3. READ 실행 경계

```text
ReadExpr
    -> required tool_ref 확인
    -> ToolRegistry.resolve(EXACT)
    -> parameter 이름·중복 검증
    -> authorization
    -> parameter expression 평가
    -> ToolContractValidator request 검증
    -> timeout이 적용된 ToolExecutor 호출
    -> structured result·schema·hash 검증
    -> Audit와 provenance 생성
```

Runtime의 READ 로직은 `_execute_read`로 분리했다. 일반 Expression 평가와 외부 Tool 신뢰 경계가 한 메서드에 섞이지 않으며 Runtime은 concrete Mock/MCP 구현에 의존하지 않는다.

## 4. Registered Tool과 Parameter

Compiler는 `requires`에 없는 Tool과 canonical Registry에 없는 Tool을 거부한다. Runtime은 다시 해시된 변조 IR의 미등록 `tool_ref`도 Executor 호출 전에 Runtime Contract 오류로 차단한다.

Parameter는 이름 집합, 중복 부재, `ValueEnvelope.type_info`, 실제 Python 값의 재귀 Type schema를 확인한다. Source와 IR 모두에서 중복 이름이 dict 변환으로 덮어써지지 않는다.

## 5. Structured Result와 Schema

모든 Executor 결과는 `ToolResultEnvelope`여야 한다. Runtime은 invocation ID, Tool ID/Version, TypeRef, Presence, Completeness, Classification, Snapshot Reference의 구조를 확인한다.

구조 검증 후 declared output Type, 실제 값의 재귀 schema와 output classification을 canonical Tool Contract와 대조한다. Primitive output도 typed envelope 안에서는 허용하지만 plain dict나 text 반환은 structured Tool Result로 인정하지 않는다.

Tool 실행 오류는 Empty Result로 변환하지 않는다. 기존 `NSL-READ-005`의 명시적 `TOOL_ERROR` 동작과 테스트를 그대로 유지한다.

## 6. Timeout

`ToolContract.timeout_ms`는 contract hash에 포함되는 canonical 정수다. 허용 범위는 `1..2147483647`이며 bool, float, 0, 음수와 범위 초과를 거부한다.

Timeout primitive는 Tool boundary의 `execute_with_timeout`에 위치한다. Runtime Core는 async framework를 직접 import하지 않으며 초과 시 Executor task를 취소하고 `TOOL_TIMEOUT`의 명시적 `TOOL_ERROR`를 반환한다.

## 7. Input/Output Audit

각 invocation은 같은 Invocation ID로 다음 이벤트를 연결한다.

- `TOOL_STARTED.input`: parameter 이름과 canonical argument hash
- `TOOL_COMPLETED.output`: result hash, snapshot reference, Type, Presence, Completeness, Classification
- `TOOL_FAILED.output`: 실패 status와 안정적인 error code

업무 원문 값은 Tool Audit payload에 복제하지 않는다. Input은 hash로, Output은 hash와 snapshot reference로 기록해 데이터 보호 경계를 유지한다. Tool이 성공한 뒤 Runtime collection limit가 발생해도 Tool 완료 Output은 먼저 Audit된다.

## 8. Result Hash

`tool_result_hash`는 `encode_value`, canonical JSON, SHA-256을 사용한다. Mapping key 순서와 무관하게 같은 값은 같은 hash를 만들고 값이 바뀌면 hash가 달라진다.

Runtime은 Executor의 `result_hash`를 재계산해 비교한다. 검증되지 않은 hash는 Audit 또는 provenance에 사용하지 않고 `TOOL_RESULT_HASH_MISMATCH`로 거부한다.

## 9. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-READ-001` | `IMPLEMENTED` | `TEST-READ-001` |
| `NSL-READ-002` | `IMPLEMENTED` | `TEST-READ-002` |
| `NSL-READ-003` | `IMPLEMENTED` | `TEST-READ-003` |
| `NSL-READ-004` | `IMPLEMENTED` | `TEST-READ-004` |
| `NSL-READ-005` | `IMPLEMENTED` | `TEST-READ-005` |
| `NSL-READ-006` | `IMPLEMENTED` | `TEST-READ-006` |
| `NSL-READ-007` | `IMPLEMENTED` | `TEST-READ-007` |
| `NSL-READ-008` | `IMPLEMENTED` | `TEST-READ-008` |

Slice 0018에서 새로 완료한 7개 Requirement와 기존 `READ-005`를 포함해 READ 8개 전체가 `IMPLEMENTED`다. Slice 0018에 남은 `PARTIAL`은 없다.

## 10. Slice 0003 PARTIAL 재평가

READ 오류는 안정적으로 구조화됐지만 변조 IR 오류는 `.ns` Source 위치가 아니다. Unsupported language/risk와 일부 EMIT schema/classification 오류의 AST SourceSpan 연결도 남아 있으므로 `NSL-ERR-002`와 `NSL-ERR-003`은 `PARTIAL`을 유지한다.

## 11. 품질 결과

각 Requirement 변경 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 코드 결과는 다음과 같다.

- Regression: `519 passed`
- Statement Coverage: `99.09%`
- Branch Coverage: `97.34%`
- `nsl/tools.py`: `100%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`
- Slice 시작값: Statement `99.08%`, Branch `97.18%`

## 12. Acceptance

- 신규 7개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- READ 8개 Requirement 전체 `IMPLEMENTED`
- 미등록 Tool과 parameter mismatch가 provider 호출 전에 차단됨
- malformed result, output schema와 hash mismatch가 fail-closed 처리됨
- timeout이 명시적 `TOOL_ERROR`로 처리됨
- 성공·실패·후처리 limit invocation의 Input/Output Audit이 연결됨
- Runtime Core의 async framework 독립성이 유지됨
- Regression failure 0
- 기존 Statement/Branch Coverage 이상 유지
- Quality Gate 통과 전 Commit/Push 금지
