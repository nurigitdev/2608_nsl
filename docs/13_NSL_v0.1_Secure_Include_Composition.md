# NSL v0.1 Secure Include Composition

- **Slice:** `0006`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 `include`를 안전하고 결정적인 Compiler Frontend 기능으로 완성한다. Include Source는 Text Concatenation 없이 각 Source별 Lexer/Parser를 거쳐 AST로 합성하며, Source 공급 권한과 경로 보안 검증을 분리한다. 합성이 끝난 AST만 Semantic Lowering으로 전달하고 Include 메타데이터는 Runtime IR에 포함하지 않는다.

## 2. Architecture 결정

```text
Root SourceFile
  -> Parser(ROOT_SKILL)
  -> IncludeResolver
  -> SourceBundleBuilder
       canonical path / cycle / depth / count / UTF-8 bytes
       dependency graph / Source Manifest / bundle hash
  -> SourceComposer
       requires / context / limits merge
  -> Semantic Lowerer
  -> immutable SkillObject
```

1. Compiler Core는 filesystem을 직접 읽지 않고 주입된 `IncludeResolver`로 Source를 공급받는다.
2. `IncludeOptions`는 Include Root, 최대 깊이 16, 최대 파일 100, 전체 UTF-8 Source 10 MiB를 기본값으로 제공한다.
3. Compiler는 Resolver 호출 전에 POSIX 논리 경로를 canonicalize하고, Resolver가 반환한 Logical Path도 기대 경로와 일치하는지 재검증한다.
4. Circular Include는 active Source ID로 검출하고, Diamond Include는 canonical Source ID별 한 번만 합성한다. 모든 선언 관계는 dependency edge로 보존한다.
5. 각 Fragment는 독립된 `SourceFile`과 `ParseMode.INCLUDE_FRAGMENT`로 파싱하므로 원래 SourceSpan, Line/Column, Snippet, Logical Path를 유지한다.
6. `requires`는 Tool ID별 동일 Version만 Set Merge하고 Version 충돌은 거부한다.
7. `context` 이름 중복은 거부하며 `limits`는 합성 결과에 정확히 한 블록만 허용한다. Root가 Include를 선언한 경우 Fragment가 단일 `limits`를 제공할 수 있다.
8. 합성 후 Include 선언은 제거된다. graph, manifest, bundle hash는 `CompilationResult`에 남지만 Runtime IR에는 들어가지 않는다.

## 3. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-ARC-013` | `IMPLEMENTED` | `TEST-ARC-013` |
| `NSL-INC-001` | `IMPLEMENTED` | `TEST-INC-001` |
| `NSL-INC-002` | `IMPLEMENTED` | `TEST-INC-002` |
| `NSL-INC-003` | `IMPLEMENTED` | `TEST-INC-003` |
| `NSL-INC-004` | `IMPLEMENTED` | `TEST-INC-004` |
| `NSL-INC-005` | `IMPLEMENTED` | `TEST-INC-005` |
| `NSL-INC-006` | `IMPLEMENTED` | `TEST-INC-006` |
| `NSL-INC-007` | `IMPLEMENTED` | `TEST-INC-007` |
| `NSL-INC-008` | `IMPLEMENTED` | `TEST-INC-008` |
| `NSL-INC-009` | `IMPLEMENTED` | `TEST-INC-009` |
| `NSL-INC-010` | `IMPLEMENTED` | `TEST-INC-010` |
| `NSL-INC-011` | `IMPLEMENTED` | `TEST-INC-011` |
| `NSL-INC-012` | `IMPLEMENTED` | `TEST-INC-012` |
| `NSL-SRC-008` | `IMPLEMENTED` | `TEST-SRC-008` |
| `NSL-SRC-010` | `IMPLEMENTED` | `TEST-SRC-010` |
| `NSL-TST-014` | `IMPLEMENTED` | `TEST-TST-014` |

Slice 0006에 할당된 16개 Requirement는 모두 구현되었으며 할당 범위 안에 남은 `PARTIAL`은 없다. 전체 Baseline 상태는 `IMPLEMENTED 115`, `PARTIAL 165`, `PLANNED 45`이다.

## 4. Boundary와 Robustness 검증

- Include 깊이 `15`, `16`, 제한 초과를 검증했다.
- 고유 Include 파일 수 `99`, `100`, 제한 초과를 검증했다.
- UTF-8 byte 기준 정확한 상한, 상한 초과, `0`, 기본 10 MiB를 검증했다.
- Absolute Path, Windows Drive/UNC, backslash, NUL, 잘못된 확장자, Root Traversal, Resolver misrouting을 거부한다.
- Circular 및 Diamond graph, 누락 Source, 중복 Tool Version, Context, Limits, Limits 0개를 검증했다.

## 5. Slice 0003 PARTIAL 재평가

- `NSL-ERR-002`: Slice 0007에서 Symbol/Scope 오류까지 Line/Column과 Logical Path 전파가 완료됐다. type, tool, resource 등 다른 Semantic 오류에는 AST SourceSpan 위치가 아직 연결되지 않아 `PARTIAL`을 유지한다.
- `NSL-ERR-003`: Slice 0007에서 Symbol/Scope 오류까지 원래 Source Snippet 전파가 완료됐다. 다른 Semantic 오류 전체에는 Snippet 전파가 아직 연결되지 않아 `PARTIAL`을 유지한다.

두 요구사항은 Include와 Symbol/Scope 기능의 부분 완료만으로 전체 Compiler Diagnostic 계약이 완료됐다고 간주하지 않는다. 후속 Semantic Diagnostic Slice에서 모든 AST 기반 오류에 위치와 Snippet을 연결한 뒤 승격한다.

## 6. Acceptance

각 Requirement 구현 직후 `tools/run_quality.py`로 Traceability, 전체 Regression, Statement/Branch Coverage를 반복 검증했다.

- 16개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0006 할당 Requirement의 `PARTIAL` 0개
- Regression failure 0
- Statement Coverage 95% 이상
- Branch Coverage 90% 이상
- Quality Gate 통과 전 Commit/Push 금지
