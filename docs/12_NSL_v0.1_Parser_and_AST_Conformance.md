# NSL v0.1 Parser and AST Conformance

- **Slice:** `0005`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 NSL v0.1 Token Stream을 source-oriented AST로 변환하는 Parser 계약을 고정한다. 재귀 하강 Parser와 precedence climbing expression Parser를 유지하고, Root Skill과 Include Fragment를 명시적으로 구분하며, Parser가 생성하는 모든 AST Node에 SourceSpan을 보존한다.

## 2. Parser와 AST 결정

1. Parser는 handwritten recursive descent 방식이며 expression에는 precedence climbing을 사용한다.
2. 모든 Parser 생성 AST Node는 원래 Source ID와 half-open SourceSpan을 갖는다.
3. Binary, Call, Path/Field Access, Read, Boolean Literal expression을 독립 AST로 보존한다.
4. Let, Foreach, Check, Emit statement를 source 순서와 block scope 그대로 보존한다.
5. `ParseMode.ROOT_SKILL`과 `ParseMode.INCLUDE_FRAGMENT`를 분리한다.
6. Include Fragment는 `include`, `requires`, `context`, `limits`만 허용한다.
7. Include Declaration은 AST에 보존하며, Slice 0006부터 Resolver/Composer를 거쳐 제거된 후에만 Compiler lowering을 허용한다. Resolver가 없는 Include compile은 `NSL-E2300`으로 차단한다.
8. Parser 오류는 안정된 코드, 기대 Token, Line/Column과 가능한 Source Snippet을 제공한다.
9. Collection field access는 AST에서 path로 유지하고 type-aware projection은 Lowering에서 수행한다.

## 3. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-PAR-001` | `IMPLEMENTED` | `TEST-PAR-001` |
| `NSL-PAR-002` | `IMPLEMENTED` | `TEST-PAR-002` |
| `NSL-PAR-003` | `IMPLEMENTED` | `TEST-PAR-003` |
| `NSL-PAR-004` | `IMPLEMENTED` | `TEST-PAR-004` |
| `NSL-PAR-005` | `IMPLEMENTED` | `TEST-PAR-005` |
| `NSL-PAR-006` | `IMPLEMENTED` | `TEST-PAR-006` |
| `NSL-PAR-007` | `IMPLEMENTED` | `TEST-PAR-007` |
| `NSL-PAR-008` | `IMPLEMENTED` | `TEST-PAR-008` |
| `NSL-PAR-009` | `IMPLEMENTED` | `TEST-PAR-009` |
| `NSL-PAR-010` | `IMPLEMENTED` | `TEST-PAR-010` |
| `NSL-PAR-011` | `IMPLEMENTED` | `TEST-PAR-011` |
| `NSL-PAR-012` | `IMPLEMENTED` | `TEST-PAR-012` |
| `NSL-PAR-013` | `IMPLEMENTED` | `TEST-PAR-013` |
| `NSL-PAR-014` | `IMPLEMENTED` | `TEST-PAR-014` |
| `NSL-PAR-015` | `IMPLEMENTED` | `TEST-PAR-015` |
| `NSL-PAR-016` | `IMPLEMENTED` | `TEST-PAR-016` |
| `NSL-PAR-017` | `IMPLEMENTED` | `TEST-PAR-017` |
| `NSL-TST-002` | `IMPLEMENTED` | `TEST-TST-002` |
| `NSL-TST-003` | `IMPLEMENTED` | `TEST-TST-003` |

Slice 0005에 할당된 19개 Requirement는 모두 구현되었으며 할당 범위 안에 남은 `PARTIAL`은 없다. 전체 Baseline 상태는 `IMPLEMENTED 99`, `PARTIAL 165`, `PLANNED 61`이다.

## 4. 기존 PARTIAL 재평가

- Slice 0004에 배정된 18개 Requirement는 모두 `IMPLEMENTED`이며 재평가할 `PARTIAL`이 없다.
- `NSL-ERR-002`는 Source/Token/Parser AST의 위치 보존까지 완료됐지만 Semantic 오류 전체의 AST 위치 연결이 남아 `PARTIAL`을 유지한다.
- `NSL-ERR-003`은 Lexer와 Parser snippet까지 완료됐지만 Semantic 오류 전체의 snippet 연결이 남아 `PARTIAL`을 유지한다.

## 5. 후속 Slice 경계

- Include Source resolve, path 보안, cycle/depth/count 제한과 structured composition은 Parser 책임이 아니며 Slice 0006의 Include 계층에서 구현되었다.
- Include Fragment 간 requires/context/limits merge 및 conflict 검사는 Slice 0006의 `SourceComposer`가 담당한다.
- AST는 collection path를 유지하고 IR Projection은 type-aware Lowering이 소유한다.
- Semantic Diagnostic의 정확한 SourceSpan/Snippet 연결이 완료되기 전까지 `NSL-ERR-002/003`을 승격하지 않는다.

## 6. Acceptance

각 Requirement 구현 직후 `tools/run_quality.py`로 Traceability, 전체 Regression, Statement/Branch Coverage를 반복 검증했다.

- 19개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Parser golden snapshot과 structured negative suite 제공
- 할당 Requirement의 미완료 상태가 있으면 `PARTIAL`로 명시
- Regression failure 0
- Statement Coverage 95% 이상
- Branch Coverage 90% 이상
- Quality Gate 통과 전 Commit/Push 금지
