use std::error::Error;

use nsl_core::{DecimalError, PayloadHash};
use nsl_ir::{
    BuildSource, NsoCodec, NsoCodecError, NsoLimits, StatementKind, artifact_sha256,
    source_manifest_sha256,
};

use super::{
    codec::{LIMITS, READ, SCALARS, WRITE},
    ir_1_0, ir_1_1,
};

fn expected(label: &str) -> Vec<&'static str> {
    include_str!("../fixtures/hash-oracle.tsv")
        .lines()
        .filter_map(|row| row.split_once('\t'))
        .find_map(|(name, values)| (name == label).then_some(values))
        .unwrap()
        .split('\t')
        .collect()
}

#[test]
fn all_hashes_match_the_frozen_oracle_for_both_ir_versions() {
    for (version, bytes, model) in [("1.0", READ, ir_1_0()), ("1.1", WRITE, ir_1_1())] {
        let digests = expected(version);
        let manifest = source_manifest_sha256(&model.build.sources).unwrap();
        let bundle = model.build.compute_source_bundle_sha256().unwrap();
        let semantic = model.compute_semantic_sha256(1024).unwrap();
        let encoded = NsoCodec::encode(&model, 1024).unwrap();
        assert_eq!(encoded, bytes);
        assert_eq!(manifest.as_str(), digests[0]);
        assert_eq!(bundle, model.build.source_bundle_sha256);
        assert_eq!(bundle, manifest);
        assert_eq!(semantic.as_str(), digests[1]);
        assert_eq!(semantic, model.semantic_hash);
        assert_eq!(artifact_sha256(&encoded).as_str(), digests[2]);
        assert_eq!(NsoCodec::decode(&encoded, LIMITS).unwrap(), model);
    }
}

#[test]
fn ordered_manifest_uses_canonical_utf8_and_semantic_identity_excludes_build() {
    let mut model = ir_1_0();
    model.build.sources.push(BuildSource {
        logical_path: "fragment/예산 \"α\".ns".into(),
        content_hash: PayloadHash::from_digest([7; 32]),
        size_bytes: "18446744073709551616".parse().unwrap(),
        is_root: false,
    });
    let ordered = source_manifest_sha256(&model.build.sources).unwrap();
    assert_eq!(ordered.as_str(), expected("ordered")[0]);

    let mut reversed = model.build.sources.clone();
    reversed.reverse();
    assert_eq!(
        source_manifest_sha256(&reversed).unwrap().as_str(),
        expected("reversed")[0]
    );
    assert_eq!(
        source_manifest_sha256(&[]).unwrap().as_str(),
        expected("empty")[0]
    );

    model.build.source_bundle_sha256 = ordered;
    assert_eq!(
        model.compute_semantic_sha256(1024).unwrap(),
        model.semantic_hash
    );
    let encoded = NsoCodec::encode(&model, 1024).unwrap();
    assert_eq!(artifact_sha256(&encoded).as_str(), expected("rebuilt")[0]);
    assert_eq!(NsoCodec::decode(&encoded, LIMITS).unwrap(), model);

    let declared = model.semantic_hash.clone();
    model.semantic_hash = PayloadHash::from_digest([0; 32]);
    assert_eq!(model.compute_semantic_sha256(1024).unwrap(), declared);
    model.build.sources[1].size_bytes = "1".repeat(4301).parse().unwrap();
    assert_eq!(
        model.build.compute_source_bundle_sha256(),
        Err(NsoCodecError::IntegerTooWide)
    );
    assert_eq!(model.compute_semantic_sha256(1024).unwrap(), declared);
}

#[test]
fn decode_distinguishes_a_bad_manifest_from_a_bad_semantic_digest() {
    for model in [ir_1_0(), ir_1_1()] {
        let mut bad_bundle = model.clone();
        bad_bundle.build.source_bundle_sha256 = PayloadHash::from_digest([0; 32]);
        let bytes = NsoCodec::encode(&bad_bundle, 1024).unwrap();
        let error = NsoCodec::decode(&bytes, LIMITS).unwrap_err();
        assert_eq!(error, NsoCodecError::SourceManifestMismatch);
        assert_eq!(error.to_string(), "source bundle manifest hash mismatch");
        assert!(error.source().is_none());

        let mut changed_manifest = model.clone();
        changed_manifest.build.sources[0].size_bytes = 0.into();
        assert_eq!(
            NsoCodec::decode(&NsoCodec::encode(&changed_manifest, 1024).unwrap(), LIMITS),
            Err(NsoCodecError::SourceManifestMismatch),
        );

        let mut changed_skill = model;
        changed_skill.skill_id.push_str(".CHANGED");
        let bytes = NsoCodec::encode(&changed_skill, 1024).unwrap();
        let error = NsoCodec::decode(&bytes, LIMITS).unwrap_err();
        assert_eq!(error, NsoCodecError::SemanticHashMismatch);
        assert_eq!(error.to_string(), "semantic hash mismatch");
    }
}

#[test]
fn semantic_rehash_normalizes_literals_within_the_aggregate_expansion_budget() {
    let canonical = std::str::from_utf8(SCALARS).unwrap();
    let alternate_text = canonical.replace(r#""-0.00""#, r#""-0E-2""#);
    let decoded = NsoCodec::decode(alternate_text.as_bytes(), LIMITS).unwrap();
    let canonical_bytes = NsoCodec::encode(&decoded, 1024).unwrap();
    assert_eq!(canonical_bytes, SCALARS);
    assert_ne!(
        artifact_sha256(alternate_text.as_bytes()),
        artifact_sha256(&canonical_bytes)
    );

    let expanded = canonical
        .replace(r#""-0.00""#, r#""1E+3000""#)
        .replace(r#""0.10""#, r#""1E+3000""#);
    let limits = NsoLimits {
        max_bytes: expanded.len(),
        ..LIMITS
    };
    let error = NsoCodec::decode(expanded.as_bytes(), limits).unwrap_err();
    assert_eq!(error, NsoCodecError::Decimal(DecimalError::EncodingLimit));
    assert!(error.source().is_some());
    assert_eq!(
        NsoCodec::decode(expanded.as_bytes(), LIMITS),
        Err(NsoCodecError::SemanticHashMismatch),
    );
}

#[test]
fn write_approval_ttl_participates_in_semantic_and_artifact_identity() {
    let mut model = ir_1_1();
    let StatementKind::Approval(approval) = &mut model.body[0].kind else {
        panic!("IR 1.1 fixture must start with approval");
    };
    approval.policy.ttl_seconds = Some(1.into());
    let expected = expected("ttl");
    model.semantic_hash = model.compute_semantic_sha256(1024).unwrap();
    assert_eq!(model.semantic_hash.as_str(), expected[0]);
    let encoded = NsoCodec::encode(&model, 1024).unwrap();
    assert_eq!(artifact_sha256(&encoded).as_str(), expected[1]);
    assert_eq!(NsoCodec::decode(&encoded, LIMITS).unwrap(), model);
}
