# NSL v0.1 Tool Registry and Mock Executor

- **Slice:** `0017`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **확정 범위:** 13 Requirements

## 1. 목적

이 Slice는 NSL Runtime과 외부 Tool 구현 사이의 canonical contract 경계를 고정한다. Registry는 Tool identity, version, capability, input/output schema를 관리하고 Runtime은 실행 전에 모든 required Tool을 resolve한다. 실제 호출은 `ToolExecutor` Port를 통해서만 수행하며 Mock Executor도 같은 contract validation을 통과해야 한다.

## 2. 내부 Part

| Part | 범위 | Requirement |
|---|---|---|
| A. Registry Model | Registry, identity, capability, schema, version | `NSL-TOL-001..005` |
| B. Runtime Resolution | required resolve, mismatch, MCP 비노출 | `NSL-TOL-006..008` |
| C. Executor and Mock | Port, Mock, dependency boundary, validation, invocation ID | `NSL-EXE-001`, `002`, `004`, `005`, `007` |

## 3. Tool Registry

`ToolRegistry`는 `get(tool_id, version)`과 `resolve(tool_id, requested_version)`을 제공하는 runtime-checkable Protocol이다. `ToolContractCatalog`가 기본 구현이며 Runtime은 concrete Catalog가 아니라 이 Protocol에 의존한다.

Registry key는 `(Tool ID, Version)`이다. 동일 key의 중복 등록은 `DuplicateToolContractError`로 거부한다. 같은 전역 Tool ID의 서로 다른 Version은 동일 Tool의 version history로 등록할 수 있으며 v0.1 resolution 정책은 `EXACT`다.

Version은 leading zero가 없는 canonical `major.minor.patch` 숫자 형식만 허용한다. `0.0.0`과 큰 정수 component는 허용하지만 누락 component, 문자 component, 음수와 leading zero는 거부한다.

## 4. Canonical Contract

`ToolContract`는 다음 의미 필드를 관리하며 모두 contract hash에 포함한다.

- Tool ID와 Version
- Capability와 Risk
- 순서가 보존된 Input 이름/Type schema
- Output Type schema
- Required Scope와 Output Classification
- Tool Timeout
- Empty result policy

Slice 0018은 Tool Timeout을 canonical `timeout_ms` 필드로 구체화했으며 이 값도 contract hash에 포함한다.

Capability는 비어 있지 않아야 한다. Input 이름은 비어 있지 않고 중복될 수 없으며 Input/Output schema는 `TypeRef`여야 한다. 고객별 endpoint, credential, MCP provider binding은 Canonical Contract, Registry, NSL Source와 NSO에 포함하지 않는다.

## 5. Runtime Resolution

```text
Skill required_tools
    -> ToolRegistry.resolve(EXACT)
    -> Canonical contract hash comparison
    -> Authorization and argument evaluation
    -> Contract validation
    -> ToolExecutor.execute()
```

미등록 Tool, 비호환 Version, contract hash 변경은 provider 호출 전에 실패한다. Runtime preflight에서 required Tool 전체를 먼저 확인하고 각 호출 직전에도 공통 validator를 적용한다.

## 6. Tool Executor Port

`ToolExecutor`는 async `execute(ToolCallRequest) -> ToolResultEnvelope` Protocol이다. 기존 `ToolExecutionPort`는 호환 alias로 유지한다. Runtime은 Port만 참조하며 Mock 또는 향후 MCP adapter 구현체를 import하지 않는다.

MCP adapter 구조 자체는 `NSL-EXE-003`과 `NSL-PY-007`이 할당된 Slice 0025 범위이며 이번 Slice에 포함하지 않는다.

## 7. Contract Validator

`ToolContractValidator`는 호출 전에 다음을 검증한다.

- Registry가 resolve한 canonical contract hash
- Input argument 이름 집합
- 각 `ValueEnvelope.type_info`
- 실제 Python 값의 재귀 Type schema

