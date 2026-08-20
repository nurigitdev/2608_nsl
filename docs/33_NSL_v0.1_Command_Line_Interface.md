# NSL v0.1 Command Line Interface

- **Slice:** `0026`
- **상태:** Accepted
- **작성일:** 2026-08-20
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 11 Implemented, 0 Partial

## 1. 목적

이 Slice는 NSL Compiler, NSO Trust Boundary, Runtime, Mock Tool, Replay를 하나의 설치 가능한 `nsl` 명령으로 연결한다. CLI는 새로운 업무 의미를 구현하지 않고 기존 Core API를 호출하는 외부 오케스트레이션 경계다. 모든 정상 결과와 오류는 결정적인 JSON으로 출력한다.

## 2. Part와 Requirement 결과

### Part A: Static Commands and Return Contract

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-CLI-001` | `nsl parse <file.ns>` | `IMPLEMENTED` |
| `NSL-CLI-002` | `nsl check <file.ns>` | `IMPLEMENTED` |
| `NSL-CLI-003` | `nsl compile <file.ns> -o <file.nso>` | `IMPLEMENTED` |
| `NSL-CLI-004` | `nsl inspect <file.nso>` | `IMPLEMENTED` |
| `NSL-CLI-011` | 일관된 Return Code와 JSON Error | `IMPLEMENTED` |

### Part B: Execution and Replay

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-CLI-005` | `.ns` 직접 실행 | `IMPLEMENTED` |
| `NSL-CLI-006` | 검증된 `.nso` 실행 | `IMPLEMENTED` |
| `NSL-CLI-007` | `--input` JSON File | `IMPLEMENTED` |
| `NSL-CLI-008` | `--context` JSON File | `IMPLEMENTED` |
| `NSL-CLI-009` | `--fixture` Mock Tool Fixture | `IMPLEMENTED` |
| `NSL-CLI-010` | `nsl replay`와 결과 비교 | `IMPLEMENTED` |

Slice 0026에 할당된 Requirement는 모두 완료되어 Slice 내부 `PARTIAL`은 없다. 기존 `NSL-AUD-007`의 `PARTIAL`은 변경하지 않는다.

## 3. Command Surface

```text
nsl parse SOURCE.ns
nsl check SOURCE.ns [--tool-contracts TOOLS.json]
nsl compile SOURCE.ns -o OUTPUT.nso [--tool-contracts TOOLS.json]
nsl inspect OUTPUT.nso
nsl run PROGRAM.ns|PROGRAM.nso --principal PRINCIPAL.json
    [--tool-contracts TOOLS.json] [--input INPUT.json]
    [--context CONTEXT.json] [--fixture FIXTURE.json]
    [--execution-id ID] [--replay-out EXECUTION.nsr]
nsl replay PROGRAM.ns|PROGRAM.nso --bundle EXECUTION.nsr
    --principal PRINCIPAL.json [--tool-contracts TOOLS.json]
    [--execution-id ID]
```

`pyproject.toml`의 console script와 `python -m nsl` 진입점을 모두 제공한다. `run`과 `replay`는 Production Runtime의 권한 경계를 유지하기 위해 검증된 Principal 파일을 필수로 요구한다.

## 4. Architecture Boundary

```text
nsl CLI
    -> CLI JSON/config validation
    -> FileSystemIncludeResolver
    -> NslCompiler / NsoCodec
    -> RuntimeEngine
        -> MockToolExecutor
    -> ReplayBundle / replay_and_compare
```

파일 include는 root source의 부모 디렉터리 아래로 제한하며 canonical path와 resolved filesystem path를 모두 검사한다. Compiler와 Runtime은 CLI 또는 filesystem adapter를 import하지 않는다. Tool Contract 파일은 한 번만 읽어 동일 Catalog 객체를 compile과 run에 공유하므로 파일 변경에 따른 contract TOCTOU를 피한다.

## 5. JSON Contracts

Tool Contract Catalog는 `tools` 배열을 가지며 각 항목은 canonical Tool ID, exact version, `READ` capability, input/output `TypeRef`, required scope와 output classification을 선언한다. endpoint, credential 또는 고객 binding은 허용하지 않는다.

