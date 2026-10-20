# NSL v0.1 EMIT and Structured Result

- **Slice:** `0021`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 9 Requirements, 2 Diagnostic carry-overs

## 1. 목적

이 Slice는 Runtime의 최종 output을 Typed Structured Record로 고정하고, 값의 Type과 Data Quality가 손실되지 않는 deterministic JSON Result를 제공한다. Runtime과 Tool 실패는 자연어 문자열이 아니라 명시적 status와 구조화된 error로 반환한다.

## 2. 내부 Part

| Part | 범위 | Requirement |
|---|---|---|
| A. Typed EMIT | Typed Record, Output Schema, Money/Date Type | `NSL-EMT-001`, `NSL-EMT-002`, `NSL-EMT-005` |
| B. Collection and Serialization | List Result, emitted_rows, Result Codec | `NSL-EMT-003`, `NSL-EMT-004`, `NSL-EMT-006` |
| C. Structured Failure | Runtime Error, Tool Error, Partial Result | `NSL-REL-003..005` |
| Carry-over | Source 위치와 Snippet 진단 종결 | `NSL-ERR-002`, `NSL-ERR-003` |

## 3. 구조 경계

```text
Output Schema + EmitStatement
    -> Runtime schema guard
    -> Mapping[field, ValueEnvelope]
    -> immutable EmitRecord
    -> ExecutionResult
    -> Result Codec
    -> deterministic JSON
```

`RuntimeEngine`은 field expression 평가와 schema guard를 담당하고 `EmitRecord`는 typed field를 보존한다. `result_codec.py`는 비교용 semantic view와 외부 전송용 lossless serialization을 분리한다. Result Codec은 Compiler, Tool, Audit 또는 Replay 구현에 의존하지 않는다.

## 4. Typed EMIT

- 각 field는 raw value가 아니라 `ValueEnvelope`로 보존한다.
- Field Type, Presence, Completeness, Classification, Provenance를 유지한다.
- 기존 `.values`와 `.classifications` 읽기 API는 immutable view로 제공한다.
- Compiler는 output/emit의 누락, 추가, 중복, Type, Classification 불일치를 거부한다.
- Runtime은 변조된 IR의 field set, declared Type, Classification을 다시 검증한다.
- Money는 currency를 포함한 TypeRef로, Date와 DateTime은 서로 구분된 TypeRef로 보존한다.

## 5. Result Serialization

외부 Result는 schema version `1.0`을 사용하고 다음 정보를 JSON으로 제공한다.

- Execution 및 Skill identity와 semantic hash
- Execution status와 Result completeness
- CHECK 결과와 provenance
- 순서가 보존된 output record list
- 각 output field의 Type, encoded value, Presence, Completeness, Classification, Provenance
- Resource usage와 구조화된 error

Money, Decimal, Date, DateTime은 tagged value로 인코딩한다. JSON은 UTF-8 text, key sort, compact separator, non-finite number 거부 규칙으로 결정적으로 생성한다.

## 6. Structured Failure

- Runtime validation 오류는 `FAILED`와 `RuntimeErrorInfo`로 반환한다.
- Tool provider 오류는 `TOOL_ERROR`, `TOOL` category와 provider detail code를 보존한다.
- ExecutionStatus와 Completeness는 서로 다른 축이다.
- 성공적으로 종료되어도 관찰 데이터가 PARTIAL 또는 UNKNOWN이면 Result completeness를 그대로 유지한다.
- 실패 전에 일부 output이 생성되면 PARTIAL, output 생성 전 실패는 UNKNOWN으로 표시한다.

## 7. Boundary와 Robustness

- output/emit field의 missing, extra, duplicate와 duplicate output schema를 검증했다.
- Compiler와 Runtime의 Type/Classification 이중 검증을 확인했다.
- Money currency, Date와 DateTime의 유사 타입 및 잘못된 값을 거부했다.
- emit record `0`, `1`, `3`과 emitted_rows `0`, limit, limit+1을 검증했다.
- 정상, Runtime failure, Tool failure, PARTIAL, UNKNOWN, interrupted execution을 직렬화했다.
- Result JSON 반복 생성과 live/replay semantic view의 결정성을 유지했다.

## 8. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-EMT-001..006` | `IMPLEMENTED` | `TEST-EMT-001..006` |
| `NSL-REL-003..005` | `IMPLEMENTED` | `TEST-REL-003..005` |
| `NSL-ERR-002..003` | `IMPLEMENTED` | `TEST-ERR-002..003` |

Slice 0021에 할당된 9개 Requirement는 모두 `IMPLEMENTED`이며 남은 `PARTIAL`은 없다. Slice 0003 carry-over 2개도 종결했다. 전체 Baseline 상태는 `IMPLEMENTED 259`, `PARTIAL 21`, `PLANNED 45`다.

## 9. 품질 결과

각 Requirement 변경 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 결과는 다음과 같다.

- Regression: `618 passed`
- Statement Coverage: `99.16%`
- Branch Coverage: `97.51%`
- Slice 시작값: Statement `99.13%`, Branch `97.40%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 10. Acceptance

- 9개 Slice Requirement와 2개 carry-over 모두 전용 Verification과 Evidence를 가짐
- Slice 0021 및 Slice 0003 Diagnostic 범위의 `PARTIAL` 없음
- Output Schema와 Runtime Emit Record가 Compiler와 Runtime에서 모두 검증됨
- Typed field metadata와 Data Quality가 Result serialization에서 보존됨
- Tool/Runtime/Partial execution이 명시적으로 구분됨
- 전체 Regression failure 0
- 기존 Statement/Branch Coverage 이상 유지
- Quality Gate 통과 전 Commit/Push 금지