값 검증은 Bool, Date, DateTime, Decimal, Int, String, Year, Domain, Money currency, List item, Record field, Enum을 처리하며 unknown Type kind는 fail-closed다.

Mock Executor는 handler 반환 후 Output schema도 검증한다. 잘못된 request는 fixture handler에 도달하지 않고 `call_count`도 증가시키지 않는다. 잘못된 output은 provider 호출 이후 `OUTPUT_CONTRACT_VIOLATION`으로 처리된다.

## 8. Mock Executor

Mock fixture mapping은 Executor 생성 시 복사해 외부 mapping 변경과 격리한다. 동일 contract, request, fixture 결과는 동일 immutable `ToolResultEnvelope`와 SHA-256 result hash를 생성한다.

`call_count`는 contract validation과 fixture resolution을 통과해 handler를 실제 호출한 횟수다. Validation 실패와 fixture 누락은 provider call로 계산하지 않는다.

## 9. Invocation ID

Runtime은 ExecutionContext별 counter를 사용해 `inv0001`, `inv0002` 순서로 Invocation ID를 생성한다. ID는 실행마다 `inv0001`로 초기화되며 `TOOL_STARTED`와 `TOOL_COMPLETED` audit event가 동일 ID를 공유한다.

## 10. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-TOL-001` | `IMPLEMENTED` | `TEST-TOL-001` |
| `NSL-TOL-002` | `IMPLEMENTED` | `TEST-TOL-002` |
| `NSL-TOL-003` | `IMPLEMENTED` | `TEST-TOL-003` |
| `NSL-TOL-004` | `IMPLEMENTED` | `TEST-TOL-004` |
| `NSL-TOL-005` | `IMPLEMENTED` | `TEST-TOL-005` |
| `NSL-TOL-006` | `IMPLEMENTED` | `TEST-TOL-006` |
| `NSL-TOL-007` | `IMPLEMENTED` | `TEST-TOL-007` |
| `NSL-TOL-008` | `IMPLEMENTED` | `TEST-TOL-008` |
| `NSL-EXE-001` | `IMPLEMENTED` | `TEST-EXE-001` |
| `NSL-EXE-002` | `IMPLEMENTED` | `TEST-EXE-002` |
| `NSL-EXE-004` | `IMPLEMENTED` | `TEST-EXE-004` |
| `NSL-EXE-005` | `IMPLEMENTED` | `TEST-EXE-005` |
| `NSL-EXE-007` | `IMPLEMENTED` | `TEST-EXE-007` |

Slice 0017에 할당된 13개 Requirement는 모두 구현됐으며 남은 `PARTIAL`은 없다.

## 11. Slice 0003 PARTIAL 재평가

Registry와 Runtime Tool 오류가 구조화됐지만 `.ns` Source Error의 Line/Column과 Snippet 요구사항과는 별도 영역이다. Unsupported language/risk와 일부 EMIT schema/classification Source 오류에 AST SourceSpan 연결이 남아 있으므로 `NSL-ERR-002`와 `NSL-ERR-003`은 `PARTIAL`을 유지한다.

## 12. 품질 결과

각 Requirement 변경 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 코드 결과는 다음과 같다.

- Regression: `493 passed`
- Statement Coverage: `99.08%`
- Branch Coverage: `97.18%`
- `nsl/tools.py`: `100%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 13. Acceptance

- 13개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0017 할당 Requirement의 `PARTIAL` 0개
- Registry 중복, Version, Capability, Schema 불변식이 강제됨
- Runtime required Tool resolution과 contract hash 검증이 선행됨
- Runtime이 concrete Mock/MCP 구현에 의존하지 않음
- Mock request/output contract validation이 적용됨
- Invocation ID가 실행별로 결정적이고 audit와 일치함
- Regression failure 0
- 기존 Coverage 지표 이상 유지
- Quality Gate 통과 전 Commit/Push 금지