Principal은 Tenant/Subject/Actor, role, scope, opaque auth context reference와 `VERIFIED` 상태를 가진다. credential material은 Principal, input, context, fixture 모든 CLI 데이터 경계에서 거부한다.

Mock Fixture 형식은 다음과 같다.

```json
{
  "schema_version": "1.0",
  "tools": [{
    "tool_id": "PROJECT.READ",
    "version": "1.0.0",
    "cases": [{"arguments": {"year": 2026}, "result": []}]
  }]
}
```

Tool/version은 Catalog와 일치해야 하며 argument object는 exact match한다. 중복 Tool ID, 중복 arguments, 알 수 없는 contract, 잘못된 result type은 fail closed 처리한다.

## 6. Replay Package Boundary

`run --replay-out`은 기존 `create_replay_bundle`이 최소화한 Input, Context, 순서가 고정된 Tool 결과와 Original semantic result를 `.nsr`로 직렬화한다. Tool argument 원문은 저장하지 않고 canonical argument hash만 저장한다. `replay`는 package hash, semantic hash, Tenant, Classification, Tool Contract와 result type을 검증한 뒤 실제 Tool provider 대신 `ReplayToolExecutor`로 실행하고 Original/Replay 결과를 자동 비교한다.

Standalone CLI에는 KMS/Vault key provider가 없으므로 `.nsr` 평문 파일은 `PUBLIC`과 `INTERNAL`만 허용한다. `CONFIDENTIAL` 또는 `RESTRICTED`가 선언된 Skill은 실행 전에 package export를 거부하며 `ProtectedSnapshotStore` backend 사용을 요구한다. Package hash는 손상 탐지용이며 발행자 인증 서명이 아니다. Package signature와 배포 신뢰는 Slice 0028의 범위다.

## 7. Return Codes

| Code | 의미 |
|---:|---|
| `0` | 성공 또는 Replay match |
| `2` | CLI 사용법 오류 |
| `3` | 파일 I/O 오류 |
| `4` | Source, NSO, JSON, Contract 검증 오류 |
| `5` | 권한 또는 Runtime 실행 실패 |
| `6` | Replay semantic/sequence 불일치 |
| `70` | 예상하지 못한 내부 오류 |

오류는 stderr에 `{"error":{"code","message","details?"}}` 형태로 출력한다. Runtime 실패의 검증된 `ExecutionResult`와 Replay difference는 `details`에 포함하지만 내부 exception text와 credential은 노출하지 않는다.

## 8. Boundary and Robustness

- Source: invalid UTF-8, syntax error, missing file, secure include와 symlink escape
- Catalog: missing/extra field, duplicate contract/input, invalid TypeRef/version/classification
- Artifact: malformed/tampered NSO와 semantic identity
- Principal: unverified actor, missing scope, duplicate role/scope, credential field
- Input/Context: non-object root, wrong runtime type, missing declared value, credential material
- Fixture: unknown/duplicate Tool, empty/duplicate case, nonmatching arguments, invalid result
- Replay: package hash/format/digest, Tenant/semantic/classification mismatch, Tool sequence/result mismatch
- Protection: high-classification portable export, missing replay scope, corrupt recorded snapshot
- Return: usage, validation, I/O, execution, mismatch와 redacted internal failure

## 9. Traceability and Quality

Slice 반영 후 전체 Baseline은 `IMPLEMENTED 293`, `PARTIAL 1`, `PLANNED 31`이다.

- Regression: `849 passed`
- Statement Coverage: `99.41%` (시작값 `99.33%`)
- Branch Coverage: `98.31%` (시작값 `98.14%`)
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 10. Acceptance

- 11개 할당 Requirement에 개별 Verification과 Evidence 연결
- 설치 가능한 `nsl` 및 module entry point 제공
- parse/check/compile/inspect의 Compiler와 NSO Trust Boundary 재사용
- `.ns`와 `.nso`의 동일 Runtime 실행 계약
- 엄격한 Principal/Input/Context/Fixture JSON 경계
- protected data를 평문 replay package로 내보내지 않는 fail-closed 정책
- deterministic Replay comparison과 일관된 Return Code
- 요구사항별 전체 Regression과 Coverage 현재값 유지
