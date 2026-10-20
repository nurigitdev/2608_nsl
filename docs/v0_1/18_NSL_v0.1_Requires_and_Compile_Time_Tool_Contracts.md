# NSL v0.1 Requires and Compile-time Tool Contracts

- **Slice:** `0011`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 NSL Compiler가 Skill의 `requires` 선언을 Canonical Business Tool Contract와 검증하고, 고객별 MCP Binding과 Endpoint를 Compile 경계 밖에 유지하도록 고정한다. Tool 선언과 호출 오류는 원래 Source 위치를 보존한다.

## 2. Canonical Contract 경계

Compiler가 Resolve하는 Canonical Contract 정보는 다음과 같다.

```text
tool_id
version
capability
risk
input schema
output schema
required scope
output classification
contract hash
```

`ToolContract.contract_hash`는 위 실행 계약을 결정하는 필드를 포함한다. `.nso`의 `RequiredTool`에는 Canonical Tool Fingerprint만 저장하며 고객 Binding, Credential, Endpoint, MCP URL 또는 Provider Mapping을 저장하지 않는다.

동일한 `.nso`는 재Compile 없이 서로 다른 Runtime `ToolExecutionPort`에 연결할 수 있다. 고객별 Binding 선택과 Provider 연결은 Runtime Port 구현의 책임이다.

## 3. Compile 정책

| 검증 | 정책 |
|---|---|
| Tool 선언 | 모든 `read` Tool은 `requires`에 존재해야 함 |
| Tool 존재 | Canonical `ToolContractCatalog`에 등록되어야 함 |
| Version | v0.1은 Exact Compatibility만 허용 |
| Capability | `READ`만 허용 |
| Schema | Canonical Input/Output Type을 Typed IR에 반영 |
| 고객 Binding | Compile 입력 및 NSL 문법에서 금지 |

존재하지 않는 Tool은 `NSL-E4001`, 존재하지만 Version이 정확히 일치하지 않는 Tool은 `NSL-E4002`로 구분한다.

## 4. Source Diagnostic

`AstRequiredTool`은 개별 Tool 선언의 SourceSpan을 보존한다. 다음 오류는 Line/Column, Snippet, Logical Path를 제공한다.

- 중복 `requires` Tool
- 존재하지 않는 Canonical Tool
- 호환되지 않는 Tool Version
- non-READ Capability
- 미선언 `read`
- Tool 인자 이름 또는 Type 불일치

## 5. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-ARC-012` | `IMPLEMENTED` | `TEST-ARC-012` |
| `NSL-REQ-001` | `IMPLEMENTED` | `TEST-REQ-001` |
| `NSL-REQ-003` | `IMPLEMENTED` | `TEST-REQ-003` |
| `NSL-REQ-004` | `IMPLEMENTED` | `TEST-REQ-004` |
| `NSL-REQ-005` | `IMPLEMENTED` | `TEST-REQ-005` |
| `NSL-REQ-007` | `IMPLEMENTED` | `TEST-REQ-007` |
| `NSL-REQ-008` | `IMPLEMENTED` | `TEST-REQ-008` |

Slice 0011에 할당된 7개 Requirement는 모두 구현되었으며 남은 `PARTIAL`은 없다. `NSL-REQ-002`와 `NSL-REQ-006`은 Slice 0001에서 이미 구현되었다.

## 6. Slice 0003 PARTIAL 재평가

Tool 계약 및 호출 관련 Source Diagnostic 공백은 Slice 0011에서 해소됐다. Static resource bound와 일부 상위 Semantic/EMIT 오류에는 SourceSpan 연결이 남아 있으므로 `NSL-ERR-002`와 `NSL-ERR-003`은 `PARTIAL`을 유지한다.

## 7. 품질 결과

각 Requirement 구현 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 결과는 다음과 같다.

- Regression: `335 passed`
- Statement Coverage: `98.86%`
- Branch Coverage: `96.04%`
- `nsl/tools.py`: `100%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 8. Acceptance

- 7개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0011 할당 Requirement의 `PARTIAL` 0개
- Compiler와 고객별 Tool Binding의 경계가 분리됨
- Tool 계약 오류가 Source 위치를 보존함
- Regression failure 0
- Quality Gate 통과 전 Commit/Push 금지
