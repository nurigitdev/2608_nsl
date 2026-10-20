# NSL v0.1 Immutable IR Schema

- **Slice:** `0013`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 `.nso`를 Source AST의 JSON 표현이 아니라 Schema Validation을 통과한 immutable Typed Execution IR로 고정한다. Runtime은 `.ns`, Lexer, Parser 또는 Compiler 없이 decode된 `SkillObject`를 직접 실행할 수 있다.

## 2. 내부 Part

| Part | 범위 | Requirement |
|---|---|---|
| A. Artifact Identity | Compile artifact와 Version/Identity | `NSL-IR-001..004` |
| B. Typed Execution Schema | Typed Node, Tool, Limit, Output, Bool | `NSL-IR-005..008`, `NSL-IR-013` |
| C. Composition Provenance | Include 비노출과 Source Manifest | `NSL-IR-012`, `NSL-IR-014` |
| D. Strict Immutable Load | 명시적 Schema Validation | `NSL-PY-004` |

## 3. Load 경계

```text
.nso bytes
    -> strict UTF-8 JSON load
       (duplicate key/non-finite number reject)
    -> validate_nso_document()
    -> frozen dataclass + tuple/frozenset model
    -> source manifest hash validation
    -> semantic hash validation
    -> Runtime preflight
```

`NsoCodec.decode()`는 Python IR 객체를 만들기 전에 UTF-8/JSON encoding과 top-level, Type, Symbol, Required Tool, Limit, Input, Context, Output, Statement, Expression, Analysis, Hash, Build Manifest를 검증한다. 필드 누락, 추가 필드, scalar type 오류, duplicate key, non-finite number, unknown kind와 mutable literal은 `NsoSchemaError`로 거부한다. 이 strict load 경계는 Slice 0014에서 강화됐다.

오류는 다음 형태로 JSON path를 보존한다.

```text
invalid NSO schema at $.output[0]: missing fields: ['type']
```

## 4. Immutable Model

IR Model은 `@dataclass(frozen=True, slots=True)`와 immutable collection을 사용한다.

- Body, argument, field, source manifest: `tuple`
- Feature set: `frozenset`
- Literal: Bool, Int, String, Decimal, Money와 같은 immutable value만 허용
- Unknown Statement/Expression/Type kind: load 거부

JSON dictionary는 Schema Validation과 model conversion 이후 Runtime에서 직접 탐색하지 않는다.

## 5. Typed Execution Schema

`.nso`에는 다음 실행 계약이 명시된다.

- Language name/version과 Skill ID/version/risk
- 모든 Expression의 `node_id`, `kind`, 구조화된 `type`
- Canonical Required Tool fingerprint
- 네 Resource Limit
- Input/Context/Output Schema와 Classification
- Static Resource Analysis
- Semantic Hash

Source `true`와 `false`는 JSON native Boolean 값과 `{kind: primitive, name: Bool}` Type을 가진 `literal` Expression으로 정규화된다.

## 6. Build Provenance

Source composition 정보는 실행 Node가 아니라 non-semantic `build` metadata로 저장한다.

```json
{
  "build": {
    "source_bundle_sha256": "sha256:...",
    "root_source": "skills/project_budget_check.ns",
    "sources": [
      {
        "logical_path": "skills/project_budget_check.ns",
        "sha256": "sha256:...",
        "size_bytes": 1,
        "is_root": true
      }
    ]
  }
}
```

각 Source는 logical path, content hash, UTF-8 byte size, root 여부를 가진다. `include` keyword와 Include AST Node는 `.nso` 실행 영역에 존재하지 않는다. Build Metadata는 `.nso`에는 포함되지만 Semantic Hash 계산에서는 제외된다.

## 7. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-IR-001` | `IMPLEMENTED` | `TEST-IR-001` |
| `NSL-IR-002` | `IMPLEMENTED` | `TEST-IR-002` |
| `NSL-IR-003` | `IMPLEMENTED` | `TEST-IR-003` |
| `NSL-IR-004` | `IMPLEMENTED` | `TEST-IR-004` |
| `NSL-IR-005` | `IMPLEMENTED` | `TEST-IR-005` |
| `NSL-IR-006` | `IMPLEMENTED` | `TEST-IR-006` |
| `NSL-IR-007` | `IMPLEMENTED` | `TEST-IR-007` |
| `NSL-IR-008` | `IMPLEMENTED` | `TEST-IR-008` |
| `NSL-IR-012` | `IMPLEMENTED` | `TEST-IR-012` |
| `NSL-IR-013` | `IMPLEMENTED` | `TEST-IR-013` |
| `NSL-IR-014` | `IMPLEMENTED` | `TEST-IR-014` |
| `NSL-PY-004` | `IMPLEMENTED` | `TEST-PY-004` |

Slice 0013에 할당된 12개 Requirement는 모두 구현됐으며 남은 `PARTIAL`은 없다.

## 8. Slice 0003 PARTIAL 재평가

Artifact Schema 오류의 JSON path는 Slice 0013에서 완성됐다. Unsupported language/risk와 일부 EMIT schema/classification Source 오류에는 SourceSpan 연결이 남아 있으므로 `NSL-ERR-002`와 `NSL-ERR-003`은 `PARTIAL`을 유지한다.

## 9. 품질 결과

공통 기반과 각 Requirement 구현 후 `tools/run_quality.py`로 Traceability, 전체 pytest Regression, Statement/Branch Coverage를 반복 실행했다. 구현 완료 시점의 결과는 다음과 같다.

- Regression: `390 passed`
- Statement Coverage: `99.01%`
- Branch Coverage: `96.92%`
- `nsl/ir.py`: `100%`
- `nsl/ir_schema.py`: `100%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 10. Acceptance

- 12개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0013 할당 Requirement의 `PARTIAL` 0개
- `.nso`가 Source frontend 없이 실행됨
- Typed execution tree와 Build provenance가 분리됨
- Schema Validation 이전에 Python IR 객체를 생성하지 않음
- Immutable model과 collection을 사용함
- Regression failure 0
- Quality Gate 통과 전 Commit/Push 금지
