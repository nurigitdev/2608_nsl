# NSL v0.1 NSO Trust Boundary and Semantic Identity

- **Slice:** `0014`
- **상태:** Accepted
- **작성일:** 2026-08-19
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`

## 1. 목적

이 Slice는 `.nso`를 비신뢰 입력으로 취급하는 load 경계와 Source provenance, 실행 의미를 구분하는 identity model을 고정한다. Runtime은 strict JSON과 exact schema, source manifest hash, semantic hash를 모두 검증한 immutable `SkillObject`만 받을 수 있다.

## 2. Trust Boundary

```text
untrusted .nso bytes
    -> bytes type and UTF-8 validation
    -> strict JSON load
       - malformed JSON reject
       - duplicate object key reject
       - NaN/Infinity reject
    -> exact NSO schema validation
    -> source hash header/build consistency
    -> canonical source manifest hash recomputation
    -> immutable typed IR construction
    -> semantic hash recomputation
    -> trusted runtime input
```

Schema 오류는 `NsoSchemaError`로, schema를 통과한 artifact의 hash 불일치는 `NsoIntegrityError`로 거부한다. 검증 실패 시 부분 IR이나 실행 가능한 객체를 반환하지 않는다.

## 3. Identity Model

| Identity | 입력 | 목적 | Include 분할 영향 |
|---|---|---|---|
| `source_bundle_sha256` | canonical `build.sources[]` manifest | 어떤 Source 조합을 Compile했는지 식별 | 받음 |
| `semantic_sha256` | build/hash를 제외한 canonical typed execution IR | Runtime 실행 의미 식별 | 받지 않음 |

Source manifest canonical item은 `logical_path`, Source content `sha256`, UTF-8 `size_bytes`, `is_root`를 포함한다. Top-level `hashes.source_bundle_sha256`과 `build.source_bundle_sha256`은 같아야 하며, loader가 manifest로 다시 계산한 값과도 같아야 한다.

동일 실행 의미를 단일 Source로 작성하거나 빈 include fragment를 추가해 분할한 경우 typed execution IR, Symbol/Node ID, `semantic_sha256`은 동일하다. Source manifest와 `source_bundle_sha256`, 최종 provenance 포함 NSO bytes는 다르다.

## 4. Security Boundary

두 SHA-256 값은 artifact 내부 일관성과 의미 식별을 위한 digest이며 발행자 인증 수단은 아니다. 공격자가 NSO와 두 hash를 함께 다시 계산하는 공격은 이 Slice만으로 막지 않는다. Production artifact의 출처, package path, signature 검증은 `NSL-SEC-010..011`이 할당된 Slice 0028의 책임이다.

Runtime은 signature 유무와 별개로 `.nso` schema와 두 hash를 항상 다시 검증해야 한다. Package signature 계층이 추가되더라도 이 load validation을 생략할 수 없다.

## 5. Requirement 결과

| Requirement | Status | Verification |
|---|---|---|
| `NSL-IR-015` | `IMPLEMENTED` | `TEST-IR-015` |
| `NSL-IR-016` | `IMPLEMENTED` | `TEST-IR-016` |
| `NSL-SEC-009` | `IMPLEMENTED` | `TEST-SEC-009` |
| `NSL-TST-017` | `IMPLEMENTED` | `TEST-TST-017` |

Slice 0014에 할당된 4개 Requirement는 모두 구현됐으며 남은 `PARTIAL`은 없다.

## 6. Test Strategy

- Boundary: top-level/build hash equality와 source manifest recomputation
- Robustness: bytes 이외 입력, invalid UTF-8, truncated JSON, duplicate key, `NaN`, positive/negative `Infinity`, invalid classification/currency/decimal wrapper
- Tamper: schema type 변조, source manifest 변조, semantic payload 변조
- Determinism: 동일 include Source Bundle 10회 Compile의 Symbol ID, 모든 Node ID, source/semantic hash, NSO bytes 비교
- Regression: 각 Requirement 완료 후 전체 `tools/run_quality.py` 실행

## 7. Slice 0003 PARTIAL 재평가

Artifact load 오류는 strict JSON/schema/hash 단계에서 안정적으로 구분된다. 그러나 `NSL-ERR-002`와 `NSL-ERR-003`은 `.ns` Source Error의 Line/Column과 Snippet 요구사항이다. Unsupported language/risk와 일부 EMIT schema/classification 오류에 AST SourceSpan 연결이 남아 있으므로 두 Requirement는 `PARTIAL`을 유지한다.

## 8. 품질 결과

구현 단계의 최종 코드 회귀 결과는 다음과 같다. Traceability 상태 반영 후 같은 품질 게이트를 다시 실행한다.

- Regression: `410 passed`
- Statement Coverage: `99.03%`
- Branch Coverage: `96.97%`
- `nsl/integrity.py`: `100%`
- `nsl/ir.py`: `100%`
- `nsl/ir_schema.py`: `100%`
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 9. Acceptance

- 4개 Requirement 모두 전용 Verification과 Repository Evidence를 가짐
- Slice 0014 할당 Requirement의 `PARTIAL` 0개
- 비신뢰 NSO가 strict parse, exact schema, provenance/semantic integrity를 모두 통과해야 load됨
- Source identity와 semantic identity가 분리됨
- Hash와 signature의 보안 책임이 구분됨
- Regression failure 0
- Quality Gate 통과 전 Commit/Push 금지
