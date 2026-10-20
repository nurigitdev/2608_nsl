from __future__ import annotations

from dataclasses import FrozenInstanceError, fields, replace
from datetime import UTC, datetime, timedelta, timezone
from hashlib import sha256

import pytest

from nsl.adapters.ed25519 import Ed25519PackageSigner, Ed25519TrustStore
from nsl.compiler import NslCompiler
from nsl.ir import NsoCodec, SkillObject
from nsl.nsp import NspBuilder
from nsl.nsp_verification import NspVerificationPolicy, NspVerifier
from nsl.security import RuntimeEnvironment
from nsl.trust import (
    PackageTrustError,
    PackageTrustEvidence,
    TrustStatus,
    VerifiedSkillArtifact,
    package_id_for,
)
from nsl.vertical_slice import build_tool_catalog


NOW = datetime(2026, 10, 20, 6, 0, tzinfo=UTC)
SOURCE = '''
language NSL "0.1";
skill FINANCE.TRUST_TEST {
  version "1.0.0";
  risk READ_VALIDATE;
  limits { tool_calls 1; loop_iterations 1; emitted_rows 1; collection_size 1; }
  output { ok: Bool classification INTERNAL; }
  emit { ok: true; }
}
'''


def _skill() -> SkillObject:
    return NslCompiler(build_tool_catalog()).compile(SOURCE).skill


def _verified_package(*skills: SkillObject):
    signer = Ed25519PackageSigner("publisher.key-0055", bytes(range(1, 33)))
    package = NspBuilder().build(
        tuple(NsoCodec.encode(skill) for skill in skills or (_skill(),)),
        signer=signer,
    )
    verifier = NspVerifier(
        policy=NspVerificationPolicy(RuntimeEnvironment.PRODUCTION),
        signature_verifier=Ed25519TrustStore(
            {signer.key_id: signer.public_key_bytes}
        ),
    )
    return verifier.verify(package.data)


def _evidence() -> PackageTrustEvidence:
    package = _verified_package()
    return package.trust_evidence(
        publisher_id="publisher.finance",
        trust_policy_id="trust-policy-0055",
        verified_at=NOW,
    )


def test_sig_002_003_verified_signed_package_issues_bound_trust_evidence() -> None:
    package = _verified_package()

    evidence = package.trust_evidence(
        publisher_id="publisher.finance",
        trust_policy_id="trust-policy-0055",
        verified_at=NOW,
    )

    assert evidence.package_id == "nsp:" + package.package_hash
    assert evidence.package_hash == package.package_hash
    assert evidence.publisher_id == "publisher.finance"
    assert evidence.signing_key_id == "publisher.key-0055"
    assert evidence.verified_at == NOW
    assert evidence.trust_status is TrustStatus.VERIFIED
    assert evidence.trust_policy_id == "trust-policy-0055"


def test_sig_004_trusted_artifact_binds_package_artifact_and_semantic_hashes() -> None:
    package = _verified_package()

    artifacts = package.trusted_artifacts(
        publisher_id="publisher.finance",
        trust_policy_id="trust-policy-0055",
        verified_at=NOW,
    )

    assert len(artifacts) == 1
    artifact = artifacts[0]
    manifest = package.skills[0].manifest
    assert artifact.skill is package.skills[0].skill
    assert artifact.artifact_hash == manifest.artifact_sha256
    assert artifact.semantic_hash == manifest.semantic_sha256
    assert artifact.trust == package.trust_evidence(
        publisher_id="publisher.finance",
        trust_policy_id="trust-policy-0055",
        verified_at=NOW,
    )


def test_sig_004_integrity_change_invalidates_prior_evidence_binding() -> None:
    package = _verified_package()
    artifact = package.trusted_artifacts(
        publisher_id="publisher.finance",
        trust_policy_id="trust-policy-0055",
        verified_at=NOW,
    )[0]

    with pytest.raises(PackageTrustError, match="artifact_hash"):
        replace(artifact, artifact_hash="sha256:" + "0" * 64)
    with pytest.raises(PackageTrustError, match="package_id"):
        replace(artifact.trust, package_hash="sha256:" + "f" * 64)

    changed_skill = replace(
        artifact.skill,
        skill_version="1.0.1",
        semantic_hash="",
    ).with_computed_hash()
    changed_package = _verified_package(changed_skill)
    changed_artifact = changed_package.trusted_artifacts(
        publisher_id="publisher.finance",
        trust_policy_id="trust-policy-0055",
        verified_at=NOW,
    )[0]
    assert changed_artifact.trust != artifact.trust
    assert changed_artifact.artifact_hash != artifact.artifact_hash
    assert changed_artifact.semantic_hash != artifact.semantic_hash


def test_ir_209_trust_is_package_evidence_not_an_ir_signature_path() -> None:
    skill_fields = {field.name for field in fields(SkillObject)}
    trust_fields = {field.name for field in fields(PackageTrustEvidence)}

    assert "signature" not in skill_fields
    assert "signing_key_id" not in skill_fields
    assert trust_fields == {
        "package_id",
        "package_hash",
        "publisher_id",
        "signing_key_id",
        "verified_at",
        "trust_status",
        "trust_policy_id",
    }


