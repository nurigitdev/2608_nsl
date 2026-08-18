# NSL v0.1 Source Model and Lexer Conformance

- **Slice:** `0004`
- **상태:** Accepted
- **작성일:** 2026-08-18
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 NSL v0.1 Source 입력과 Lexer의 계약을 코드와 회귀 검사로 고정한다. 문자열 입력의 기존 호환성을 유지하면서 논리 경로, 안정된 Source ID, UTF-8 디코딩, 정확한 위치 정보를 갖는 `SourceFile`을 도입하고, 모든 Token을 명시적인 종류와 범주로 분류한다.

## 2. Source Model 결정

1. NSL Source의 파일 확장자는 정확히 `.ns`이다.
2. Source byte 입력은 UTF-8 또는 UTF-8 BOM만 허용하며, 잘못된 byte sequence는 명시적으로 거부한다.
3. `SourceFile.from_text()`는 논리 경로와 원문으로 결정적인 Source ID를 생성한다.
4. `SourcePosition`은 zero-based offset과 one-based line/column을 함께 보존한다.
5. `SourceSpan`은 Source ID와 half-open start/end 위치를 보존한다.
6. 기존 `Compiler.compile(str)` 계약은 유지하고 내부에서 메모리 Source로 정규화한다.

## 3. Lexer 결정

1. Token은 `TokenKind`, 원시 `lexeme`, 정규화된 `value`, `SourceSpan`을 갖는 immutable value object이다.
2. Token 범주는 Keyword, Identifier, Literal, Operator, Delimiter, End로 명시한다.
3. Identifier는 NSL v0.1의 ASCII 규칙을 따르며 Keyword와 Boolean은 대소문자를 구분한다.
4. 주석의 `/`와 나눗셈 연산자를 구분하고 문자열 내부의 주석 표시는 원문으로 보존한다.
5. Duration literal은 `ms`, `s`, `m` 단위를 millisecond 값으로 정규화하되 identifier 경계를 침범하지 않는다.
6. `tokenize()`는 첫 Lexer 진단을 `CompileError`로 반환하는 strict API이다.
7. `scan()`은 잘못된 문자를 건너뛰며 가능한 진단을 수집하고 항상 EOF Token으로 종료하는 recovery API이다.

## 4. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-SRC-001` | `IMPLEMENTED` | `TEST-SRC-001` |
| `NSL-SRC-002` | `IMPLEMENTED` | `TEST-SRC-002` |
| `NSL-SRC-003` | `IMPLEMENTED` | `TEST-SRC-003` |
| `NSL-SRC-004` | `IMPLEMENTED` | `TEST-SRC-004` |
| `NSL-SRC-005` | `IMPLEMENTED` | `TEST-SRC-005` |
| `NSL-SRC-006` | `IMPLEMENTED` | `TEST-SRC-006` |
| `NSL-SRC-007` | `IMPLEMENTED` | `TEST-SRC-007` |
| `NSL-SRC-009` | `IMPLEMENTED` | `TEST-SRC-009` |
| `NSL-LEX-001` | `IMPLEMENTED` | `TEST-LEX-001` |
| `NSL-LEX-002` | `IMPLEMENTED` | `TEST-LEX-002` |
| `NSL-LEX-003` | `IMPLEMENTED` | `TEST-LEX-003` |
| `NSL-LEX-004` | `IMPLEMENTED` | `TEST-LEX-004` |
| `NSL-LEX-005` | `IMPLEMENTED` | `TEST-LEX-005` |
| `NSL-LEX-006` | `IMPLEMENTED` | `TEST-LEX-006` |
| `NSL-LEX-007` | `IMPLEMENTED` | `TEST-LEX-007` |
| `NSL-LEX-008` | `IMPLEMENTED` | `TEST-LEX-008` |
| `NSL-LEX-009` | `IMPLEMENTED` | `TEST-LEX-009` |
| `NSL-TST-001` | `IMPLEMENTED` | `TEST-TST-001` |

Slice 0004에 할당된 18개 Requirement는 모두 구현되었으며 할당 범위 안에 남은 `PARTIAL`은 없다. 전체 Baseline 상태는 `IMPLEMENTED 80`, `PARTIAL 184`, `PLANNED 61`이다.

## 5. 후속 Slice 경계

- `include`는 이 Slice에서 예약 Keyword로만 고정한다. include 구문 분석과 Source 조합은 후속 Parser/Compiler Slice 범위이다.
- Duration은 이 Slice에서 독립 Token으로만 고정한다. AST 및 type/semantic 해석은 후속 Slice 범위이다.
- SourceSpan은 Source와 Token까지 전파된다. AST Node와 Semantic Diagnostic의 전체 Span/Snippet 전파는 아직 완료되지 않았으므로 `NSL-ERR-002`, `NSL-ERR-003`은 `PARTIAL`을 유지한다.
- `NSL-SRC-008`, `NSL-SRC-010`은 include 보안과 결정성 검증이 필요한 별도 할당 Requirement이므로 이번 Slice에서 상태를 앞당기지 않는다.

## 6. Acceptance

각 Requirement 구현 직후 `tools/run_quality.py`로 Traceability, 전체 Regression, Statement/Branch Coverage를 반복 검증했다. Slice 완료 기준은 다음과 같다.

- 18개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- 할당 Requirement의 미완료 상태가 있으면 `PARTIAL`로 명시
- Regression failure 0
- Statement Coverage 95% 이상
- Branch Coverage 90% 이상
- Quality Gate 통과 전 Commit/Push 금지
