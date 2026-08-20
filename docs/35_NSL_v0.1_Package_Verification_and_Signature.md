# NSL v0.1 Package Verification and Signature

- **Slice:** `0028`
- **상태:** Accepted
- **작성일:** 2026-08-20
- **Requirement Baseline:** `requirements/nsl_v0_1_traceability.json`
- **완료 범위:** 6 Implemented, 0 Partial

## 1. 목적

이 Slice는 `.nsp`를 비신뢰 배포 입력으로 처리하는 verification boundary와 발행자 신뢰를 검증하는 signature 계층을 구현한다. Slice 0027의 canonical package hash를 유지하면서 path traversal, archive/resource abuse, manifest와 NSO tamper, unsigned downgrade 및 untrusted signer를 실행 전에 차단한다.

## 2. Part와 Requirement 결과

### Part A: Package Verification

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-SEC-010` | NSP path traversal 및 archive abuse 방지 | `IMPLEMENTED` |
| `NSL-TST-012` | Package tamper test matrix | `IMPLEMENTED` |

### Part B: Signature and Policy

| Requirement | 범위 | 결과 |
|---|---|---|
| `NSL-PKG-006` | Canonical signature metadata | `IMPLEMENTED` |
| `NSL-PKG-007` | 명시적 Development unsigned 허용 | `IMPLEMENTED` |
| `NSL-PKG-008` | Production signature-required 정책 | `IMPLEMENTED` |
| `NSL-SEC-011` | Production Ed25519 signature 검증 | `IMPLEMENTED` |

Slice 0028에 할당된 Requirement는 모두 완료되어 Slice 내부 `PARTIAL`은 없다. 기존 `NSL-AUD-007`의 `PARTIAL`은 변경하지 않는다.

## 3. Architecture Boundary

```text
untrusted .nsp bytes
    -> NspVerifier
        -> ZIP central directory and resource limits
        -> strict canonical manifest/signature JSON
        -> package/member/NSO identity and hash validation
        -> NspVerificationPolicy
        -> NspSignatureVerifier port
            -> Ed25519TrustStore             nsl.adapters.ed25519
                -> cryptography              vetted crypto dependency
    -> VerifiedNspPackage
