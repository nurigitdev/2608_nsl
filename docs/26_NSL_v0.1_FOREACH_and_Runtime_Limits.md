# NSL v0.1 FOREACH and Runtime Limits

- **Slice:** `0019`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 18 Requirements

## 1. 목적

이 Slice는 FOREACH의 정적·동적 유한성과 Runtime Resource Limit 집행을 하나의 fail-closed 실행 경계로 고정한다. 선언된 정적 상한을 Compiler가 확인하는 Slice 0012의 결과 위에서 Runtime은 실제 사용량을 독립적으로 계측하고 제한한다.

## 2. 내부 Part

| Part | 범위 | Requirement |
|---|---|---|
| A. FOREACH | max, 실제 반복, 순서, depth, sequential execution | `NSL-FOR-001..008` |
| B. Resource Limits | 선언, tool/loop/emit/duration/collection, 실패 상태 | `NSL-LIM-001..008` |
| C. Cross-cutting Tests | bounded loop와 resource worst case | `NSL-TST-006`, `NSL-TST-009` |

## 3. 구조 경계

```text
Source limits
    -> AstLimits
    -> immutable ResourceLimits in NSO
    -> ExecutionContext
    -> ResourceMeter + ResourceGuard
    -> ExecutionResult.resources / LIMIT_EXCEEDED
```

`ResourceMeter`는 실제 사용량만 보존하고 `ResourceGuard`는 제한 판정을 담당한다. Runtime의 Tool, FOREACH, EMIT, collection 코드에 분산됐던 비교 로직은 guard의 safe point로 통합했다.

전체 실행시간은 `RuntimeClock` Protocol로 주입한다. Production은 monotonic system clock을 사용하고 Test는 fake clock으로 1ms 경계를 결정론적으로 재현한다. Tool 호출 timeout은 canonical Tool Contract timeout과 남은 전체 실행시간 중 짧은 값으로 제한한다.

## 4. FOREACH Semantics

- 모든 FOREACH는 양의 정수 `max`를 명시한다.
- collection 크기가 statement max를 초과하면 body 실행 전에 중단한다.
- 실제 시작된 iteration만 `loop_iterations`에 기록한다.
- 중첩 FOREACH depth의 Runtime 기본 상한은 16이다.
- source collection 순서를 그대로 보존하며 v0.1은 parallel FOREACH를 지원하지 않는다.
- while과 같은 unbounded loop 구문은 Parser가 허용하지 않는다.

## 5. Resource Limit Semantics

`limits`는 tool call, loop iteration, emitted row, collection size를 필수로 정의한다. 전체 실행시간은 `duration 2s;`처럼 선택적으로 선언하며 생략 시 canonical 기본값 `60000ms`를 AST와 NSO에 명시한다.

- Tool Call은 Runtime이 허용한 invocation attempt를 계측하며 제한된 다음 호출은 executor 전달 전에 차단한다.
- Loop Iteration은 body 진입 전에 예약하고 실제 시작된 반복 수를 보존한다.
- Emitted Row는 output에 성공적으로 추가된 row만 계측한다.
- Collection은 input/context와 READ 유입에서 최대 크기를 계측하고 모든 expression 결과에서 크기 제한을 검사한다.
- Duration은 statement, expression, loop, emit, Tool 경계의 monotonic deadline으로 집행한다.
- 모든 초과는 `NSL-E6001`, `RESOURCE`, `LIMIT_EXCEEDED`로 반환하며 CHECK FAIL로 변환하지 않는다.

## 6. Boundary와 Robustness

- FOREACH max `1`, `10`, `0`, 음수, decimal, max 누락, max+1을 검증했다.
- 실제 iteration `0`, `1`, `10`과 nested depth `16`, `17`을 검증했다.
- Duration `1ms`, `5m`, 생략 기본값, `0ms`, 잘못된 literal, 중복 field를 검증했다.
- Tool/loop/emit/collection은 정확한 상한과 한도 초과를 각각 검증했다.
- forged zero limit 다섯 종류가 static analysis 변조와 관계없이 Runtime에서 차단되는 기존 보안 경계를 유지한다.
- 정상 Vertical Slice에서 tool `2`, loop `1`, emit `1`, max collection `3`을 동시에 검증했다.

## 7. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-FOR-001..008` | `IMPLEMENTED` | `TEST-FOR-001..008` |
| `NSL-LIM-001..008` | `IMPLEMENTED` | `TEST-LIM-001..008` |
| `NSL-TST-006` | `IMPLEMENTED` | `TEST-TST-006` |
| `NSL-TST-009` | `IMPLEMENTED` | `TEST-TST-009` |

Slice 0019에 할당된 18개 Requirement는 모두 `IMPLEMENTED`이며 남은 `PARTIAL`은 없다. 전체 Baseline 상태는 `IMPLEMENTED 238`, `PARTIAL 42`, `PLANNED 45`다.

## 8. Slice 0003 PARTIAL 재평가

Runtime limit 오류는 안정적인 code/category/status를 제공하지만 `.ns` Source Diagnostic은 아니다. Unsupported language/risk와 일부 EMIT schema/classification 오류의 SourceSpan 공백도 남아 있으므로 `NSL-ERR-002`와 `NSL-ERR-003`은 `PARTIAL`을 유지한다.

## 9. 품질 결과

각 Requirement 변경 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 결과는 다음과 같다.

- Regression: `567 passed`
- Statement Coverage: `99.12%`
- Branch Coverage: `97.39%`
- Slice 시작값: Statement `99.09%`, Branch `97.34%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 10. Acceptance

- 18개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- FOREACH와 Resource Limits 범위의 `PARTIAL` 없음
- Resource usage와 limit 판단의 구조적 책임이 분리됨
- 전체 실행시간과 Tool timeout이 하나의 deadline으로 제한됨
- 모든 limit이 CHECK FAIL과 분리된 `LIMIT_EXCEEDED`로 반환됨
- Regression failure 0
- 기존 Statement/Branch Coverage 이상 유지
- Quality Gate 통과 전 Commit/Push 금지
