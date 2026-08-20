# NSL v0.1 NSP Format and Builder

- **Slice:** `0027`
- **상태:** Accepted
- **작성일:** 2026-08-20
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 5 Implemented, 0 Partial

## 1. 목적

이 Slice는 검증된 `.nso`를 하나의 결정적인 `.nsp` 배포 Package로 구성하는 형식과 Builder를 정의한다. Builder는 기존 `NsoCodec` Trust Boundary를 재사용하며, Runtime이나 Compiler에 파일시스템 및 archive 책임을 추가하지 않는다.

## 2. Requirement 결과

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-PKG-001` | 결정적 `.nsp` Package 생성 | `IMPLEMENTED` |
| `NSL-PKG-002` | 최소 1개 `.nso` 포함 | `IMPLEMENTED` |
| `NSL-PKG-003` | Canonical Package Manifest | `IMPLEMENTED` |
| `NSL-PKG-004` | Manifest Skill ID/Version | `IMPLEMENTED` |
| `NSL-PKG-005` | 결정적 Package Hash | `IMPLEMENTED` |

Slice 0027에 할당된 Requirement는 모두 완료되어 Slice 내부 `PARTIAL`은 없다. 기존 `NSL-AUD-007`의 `PARTIAL`은 변경하지 않는다.

## 3. Architecture Boundary

```text
canonical .nso bytes
    -> NsoCodec.decode()          untrusted artifact validation
    -> NsoCodec.encode()          canonical normalization
    -> NspBuilder                 stable identity ordering
        -> NspManifest            canonical logical content
        -> ZIP_STORED archive     deterministic container bytes
```

`NspBuilder`는 archive bytes와 immutable `NspManifest`를 반환한다. 파일 경로 선택과 실제 write는 CLI 또는 배포 Adapter 책임으로 남겨 Core Builder의 임의 filesystem 접근을 피한다.

## 4. NSP Container

v0.1 NSP는 표준 ZIP container이며 모든 member는 `ZIP_STORED`, 고정 timestamp `1980-01-01T00:00:00`, 고정 regular-file mode를 사용한다. Member 순서는 `manifest.json` 다음 Skill identity 순으로 정렬된 `skills/NNNN.nso`다.

```text
package.nsp
├─ manifest.json
├─ skills/0001.nso
└─ skills/0002.nso
```

호출자가 archive member path를 제공하지 않으며 Builder가 안전한 상대 경로를 생성한다. 외부에서 받은 `.nsp`의 path traversal, duplicate member, size 제한 및 extraction 검증은 Slice 0028 verifier 범위다.

## 5. Canonical Manifest

Manifest는 UTF-8 canonical JSON으로 저장되며 key 정렬과 compact separator를 사용한다.

```json
{
  "format": "NSP",
  "schema_version": "1.0",
  "package_sha256": "sha256:<digest>",
  "skills": [{
    "path": "skills/0001.nso",
    "skill_id": "FINANCE.PROJECT_BUDGET_CHECK",
    "skill_version": "1.0.0",
    "semantic_sha256": "sha256:<digest>",
    "artifact_sha256": "sha256:<digest>",
    "size_bytes": 1234
  }]
}
```

Skill ID/Version과 semantic hash는 strict NSO decode 결과에서만 가져온다. 동일 Skill ID/Version이 둘 이상이면 Package identity가 모호하므로 Builder가 거부한다.

## 6. Package Hash

`package_sha256`은 자기 자신을 제외한 canonical manifest content에 ASCII `NSP-CONTENT-V1`과 NUL byte (`0x00`)로 구성된 domain separator를 붙여 계산한다. 각 Skill entry의 `artifact_sha256`과 `size_bytes`가 canonical `.nso` bytes를 결속하므로 모든 논리 Package content가 hash에 포함된다.

이 hash는 손상 탐지와 향후 서명 대상 identity를 제공하지만 발행자 인증은 아니다. Signature metadata, unsigned development policy, production signature-required 정책은 `NSL-PKG-006..008`과 Slice 0028의 범위다.

## 7. Boundary and Robustness

- Artifact input: bytes sequence가 아닌 값, non-bytes member, empty/malformed/tampered NSO 거부
- Cardinality: 0개 거부, 1개 허용, 복수 Skill 모두 포함
- Identity: 입력 순서와 무관한 ID/Version 정렬, 중복 identity 거부
- Canonicalization: whitespace가 다른 유효 NSO도 canonical bytes로 정규화
- Determinism: manifest key/member order, timestamp, mode, compression 고정
- Integrity: semantic hash, artifact hash, size와 package hash 연결
- Separation: 외부 Package verification과 signature policy 미구현

## 8. Traceability and Quality

Slice 반영 후 전체 Baseline은 `IMPLEMENTED 298`, `PARTIAL 1`, `PLANNED 26`이다.

- Regression: `867 passed`
- Statement Coverage: `99.42%` (시작값 `99.41%`)
- Branch Coverage: `98.32%` (시작값 `98.31%`)
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 9. Acceptance

- 5개 할당 Requirement에 개별 Verification과 Evidence 연결
- strict NSO Trust Boundary 재사용과 canonical artifact normalization
- 최소 1개 Skill 및 duplicate identity fail-closed 보장
- canonical manifest와 결정적 ZIP archive 생성
- 논리 Package content hash 생성 및 manifest 저장
- Slice 0028 signature/verifier 경계 유지
- 요구사항별 전체 Regression과 Coverage 현재값 유지