```

Core Builder와 Verifier는 `cryptography`를 import하지 않는다. 실제 Ed25519 private/public key 처리는 `nsl.adapters.ed25519`에만 존재하며 AST architecture test가 이 의존 방향을 고정한다. KMS 또는 원격 signing service도 `NspSigner`와 `NspSignatureVerifier` port를 구현해 Core 변경 없이 교체할 수 있다.

## 4. Safe Package Verification

Verifier는 archive를 filesystem에 추출하지 않는다. ZIP central directory를 먼저 검사하고 허용된 member만 메모리에서 읽는다.

검사 순서:

1. Package byte와 member count 제한
2. 원본 `orig_filename`과 정규화된 `filename` 비교
3. absolute path, drive/colon, backslash, NUL, empty/dot/dot-dot segment, non-ASCII path 거부
4. directory, symlink/non-regular type, encrypted member, compression 거부
5. member 및 전체 uncompressed byte 제한
6. strict canonical `manifest.json`과 exact schema
7. manifest와 실제 member set 일치
8. NSO size, artifact hash, canonical encoding, schema, Skill ID/Version/semantic hash 일치
9. logical package hash 재계산
10. environment policy와 signature 검증

v1.0은 Slice 0027 Builder와 동일하게 `ZIP_STORED`만 허용한다. 따라서 compressed/decompression bomb surface를 제거하며 기본 한도는 Package 64 MiB, member 1,024개, member당 16 MiB, 전체 uncompressed 64 MiB다.

## 5. Signature Metadata

서명은 Package의 마지막 member인 canonical `signature.json`에 저장한다.

```json
{
  "format": "NSP-SIGNATURE",
  "schema_version": "1.0",
  "algorithm": "Ed25519",
  "key_id": "publisher.production-1",
  "package_sha256": "sha256:<digest>",
  "signature": "<unpadded-base64url>"
}
```

Signature는 64-byte Ed25519 signature를 unpadded base64url로 표현한다. 서명 payload는 domain separator `NSP-SIGNATURE-V1` + NUL byte 뒤에 `algorithm`, `key_id`, `package_sha256`의 canonical JSON을 연결한다. 따라서 Package content뿐 아니라 algorithm과 trusted key identity도 함께 결속된다.

`signature.json`은 logical `package_sha256` 계산에서 제외되는 detached metadata다. 같은 content의 signed/unsigned Package는 같은 package identity를 갖지만 archive bytes는 다르다.

## 6. Environment Policy

| Environment | Policy | Unsigned | Signed without verifier | Verified signature |
|---|---|---:|---:|---:|
| `DEVELOPMENT` | `allow_unsigned_development=true` | 허용 | 거부 | 허용 |
| `DEVELOPMENT` | `allow_unsigned_development=false` | 거부 | 거부 | 허용 |
| `PRODUCTION` | 고정 | 거부 | 거부 | 허용 |

환경과 unsigned 정책은 필수로 명시한다. Production에서 unsigned 허용을 설정하려는 policy 자체를 생성할 수 없으며, signed metadata가 있더라도 verifier port가 없거나 `True` 이외의 값을 반환하면 거부한다.

## 7. Ed25519 Trust Store

`Ed25519PackageSigner`는 32-byte raw private key로 deterministic signature를 생성한다. `Ed25519TrustStore`는 사전에 등록된 `key_id -> 32-byte raw public key`만 신뢰하며 unknown key, wrong key, algorithm mismatch, malformed/modified signature를 모두 거부한다.

암호 연산은 직접 구현하지 않고 공식 `cryptography 49.0.0`을 사용한다. 공식 문서가 설명하는 `Ed25519PrivateKey.sign()`과 `Ed25519PublicKey.verify()` 계약을 적용하며 invalid signature는 예외를 외부에 노출하지 않고 fail-closed 결과로 정규화한다.

- Ed25519 API: https://cryptography.io/en/49.0.0/hazmat/primitives/asymmetric/ed25519/
- Installation: https://cryptography.io/en/latest/installation/

Production private key는 Source, NSO, NSP, Audit에 저장하지 않는다. 배포 환경은 KMS/Vault/remote signer port를 사용해야 하며 raw-key signer는 deterministic local integration과 adapter contract를 제공한다.

## 8. Tamper and Boundary Coverage

- Archive: malformed/truncated ZIP, duplicate/extra/missing member, directory/symlink, compression/encryption
- Path: root/drive/UNC 성격, backslash normalization, dot traversal, NUL, Unicode, empty segment
- Resource: 0/1/exact/exceeded Package, member count, member size, total size
- Manifest: malformed/duplicate/non-finite JSON, missing/extra/type/value field, noncanonical encoding
- Artifact: size/hash/schema/canonical NSO/Skill identity/package hash mismatch
- Signature: metadata schema, base64url length, package hash mismatch, signer failure
- Policy: Development opt-in, Production required, verifier missing/false/non-bool/exception
- Crypto: valid, unknown key, wrong key, modified signature, key ID substitution, unsupported platform

## 9. Traceability and Quality

Slice 반영 후 전체 Baseline은 `IMPLEMENTED 304`, `PARTIAL 1`, `PLANNED 20`이다.

- Regression: `944 passed`
- Statement Coverage: `99.45%` (시작값 `99.42%`)
- Branch Coverage: `98.42%` (시작값 `98.32%`)
- 요구 기준: Statement `>= 95%`, Branch `>= 90%`

## 10. Acceptance

- 6개 할당 Requirement에 개별 Verification과 Evidence 연결
- extraction 없는 strict NSP trust boundary
- traversal, normalization collision, archive/resource abuse fail-closed
- canonical signature metadata와 protected signature payload
- Development explicit opt-in 및 Production signature-required 정책
- trusted key ID 기반 실제 Ed25519 verification
- crypto dependency의 Adapter 격리
- Package tamper 및 worst-case matrix
- 요구사항별 전체 Regression과 Coverage 현재값 유지