def test_sig_006_unsigned_v0_1_development_package_remains_compatible() -> None:
    skill = _skill()
    package = NspBuilder().build((NsoCodec.encode(skill),))
    verified = NspVerifier(
        policy=NspVerificationPolicy(
            RuntimeEnvironment.DEVELOPMENT,
            allow_unsigned_development=True,
        )
    ).verify(package.data)

    assert verified.signature is None
    assert verified.skills[0].skill == skill
    legacy_artifact = VerifiedSkillArtifact(
        skill,
        "sha256:" + sha256(NsoCodec.encode(skill)).hexdigest(),
        None,
    )
    assert legacy_artifact.trust is None
    with pytest.raises(PackageTrustError, match="unsigned"):
        verified.trust_evidence(
            publisher_id="publisher.finance",
            trust_policy_id="trust-policy-0055",
            verified_at=NOW,
        )


def test_signed_package_hash_mismatch_cannot_issue_trust_evidence() -> None:
    package = _verified_package()
    assert package.signature is not None
    forged = replace(
        package,
        signature=replace(
            package.signature,
            package_sha256="sha256:" + "0" * 64,
        ),
    )

    with pytest.raises(PackageTrustError, match="signature package hash"):
        forged.trust_evidence(
            publisher_id="publisher.finance",
            trust_policy_id="trust-policy-0055",
            verified_at=NOW,
        )


@pytest.mark.parametrize(
    ("mutation", "error", "message"),
    [
        ({"package_id": ""}, PackageTrustError, "package_id"),
        ({"package_id": "nsp:sha256:" + "f" * 64}, PackageTrustError, "package_id"),
        ({"package_hash": "bad"}, PackageTrustError, "package_hash"),
        ({"publisher_id": ""}, PackageTrustError, "publisher_id"),
        ({"signing_key_id": ""}, PackageTrustError, "signing_key_id"),
        ({"trust_policy_id": ""}, PackageTrustError, "trust_policy_id"),
        ({"verified_at": datetime(2026, 10, 20)}, PackageTrustError, "UTC"),
        (
            {"verified_at": NOW.astimezone(timezone(timedelta(hours=9)))},
            PackageTrustError,
            "UTC",
        ),
        ({"verified_at": "now"}, TypeError, "datetime"),
        ({"trust_status": "VERIFIED"}, TypeError, "TrustStatus"),
    ],
)
def test_package_trust_evidence_rejects_malformed_boundaries(
    mutation, error, message
) -> None:
    with pytest.raises(error, match=message):
        replace(_evidence(), **mutation)


@pytest.mark.parametrize(
    "field",
    ["publisher_id", "signing_key_id", "trust_policy_id"],
)
def test_package_trust_evidence_rejects_credential_material(field) -> None:
    with pytest.raises(ValueError, match="credential"):
        replace(_evidence(), **{field: "api_key=secret"})


def test_package_id_is_deterministic_and_rejects_invalid_hashes() -> None:
    package_hash = "sha256:" + "a" * 64
    assert package_id_for(package_hash) == "nsp:" + package_hash
    assert package_id_for(package_hash) == package_id_for(package_hash)
    with pytest.raises(PackageTrustError, match="SHA-256"):
        package_id_for("sha256:" + "a" * 63)


def test_trust_models_are_immutable_and_reject_invalid_artifacts() -> None:
    package = _verified_package()
    artifact = package.trusted_artifacts(
        publisher_id="publisher.finance",
        trust_policy_id="trust-policy-0055",
        verified_at=NOW,
    )[0]

    with pytest.raises(FrozenInstanceError):
        artifact.trust.package_id = "changed"  # type: ignore[misc,union-attr]
    with pytest.raises(TypeError, match="SkillObject"):
        VerifiedSkillArtifact(object(), artifact.artifact_hash, artifact.trust)  # type: ignore[arg-type]
    with pytest.raises(TypeError, match="PackageTrustEvidence"):
        replace(artifact, trust=object())
    with pytest.raises(PackageTrustError, match="artifact_hash"):
        replace(
            artifact,
            skill=replace(artifact.skill, semantic_hash="sha256:" + "0" * 64),
        )
    with pytest.raises(PackageTrustError, match="valid canonical"):
        replace(
            artifact,
            skill=replace(artifact.skill, semantic_hash="bad"),
        )


def test_multiple_verified_skills_share_exact_package_trust_identity() -> None:
    first = _skill()
    second = replace(
        first,
        skill_id="FINANCE.TRUST_TEST_SECOND",
        semantic_hash="",
    ).with_computed_hash()
    package = _verified_package(first, second)

    artifacts = package.trusted_artifacts(
        publisher_id="publisher.finance",
        trust_policy_id="trust-policy-0055",
        verified_at=NOW,
    )

    assert len(artifacts) == 2
    assert artifacts[0].trust is artifacts[1].trust
    assert artifacts[0].skill.skill_id != artifacts[1].skill.skill_id
