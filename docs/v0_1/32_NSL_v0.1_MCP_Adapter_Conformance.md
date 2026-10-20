# NSL v0.1 MCP Adapter Conformance

- **Slice:** `0025`
- **상태:** Accepted
- **작성일:** 2026-08-20
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 3 Implemented, 0 Partial

## 1. 목적

이 Slice는 Canonical NSL Tool Contract를 고객별 MCP binding과 연결하는 Adapter 경계를 구현한다. Runtime Core는 기존 `ToolExecutionPort`만 사용하며 MCP SDK, endpoint, credential 및 wire-format 변환을 알지 못한다.

## 2. Requirement 결과

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-EXE-003` | MCPToolExecutor Adapter 구조 | `IMPLEMENTED` |
| `NSL-EXE-006` | Tool 실행시간 측정 | `IMPLEMENTED` |
| `NSL-PY-007` | MCP Adapter와 Runtime Core 분리 | `IMPLEMENTED` |

Slice 0025에 할당된 Requirement는 모두 완료되어 Slice 내부 `PARTIAL`은 없다.

## 3. Architecture Boundary

```text
RuntimeEngine
    -> ToolExecutionPort
        -> MeasuringToolExecutor
            -> MCPToolExecutor                 nsl.adapters.mcp
                -> MCPBindingResolver
                -> MCPClientPort
                    -> MCP SDK / transport     deployment adapter
```

`RuntimeEngine`과 `tools.py`는 `MCPToolExecutor` 또는 `nsl.adapters`를 import하지 않는다. MCP Adapter는 `core`, `data_protection`, `tools`만 참조하며 Runtime, Compiler, IR, Audit, Replay와 분리된다. 이 방향은 AST architecture test로 고정한다.

## 4. MCP Binding and Invocation

`MCPToolBinding`은 Tenant, canonical Tool ID/Version, opaque server reference, MCP Tool name과 structured result path를 연결한다. Binding Registry는 Tenant/Tool/Version exact key를 사용하고 중복 또는 cross-Tenant lookup을 거부한다. Binding surface에는 credential 원문을 저장할 수 없다.

`MCPToolExecutor`의 처리 순서는 다음과 같다.

1. Canonical Tool Contract resolve 및 request validation
2. Tenant별 MCP binding resolve와 identity 재검증
3. NSL 값을 JSON-safe MCP arguments로 canonical encoding
4. SDK 중립 `MCPClientPort.call_tool` 호출
5. MCP result envelope, `isError`, `structuredContent` 검증
6. Binding result path 선택 및 NSL 값 decoding
7. Canonical output contract 검증과 `ToolResultEnvelope` 생성

MCP Tool의 업무 오류는 `isError: true`로, 성공 구조화 결과는 `structuredContent`로 처리한다. 이는 [Model Context Protocol Tools 사양](https://modelcontextprotocol.io/specification/2025-11-25/server/tools)의 `tools/call` 계약과 일치한다. 비구조화 text content를 업무 데이터로 임의 parsing하지 않으며 오류 content도 일반 예외 메시지에 노출하지 않는다.

## 5. Error Normalization

| Adapter condition | NSL error code |
|---|---|
| Canonical contract 없음/불일치 | `TOOL_CONTRACT_MISMATCH` |
| Tenant binding 없음 | `MCP_BINDING_NOT_FOUND` |
| Resolver identity 불일치 | `MCP_BINDING_MISMATCH` |
| MCP client transport/protocol 실패 | `MCP_CLIENT_ERROR` |
| MCP `isError: true` | `MCP_TOOL_ERROR` |
| Malformed result envelope/encoded value | `MCP_MALFORMED_RESULT` |
| 성공 응답의 structured content 없음 | `MCP_STRUCTURED_CONTENT_REQUIRED` |
| Binding result path 없음 | `MCP_RESULT_PATH_MISSING` |
| Canonical output schema 불일치 | `OUTPUT_CONTRACT_VIOLATION` |

## 6. Tool Execution Measurement

`MeasuringToolExecutor`는 Mock, MCP, Replay를 포함한 모든 `ToolExecutionPort` 구현을 동일하게 감싼다. monotonic clock의 호출 전후 차이를 nanosecond 정수로 측정하며 millisecond 정수 view도 제공한다. 음수 clock 차이는 0으로 제한하고 정상 반환, 오류, 취소 outcome을 구분한다.

Measurement에는 Execution/Invocation/Node/Tool/Tenant 식별자와 outcome, duration만 포함하며 argument, result, credential은 포함하지 않는다. Timing은 별도 `ToolMeasurementSink`로 전달하고 deterministic Audit Event와 `ExecutionResult`에는 넣지 않는다. 따라서 기존 semantic result와 audit hash chain의 결정성을 유지한다.

## 7. Boundary and Robustness

- Binding: empty identifier, duplicate, unknown Tenant, identity mismatch, credential material
- Contract: unknown/incompatible version, forged contract hash, invalid input
- MCP request: JSON-safe Money/Decimal/Date encoding과 context reference 전달
- MCP result: root/path selection, empty collection, malformed content/isError/structured content
- Failure: tool-declared error, client failure, missing path, decoding 및 output contract failure
- Timing: negative/zero, 1ms 직전/정확/초과, normal/error/cancelled outcome
- Separation: nested Adapter package까지 eval/exec/LLM/arbitrary import gate 적용

## 8. Traceability and Quality

Slice 반영 후 전체 Baseline은 `IMPLEMENTED 282`, `PARTIAL 1`, `PLANNED 42`다.

- Regression: `783 passed`
- Statement Coverage: `99.33%` (시작값 `99.31%`)
- Branch Coverage: `98.14%` (시작값 `98.10%`)
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 9. Acceptance

- 3개 할당 Requirement에 개별 Verification과 Evidence 연결
- Canonical Contract와 Tenant MCP binding의 분리
- 공식 MCP structured result/error 경계의 fail-closed 변환
- MCP SDK/transport를 client port 뒤에 격리
- 모든 Tool port의 monotonic execution timing 측정
- deterministic Audit/Result 및 기존 Mock/Replay code path 유지
- Runtime Core와 MCP Adapter 의존 방향 architecture test 통과
