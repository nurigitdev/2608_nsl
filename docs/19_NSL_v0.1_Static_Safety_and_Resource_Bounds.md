# NSL v0.1 Static Safety and Resource Bounds

- **Slice:** `0012`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 NSL Compiler가 v0.1 Skill의 최대 Tool Call, Loop Iteration, Emit Record 수를 Compile 단계에서 증명하도록 고정한다. 선언된 Resource Limit을 넘거나 정적 유한성을 증명할 수 없는 Skill은 `.nso` 생성 전에 거부한다.

## 2. 구조 경계

정적 상한 계산은 `_Lowerer`에서 독립된 `StaticBoundAnalyzer`가 담당한다. Compiler는 Typed IR을 생성하고 분석 결과와 선언된 Limit을 비교한다. Runtime은 `.nso`의 분석값을 신뢰해 제한을 생략하지 않으며 실제 사용량을 별도로 집행한다.

```text
Source -> AST -> Typed IR -> StaticBoundAnalyzer -> Limit Validation -> .nso
```

분석기는 v0.1의 모든 Statement와 Expression 형상을 명시적으로 방문한다. 알 수 없는 IR 형상과 비양수 또는 비정수 `foreach max`는 0으로 간주하지 않고 `UnboundedStructureError`로 fail-closed 처리한다.

## 3. Worst-case 계산

순차 Statement의 상한은 합산한다. `foreach`의 Collection Expression은 Loop 진입 전에 한 번 평가하고, Body 상한은 선언된 `max`만큼 반복한다.

```text
tool_calls(loop) = calls(collection) + max * calls(body)
loop_iterations(loop) = max * (1 + loop_iterations(body))
emit_records(loop) = max * emit_records(body)
```

따라서 중첩 `foreach`도 각 `max`가 곱셈적으로 반영된다. `PROJECT_BUDGET_CHECK`의 정적 결과는 Tool Call 11, Loop Iteration 10, Emit Record 10이다.

## 4. Compile 정책

| 조건 | 결과 |
|---|---|
| 정적 상한과 선언값이 같음 | Compile 허용 |
| Tool Call 상한이 선언값보다 큼 | `NSL-E6101` |
| Loop 상한이 선언값보다 큼 | `NSL-E6102` |
| Emit 상한이 선언값보다 큼 | `NSL-E6103` |
| 유한 실행을 증명할 수 없음 | `NSL-E6104` |

Resource Limit 초과 오류는 원본 `limits` 블록의 Line/Column, Snippet, Logical Path를 제공한다. v0.1 문법은 `foreach`에 양의 정적 정수 `max`를 필수로 요구하며 `while`, recursion 또는 동적 반복 상한을 제공하지 않는다.

## 5. NSO 계약

`.nso`의 `analysis`에는 다음 Canonical 필드를 포함한다.

```json
{
  "bounded": true,
  "max_emit_records": 10,
  "max_loop_iterations": 10,
  "max_tool_calls": 11
}
```

이 값은 Semantic Hash 계산 대상이며 `NsoCodec.decode()` 후 동일한 `StaticAnalysis`로 복원된다.

## 6. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-BND-001` | `IMPLEMENTED` | `TEST-BND-001` |
| `NSL-BND-002` | `IMPLEMENTED` | `TEST-BND-002` |
| `NSL-BND-003` | `IMPLEMENTED` | `TEST-BND-003` |
| `NSL-BND-004` | `IMPLEMENTED` | `TEST-BND-004` |
| `NSL-BND-005` | `IMPLEMENTED` | `TEST-BND-005` |

Slice 0012에 할당된 5개 Requirement는 모두 구현됐으며 남은 `PARTIAL`은 없다.

## 7. Slice 0003 PARTIAL 재평가

Static resource bound와 분석 불가능 구조의 Source Diagnostic 공백은 Slice 0012에서 해소됐다. Unsupported language/risk와 일부 EMIT schema/classification 오류에는 SourceSpan 연결이 남아 있으므로 `NSL-ERR-002`와 `NSL-ERR-003`은 `PARTIAL`을 유지한다.

## 8. 품질 결과

각 Requirement 구현 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 결과는 다음과 같다.

- Regression: `347 passed`
- Statement Coverage: `98.87%`
- Branch Coverage: `96.21%`
- `nsl/bounds.py`: `100%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 9. Acceptance

- 5개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0012 할당 Requirement의 `PARTIAL` 0개
- 정적 상한 계산과 Compiler lowering 책임이 분리됨
- 유한성을 증명할 수 없는 구조는 Compile 단계에서 거부됨
- Bound 분석 결과가 canonical `.nso`에 포함됨
- Regression failure 0
- Quality Gate 통과 전 Commit/Push 금지
