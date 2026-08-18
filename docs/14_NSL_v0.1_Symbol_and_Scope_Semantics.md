# NSL v0.1 Symbol and Scope Semantics

- **Slice:** `0007`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 Source Identifier를 선언 순서와 Scope에 따라 결정적으로 Symbol ID로 해석하는 계약을 고정한다. Input, Context, Variable, Iterator, Check를 명시적인 Symbol Table로 관리하고, use-before-declaration, block escape, duplicate, shadowing을 Compiler 단계에서 차단한다.

## 2. Architecture 결정

```text
Composed AST + SourceFile Set
  -> SymbolTable(scope0001: SKILL)
       declare Input / Context / Variable
       enter scopeNNNN: FOREACH
       declare Iterator / local Variable / Check
       resolve active declarations
       leave foreach scope
  -> SymbolBinding
  -> IR SymbolSpec / SymbolRefExpr
```

1. `symbols.py`는 `core`, `source`, `diagnostics`에만 의존하며 IR, Compiler, Runtime에 의존하지 않는다.
2. Symbol Namespace는 Source value와 CHECK result를 구분한다. Source 표현식의 이름 충돌을 피하기 위해 활성 Scope 전체에서 동일 이름 선언을 금지한다.
3. Root Skill은 `scope0001`이며 foreach 진입 순서대로 결정적인 Scope ID를 부여한다.
4. Symbol ID는 합성된 AST의 선언 순서대로 `s0001`, `s0002`, ... 형식으로 부여한다.
5. 같은 Scope의 중복 선언과 활성 ancestor 이름의 shadowing을 `NSL-E2002`로 거부한다. 종료된 sibling Scope에서는 동일 이름을 다시 선언할 수 있다.
6. foreach iterator와 block local은 해당 frame에서만 해석되며, 정상 종료와 오류 종료 모두 stack 순서로 Scope를 제거한다.
7. `let` 재할당 문법은 존재하지 않으며 AST, Symbol Binding, IR Statement는 immutable value object이다.
8. CHECK result Symbol은 condition 검증 후 선언하므로 CHECK 이전에는 보이지 않고 이후 같은 Scope에서 참조할 수 있다.
9. Symbol 오류는 AST SourceSpan과 SourceFile Set을 이용해 원래 Line/Column, Snippet, Logical Path를 보존한다.

## 3. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-SEM-001` | `IMPLEMENTED` | `TEST-SEM-001` |
| `NSL-SEM-002` | `IMPLEMENTED` | `TEST-SEM-002` |
| `NSL-SEM-003` | `IMPLEMENTED` | `TEST-SEM-003` |
| `NSL-SEM-004` | `IMPLEMENTED` | `TEST-SEM-004` |
| `NSL-SEM-005` | `IMPLEMENTED` | `TEST-SEM-005` |
| `NSL-SEM-006` | `IMPLEMENTED` | `TEST-SEM-006` |
| `NSL-SEM-007` | `IMPLEMENTED` | `TEST-SEM-007` |
| `NSL-SEM-008` | `IMPLEMENTED` | `TEST-SEM-008` |

Slice 0007에 할당된 8개 Requirement는 모두 구현되었으며 할당 범위 안에 남은 `PARTIAL`은 없다. 전체 Baseline 상태는 `IMPLEMENTED 123`, `PARTIAL 157`, `PLANNED 45`이다.

## 4. Boundary와 Robustness 검증

- Identifier는 선언 직전에는 실패하고 선언 직후에는 성공한다.
- foreach 종료 직전의 iterator 참조는 성공하고 종료 직후에는 실패한다.
- 같은 Scope의 두 번째 `let`은 실패하고, 두 sibling foreach의 동일 이름 선언은 각각 고유 Symbol ID로 성공한다.
- Root value, block local, nested iterator에 대한 shadowing을 모두 거부한다.
- CHECK result는 CHECK condition 안에서는 실패하고 뒤따르는 EMIT에서는 동일 result Symbol ID로 해석된다.
- Root Scope 이탈, Scope stack 역순 이탈, Source 정보 없는 Diagnostic fallback을 검증한다.

## 5. Slice 0003 PARTIAL 재평가

- `NSL-ERR-002`: Symbol/Scope Semantic 오류에 Line/Column과 Logical Path가 추가됐다. type, tool, resource 등 나머지 Semantic 오류의 위치 연결이 남아 `PARTIAL`을 유지한다.
- `NSL-ERR-003`: Symbol/Scope Semantic 오류에 Source Snippet이 추가됐다. 나머지 Semantic 오류의 Snippet 연결이 남아 `PARTIAL`을 유지한다.

두 요구사항은 Compiler의 모든 Source 오류가 동일한 위치 계약을 만족한 뒤에만 `IMPLEMENTED`로 승격한다.

## 6. Acceptance

구조 리팩터링 전후와 각 Requirement 구현 직후 `tools/run_quality.py`로 Traceability, 전체 Regression, Statement/Branch Coverage를 검증했다.

- 8개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0007 할당 Requirement의 `PARTIAL` 0개
- Regression failure 0
- Statement Coverage가 Slice 0006 최종값 이상
- Branch Coverage가 Slice 0006 최종값 이상
- Quality Gate 통과 전 Commit/Push 금지
